"""螺壳主控 (luoke-core): how luoke-mem asks for a model when it forms understanding (top-level ADR 0005).

The contract is luoke-core's docs/V1.md §2 (contract 1): HTTP/1.1 over a Unix socket only this macOS account can
reach; every request says who asks (`X-Luoke-Accessory: luoke-mem/<version>`) and for which task
(`luoke-mem.<task>`). luoke-mem decides the messages, the output format and how many items a request carries;
the hub decides the model, the output budget and sampling, and refuses what is too long. Generation always
streams (SSE). What is sent lives only in memory, here and in the hub.

Forming understanding is background work: when the hub says busy, paused, interrupted (the user came back, on
battery, ...) it stops at once (`Stop`) and comes back another time; it never waits in place."""
from __future__ import annotations

import http.client
import json
import os
import socket

ACCESSORY = "luoke-mem"
DEFAULT_SOCKET = os.path.expanduser("~/Library/Application Support/luoke-core/run/hub.sock")
LAB_SOCKET = os.path.expanduser("~/Library/Application Support/luoke-core-lab/run/hub.sock")
LATER = ("busy", "paused", "interrupted", "not_ready", "needs_user")   # not now: stop, come back later


def socket_path(explicit: str | None = None) -> str | None:
    """Where the hub listens. A socket named (here, or `LUOKE_HUB_SOCKET`) is that one or none -- never the
    hub in daily use instead. Nothing named: the released hub's place
    only. A lab build of the hub is used only when named (the evaluation does), never fallen back to: what is typed
    goes to a development instance only on purpose (self-check)."""
    named = explicit or os.environ.get("LUOKE_HUB_SOCKET")
    if os.environ.get("LUOKE_MEM_TEST"):           # under test: only a socket the test made itself
        theirs = {os.path.realpath(DEFAULT_SOCKET), os.path.realpath(LAB_SOCKET)}
        return named if named and os.path.realpath(named) not in theirs and os.path.exists(named) else None
    if named:
        return named if os.path.exists(named) else None
    return DEFAULT_SOCKET if os.path.exists(DEFAULT_SOCKET) else None


class HubError(Exception):
    """The hub could not or would not do it: `code` is its reason code ("unreachable" when there is no hub)."""

    def __init__(self, message: str, code: str = "unreachable", retry_after: float | None = None, reason: str = ""):
        super().__init__(message)
        self.code, self.retry_after, self.reason = code, retry_after, reason


class Stop(HubError):
    """Not now (busy, paused, the user came back...): background work stops and comes back later."""


def _refused(code: str, data: dict) -> HubError:
    retry = data.get("retry_after")
    retry = float(retry) if isinstance(retry, (int, float)) else None
    cls = Stop if code in LATER else HubError
    return cls(str(data.get("message") or code), code, retry, str(data.get("reason") or ""))


class _Unix(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = path

    def connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.unix_path)
        except OSError:
            s.close()
            raise
        self.sock = s


class Hub:
    def __init__(self, path: str | None = None, version: str = ""):
        self.path = socket_path(path)         # named or not: under test never the real or the lab hub
        if not version:
            from . import __version__ as version
        self.who = f"{ACCESSORY}/{version}"
        self.model = ""                     # the combination the hub answered with (its chunks say), for evidence

    def _conn(self, timeout: float) -> _Unix:
        if not self.path:
            raise HubError("螺壳没有运行")
        return _Unix(self.path, timeout)

    def tasks(self) -> dict:
        """`GET /tasks`: this accessory's tasks, their status and parameters."""
        c = self._conn(10)
        try:
            c.request("GET", "/tasks", headers={"X-Luoke-Accessory": self.who})
            r = c.getresponse()
            raw = r.read()
        except (OSError, http.client.HTTPException) as ex:
            raise HubError(f"螺壳没有回应：{ex}") from ex
        finally:
            c.close()
        data = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        if r.status >= 400:
            raise _refused(str(data.get("code") or r.status), data)
        return data

    def task(self, task: str) -> dict:
        return (self.tasks().get("tasks") or {}).get(f"{ACCESSORY}.{task}") or {}

    def chat(self, task: str, messages: list[dict], schema: dict, *, items: int = 1, timeout: float = 600) -> str:
        """Background generation; the answer's text (JSON as `schema` asks). Stop when not now, HubError else."""
        body = {"messages": messages, "stream": True, "luoke_items": max(1, int(items)),
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": task.replace("-", "_"), "schema": schema}}}
        c = self._conn(timeout)
        try:
            try:
                c.request("POST", f"/tasks/{ACCESSORY}.{task}/v1/chat/completions",
                          body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"X-Luoke-Accessory": self.who, "X-Luoke-Priority": "background",
                                   "Content-Type": "application/json"})
                r = c.getresponse()
            except (OSError, http.client.HTTPException) as ex:
                raise HubError(f"螺壳没有回应：{ex}") from ex
            if r.status != 200:
                raw = r.read()
                try:
                    data = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    data = {}
                raise _refused(str(data.get("code") or r.status), data)
            parts, event = [], None
            try:
                while True:
                    raw = r.readline()
                    if not raw:
                        raise HubError("回答没有完整传回", "engine_failed")
                    line = raw.decode("utf-8", "replace").rstrip("\r\n")
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        data = line[6:]
                        if event == "luoke.error":       # what came so far is thrown away
                            payload = json.loads(data)
                            raise _refused(str(payload.get("code") or "engine_failed"), payload)
                        if event:                       # luoke.progress
                            event = None
                            continue
                        if data.strip() == "[DONE]":
                            break
                        chunk = json.loads(data)
                        self.model = chunk.get("model") or self.model
                        for ch in chunk.get("choices") or []:
                            piece = (ch.get("delta") or {}).get("content")
                            if piece:
                                parts.append(piece)
                    elif not line:
                        event = None
            except (OSError, http.client.HTTPException, ValueError) as ex:
                raise HubError(f"回答没有完整传回：{ex}", "engine_failed") from ex
        finally:
            c.close()
        return "".join(parts).strip()
