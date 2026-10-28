"""The whole memory's page (top-level ADR 0002, 0003): every kind of understanding, each with all its evidence
whoever wrote it, forgetting by source, clearing everything. A small server on 127.0.0.1 with a random port and
token, checking the Host header; it runs on its own (not as a child of whoever opened it). An open tab asks every
minute, in the background too, so the server stays as long as a tab is open; IDLE seconds after the last tab
closed it leaves. Whoever must replace it (the hub, before an update) stops it with SIGTERM: its pid is in
<file>-ui.json, and an open tab then says to open the page again (no more "expired").

    from luoke_mem import ui
    url = ui.ensure(path)        # start or find the page for this memory file; http://127.0.0.1:<port>/?t=<token>

One server per memory file: its pid, port and token are kept next to the file in <file>-ui.json (0600)."""
from __future__ import annotations

import fcntl
import hmac
import http.server
import json
import os
import secrets
import select
import signal
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser

from . import __version__
from .memory import CATCH_ALL, KEEPER, KIND_NAMES as KINDS, NOTE_KINDS, STATUSES, TOOL_NAMES as TOOLS, TooNew, WholeMemory, default_path
from .sensitive import SENSITIVE_KINDS, never_kept, sensitive_kinds

IDLE = 600                     # seconds without a request (no tab open any more) before the server leaves
START = 5.0                    # how long ensure() waits for a new server
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
ICON = os.path.join(os.path.dirname(PAGE), "icon.png")
CLEAR_WORD = "清空"            # what the user types to clear the whole memory
DAYS = 14                      # how far back the overview shows what was written
RECENT_DAYS = 30               # how far back 最近记下的 goes (ADR 0011)
# What a record is, in words: topics belong to the tools (ARCHITECTURE §5); unknown ones show as they are.
TOPICS = {"ime-commit": "打的字", "ime-correction": "改选和删改", "ime-forget": "忘掉候选", "sent-mail": "发出的邮件",
          "received-mail": "收到的邮件", "mail-read": "阅读", "digest-feedback": "精选里的反馈", "digest-open": "精选里打开的",
          "override": "改过的建议", "own-site": "网站文章", "own-label": "邮箱标签", "mail-cleared": "清掉的邮件",
          "note-decision": "确认、否定、修改", "note-later": "以后再说", "ask-answer": "回答的问题", "ask-dismissed": "不用再问的"}
SAID = {("user", "statement"): "你说的", ("user", "activity"): "你做的", ("user", "receipt"): "你处理的",
        ("third_party", "statement"): "别人说的", ("system", "statement"): "系统记的"}
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


def _state_path(path: str) -> str:
    return path + "-ui.json"


def _read_state(path: str) -> dict | None:
    try:
        with open(_state_path(path)) as f:
            st = json.load(f)
        return st if isinstance(st, dict) and {"pid", "port", "token"} <= set(st) else None
    except (OSError, ValueError):
        return None


def _url(st: dict) -> str:
    return f"http://127.0.0.1:{st['port']}/?t={st['token']}"


def _answers(st: dict | None) -> bool:
    if not st:
        return False
    req = urllib.request.Request(f"http://127.0.0.1:{st['port']}/api/ping", headers={"X-Luoke-Token": st["token"]})
    try:
        with urllib.request.urlopen(req, timeout=1) as r:
            return r.status == 200
    except OSError:
        return False


def _env() -> dict:
    """A child Python that finds this luoke_mem, even when the caller put it on sys.path by hand (luoke-mail
    loads the luoke-mem folder next to it; its login item's PYTHONPATH does not name it), and writes no
    __pycache__: inside the signed 螺壳记忆.app that would break the seal (luoke-core V1 §8)."""
    env = dict(os.environ)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env["PYTHONPATH"] = here + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def ensure(path: str | None = None) -> str:
    """Start or find the whole memory's page for this memory file; returns its address with the token.
    Raises FileNotFoundError when there is no memory yet, TooNew when it is too new for this luoke-mem, and
    RuntimeError when the page could not be started."""
    path = os.path.abspath(path or default_path())
    if not os.path.exists(path):
        raise FileNotFoundError(f"no memory at {path}")
    WholeMemory(path).close()                  # a memory too new for this luoke-mem raises TooNew here, as itself
    fd = os.open(_state_path(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)      # two tools opening the page at once get the same server
        st = _read_state(path)
        if _answers(st):
            return _url(st)
        proc = subprocess.Popen([sys.executable, "-B", "-m", "luoke_mem", "--path", path, "serve"], env=_env(), cwd="/",
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            _, err = proc.communicate(timeout=START)    # it starts the server on its own and leaves
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise RuntimeError("the memory page did not start in time") from None
        if proc.returncode != 0:
            raise RuntimeError(err.decode("utf-8", "replace").strip() or "the memory page could not start")
        st = _read_state(path)
        deadline = time.time() + START
        while not _answers(st):
            if time.time() > deadline:
                raise RuntimeError("the memory page started but does not answer")
            time.sleep(0.05)
            st = _read_state(path)
        return _url(st)


def _alert(message: str) -> None:
    """Opened from Finder there is no terminal: say it in a dialog. The text goes in as an argument, never as
    AppleScript source."""
    subprocess.run(["/usr/bin/osascript", "-e", "on run argv", "-e",
                    'display alert "螺壳记忆打不开" message (item 1 of argv) as critical', "-e", "end run", message],
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def app(path: str | None = None, open_url=webbrowser.open, alert=_alert, note: int | None = None,
        view: str | None = None) -> int:
    """What opening 螺壳记忆.app does (`python -m luoke_mem app`): start or find the page and show it in the
    browser. On a machine where no tool has written anything yet the memory starts empty, rather than the app
    refusing to open. Problems go to a dialog."""
    path = os.path.abspath(path or default_path())
    try:
        if not os.path.exists(path):
            WholeMemory(path).close()
        url = ensure(path)
    except TooNew:
        alert("这份记忆是更新版本的螺壳记忆写的，请更新螺壳记忆。")
        return 1
    except (OSError, RuntimeError) as ex:
        alert(f"整份记忆的页面没能打开：{ex}")
        return 1
    open_url(url + _at(note, view))           # at one note (luoke-agent's 看依据), or at 最近记下的 (its summary card)
    return 0


def _at(note: int | None = None, view: str | None = None) -> str:
    if note:
        return f"#note={int(note)}"
    return "#view=recent" if view == "recent" else ""


# ---- the server ---------------------------------------------------------------------------------------------
def _labelled(tool: str) -> dict:
    return {"tool": tool, "name": TOOLS.get(tool, tool)}


def _topic(topic: str | None) -> str:
    if topic and topic.startswith("note:"):
        return "你写的" + KINDS.get(topic[5:], topic[5:])
    return TOPICS.get(topic or "", topic or "")


def _together(notes: list) -> list:
    """Facts on the same topic (账户:Apple) next to each other, where the first of them is, each saying how many
    others there are (`alike`): they may be one thing said several times. Facts are not folded on their own (two
    accounts at one place may be two); the user sees them side by side and keeps what is right."""
    from .memory import _inst, _same_fact
    facts = [n for n in notes if n["kind"] == "fact"]
    for n in facts:                              # 其他 beside the topic it is really about (luoke-mem#2)
        if _inst(n.get("topic")):                # one institution, whatever category it was filed under
            n["_group"] = "inst:" + _inst(n["topic"])
        elif not n.get("topic") or str(n["topic"]).split(":")[0] in CATCH_ALL:
            other = next((m for m in facts if m is not n and m.get("topic") and str(m["topic"]).split(":")[0] not in CATCH_ALL
                          and _same_fact(m["topic"], m["text"], n.get("topic"), n["text"])), None)
            n["_group"] = ("inst:" + _inst(other["topic"]) if _inst(other["topic"]) else other["topic"]) if other else None
        else:
            n["_group"] = n["topic"]
    by: dict = {}
    for n in facts:
        if n.get("_group"):
            by.setdefault(n["_group"], []).append(n)
    out, seen = [], set()
    for n in notes:
        if n["id"] in seen:
            continue
        group = by.get(n.get("_group")) if n["kind"] == "fact" else None
        for m in group or [n]:
            m["alike"] = len(group) - 1 if group else 0
            out.append(m)
            seen.add(m["id"])
    return out


def _sensitive(row: dict) -> list:
    """The kinds of sensitive context in a note or record, by name: shown here and nowhere else (ADR 0004)."""
    return [SENSITIVE_KINDS.get(k, k) for k in (row.get("sensitive") or "").split(",") if k]


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "luoke-mem"
    sys_version = ""
    timeout = 10                              # a connection that says nothing is let go

    def log_message(self, *args) -> None:     # nothing about what the user looks at goes anywhere
        pass

    # -- guards: only this machine, only with the token ----------------------------------------------------
    def _host_ok(self) -> bool:
        port = self.server.server_port
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given.encode(), self.server.token.encode())

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _refuse(self, status: int = 403) -> None:
        self._json({"error": "forbidden" if status == 403 else "not found"}, status)

    def _seen(self, path: str) -> None:
        """Only someone with the token keeps the server up: an open tab's pings do, for as long as it is open."""
        self.server.last = time.time()

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        if not self._host_ok():
            return self._refuse()
        if url.path == "/":                   # the page itself holds nothing: a reload (⌘R) has no ?t= any more --
            if self._token_ok(q.get("t")):    # the page took it into the tab's sessionStorage -- and must still open
                self._seen(url.path)          # (luoke-mem#5); everything it shows comes from /api/, token and all
            with open(PAGE, "rb") as f:          # the version shows under the name in the sidebar
                return self._send(200, f.read().replace(b"{{VERSION}}", __version__.encode()), "text/html; charset=utf-8")
        if url.path == "/icon.png":           # the sidebar's and the tab's icon: nothing private in it
            with open(ICON, "rb") as f:
                return self._send(200, f.read(), "image/png")
        if not url.path.startswith("/api/") or not self._token_ok(self.headers.get("X-Luoke-Token")):
            return self._refuse()
        self._seen(url.path)
        try:
            route = {"/api/ping": lambda: {"ok": True}, "/api/summary": self._summary, "/api/grants": self._grants,
                     "/api/overview": self._overview, "/api/records": lambda: self._records(q),
                     "/api/notes": lambda: self._notes(q), "/api/note": lambda: self._note(q),
                     "/api/recent": lambda: self._recent(q)}.get(url.path)
            if not route:
                return self._refuse(404)
            self._json(route())
        except (KeyError, ValueError) as ex:
            self._json({"error": str(ex)}, 400)

    def do_POST(self) -> None:  # noqa: N802
        origin = self.headers.get("Origin")
        port = self.server.server_port
        if (not self._host_ok() or not self._token_ok(self.headers.get("X-Luoke-Token"))
                or (origin and origin not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"))):
            return self._refuse()
        self._seen(urllib.parse.urlsplit(self.path).path)
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536)) or b"{}")
            route = {"/api/change": self._change, "/api/forget-tool": self._forget_tool,
                     "/api/forget-all": self._forget_all, "/api/grant": self._grant}.get(urllib.parse.urlsplit(self.path).path)
            if not route:
                return self._refuse(404)
            self._json(route(body))
        except (KeyError, TypeError, ValueError) as ex:
            self._json({"error": str(ex)}, 400)

    # -- what the page reads -------------------------------------------------------------------------------
    @property
    def mem(self) -> WholeMemory:
        return self.server.mem

    def _summary(self) -> dict:
        counts = self.mem.counts()
        kinds = list(NOTE_KINDS) + sorted(k for k in counts if k not in NOTE_KINDS)   # unknown kinds by name
        return {"path": self.mem.path, "clear_word": CLEAR_WORD,
                "kinds": [{"kind": k, "label": KINDS.get(k, k), **{s: (counts.get(k) or {}).get(s, 0) for s in STATUSES}}
                          for k in kinds],
                "proposed": sum((c or {}).get("proposed", 0) + (c or {}).get("observed", 0) for c in counts.values())
                - len(self._others()),                                                                          # guesses
                "others": len(self._others()),          # read off others' letters, not seen enough (shown apart)
                "past": sum((c or {}).get("past", 0) for c in counts.values()),                                        # 以前的事
                "sources": [{**s, **_labelled(s["tool"])} for s in self.mem.sources()],
                "asked": 0}                     # no leave to give any more (ADR 0013); kept for older pages

    def _recent(self, q: dict) -> dict:
        """最近记下的 (ADR 0011): taken down without asking in the last RECENT_DAYS, with where it comes from."""
        since = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400 * RECENT_DAYS))
        notes = self.mem.recent(since=since, limit=int(q.get("limit") or 200))
        with self.mem._lock:
            cited = dict(self.mem.db.execute("SELECT note_id, COUNT(*) FROM cites GROUP BY note_id").fetchall())
        return {"days": RECENT_DAYS, "notes": [{**n, **_labelled(n["tool"]), "kind_label": KINDS.get(n["kind"], n["kind"]),
                                                "sensitive_labels": _sensitive(n), "cites": cited.get(n["id"], 0)} for n in notes]}

    def _others(self) -> set:
        """Guesses read off others' letters (facts from what others said, not yet seen enough): never asked, never
        used, waiting for more evidence -- kept apart from the guesses the user may settle."""
        with self.mem._lock:
            return {r[0] for r in self.mem.db.execute(
                "SELECT n.id FROM notes n WHERE n.kind='fact' AND n.status IN ('proposed', 'observed') AND EXISTS"
                " (SELECT 1 FROM cites c JOIN records r ON r.id=c.record_id WHERE c.note_id=n.id AND r.speaker='third_party')")}

    def _notes(self, q: dict) -> dict:
        status = [s for s in (q.get("status") or "active").split(",") if s in STATUSES]
        notes = self.mem.notes(kinds=[q["kind"]] if q.get("kind") else None, status=status)
        if q.get("from") in ("own", "others"):        # the guesses the user may settle, or those read off others' letters
            others = self._others()
            notes = [n for n in notes if (n["id"] in others) == (q["from"] == "others")]
        words = (q.get("q") or "").casefold().split()
        if words:
            notes = [n for n in notes if all(w in n["text"].casefold() for w in words)]
        offset, limit = max(0, int(q.get("offset") or 0)), min(200, max(1, int(q.get("limit") or 50)))
        with self.mem._lock:
            cited = dict(self.mem.db.execute("SELECT note_id, COUNT(*) FROM cites GROUP BY note_id").fetchall())
        notes = _together(notes)
        page = [{**n, **_labelled(n["tool"]), "kind_label": KINDS.get(n["kind"], n["kind"]), "cites": cited.get(n["id"], 0),
                 "sensitive_labels": _sensitive(n)} for n in notes[offset:offset + limit]]
        return {"total": len(notes), "offset": offset, "notes": page}

    def _note(self, q: dict) -> dict:
        n = self.mem.note(int(q["id"]))
        if not n:
            raise ValueError("这条记忆已经不在了")
        n.update(_labelled(n["tool"]))
        n["kind_label"] = KINDS.get(n["kind"], n["kind"])
        n["sensitive_labels"] = _sensitive(n)
        n["merged"] = self.mem.merged(n["id"])    # written again in other words, folded in: the user can part them
        for c in n["cites"]:
            c.update(_labelled(c["tool"]))
            c["sensitive_labels"] = _sensitive(c)
            data = c.get("data")                  # shown with the evidence (ADR 0013); which tool the user said it in
            via = data.get("via") if c["tool"] == "luoke-mem" and isinstance(data, dict) else None
            c["via"] = TOOLS.get(via, via) if via else None
        return n

    def _overview(self) -> dict:
        """What the page opens on: the guesses waiting for the user, and that collecting is happening (how much
        each tool wrote each day of the last DAYS), before any understanding is drawn from it."""
        today = time.time()
        days = [time.strftime("%Y-%m-%d", time.localtime(today - 86400 * i)) for i in range(DAYS - 1, -1, -1)]
        counts: dict = {}
        for row in self.mem.daily(days[0]):
            counts.setdefault(row["tool"], {})[row["day"]] = row["count"]
        proposed = self._notes({"status": "proposed,observed", "limit": "5", "from": "own"})   # guesses (ADR 0015)
        with self.mem._lock:
            kept = dict(self.mem.db.execute("SELECT name, value FROM learning WHERE name IN"
                                            " ('typing.last_attempt', 'typing.last_outcome', 'typing.last_run')").fetchall())
        at = float(kept.get("typing.last_attempt") or 0)
        learning = {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else None,
                    "outcome": kept.get("typing.last_outcome")}
        recent = self._recent({"limit": "200"})["notes"]
        return {"days": days, "proposed": proposed["notes"], "proposed_total": proposed["total"], "learning": learning,
                "recent": recent[:5], "recent_total": len(recent),
                "tools": [{**_labelled(t), "counts": [c.get(d, 0) for d in days], "total": sum(c.values())}
                          for t, c in sorted(counts.items())],
                "kinds": self._summary()["kinds"]}

    def _records(self, q: dict) -> dict:
        """One tool's records of one day, newest first, in words; a tool's own data is for that tool."""
        day = q["day"]
        start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        until = time.strftime("%Y-%m-%d", time.localtime(start + 86400 + 3600))   # the next day, DST or not
        offset, limit = max(0, int(q.get("offset") or 0)), min(200, max(1, int(q.get("limit") or 50)))
        rows = self.mem.records(tool=q["tool"], since=day, until=until, limit=limit, offset=offset)
        return {"records": [{"id": r["id"], "text": r["text"], "topic": _topic(r["topic"]),
                             "said": SAID.get((r["speaker"], r["kind"]), r["speaker"]),
                             "happened_at": r["happened_at"], "recorded_at": r["recorded_at"],
                             "sensitive_labels": _sensitive(r)} for r in rows],
                "more": len(rows) == limit}

    def _grants(self) -> dict:
        """Which tool may read which kind of sensitive context, and what is waiting for the user."""
        return {"kinds": [{"kind": k, "label": v} for k, v in SENSITIVE_KINDS.items()],
                "grants": [{**g, **_labelled(g["tool"]), "label": SENSITIVE_KINDS.get(g["category"], g["category"])}
                           for g in self.mem.grants()]}

    # -- what the page changes -----------------------------------------------------------------------------
    def _change(self, body: dict) -> dict:
        op, nid = body["op"], int(body["id"])
        if op == "split":
            self.mem.split(nid)
            return {"ok": True}
        if op in ("confirm", "reject", "forget"):
            getattr(self.mem, op)(nid)
            return {"ok": True}
        if op == "revise":
            text = " ".join(str(body.get("text") or "").split())[:200]
            if not text:
                raise ValueError("请填写内容")
            if never_kept(text):               # secrets and credentials are not kept, not even when typed here
                raise ValueError("密码、验证码、密钥、完整卡号和证件号这类不记在记忆里")
            return {"ok": True, "id": self.mem.revise(nid, text, sensitive=sensitive_kinds(text))}
        raise ValueError(f"unknown change: {op}")

    def _grant(self, body: dict) -> dict:
        (self.mem.grant if body.get("allow") else self.mem.deny)(body["tool"], body["kind"])
        return {"ok": True}

    def _forget_tool(self, body: dict) -> dict:
        if body["tool"] == KEEPER:                # the user's own words and what the memory drew: not a tool to drop
            raise ValueError("螺壳记忆里是你说的话和它归纳的理解：在每一条上删，或者清空整份记忆")
        return {**_labelled(body["tool"]), **self.mem.forget_tool(body["tool"], dry_run=bool(body.get("dry_run")))}

    def _forget_all(self, body: dict) -> dict:
        if body.get("confirm") != CLEAR_WORD:
            raise ValueError(f"要清空整份记忆，请输入「{CLEAR_WORD}」")
        self.mem.forget_all()
        return {"ok": True}


class _Server(http.server.ThreadingHTTPServer):    # one stuck connection does not hold the others
    def __init__(self, path: str):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.path, self.token, self.mem = path, secrets.token_urlsafe(24), None
        self.last = time.time()

    def done(self, idle: float = IDLE, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now - self.last >= idle

    def handle_error(self, request, client_address) -> None:   # a dropped connection is not news
        pass


def make_server(path: str) -> _Server:
    """A server for this memory file, bound but not yet serving (the tests run it in a thread)."""
    server = _Server(os.path.abspath(path))
    server.mem = WholeMemory(server.path)
    return server


def serve(path: str, idle: float = IDLE) -> None:
    """Run by ensure() as `python3 -m luoke_mem serve`: start the server as a process of its own (a fresh one:
    the system SQLite crashes in a child that was forked after it was used and did not exec), wait until it
    says it is ready, write <file>-ui.json, and leave. Ends the process: 0 when the server runs, 1 if not."""
    path = os.path.abspath(path)
    r, w = os.pipe()
    proc = subprocess.Popen([sys.executable, "-B", "-m", "luoke_mem", "--path", path, "run", "--ready", str(w),
                             "--idle", str(idle)], pass_fds=(w,), env=_env(), cwd="/",
                            start_new_session=True,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(w)
    said, deadline = b"", time.time() + START
    while time.time() < deadline:
        if not select.select([r], [], [], max(0.0, deadline - time.time()))[0]:
            break
        chunk = os.read(r, 4096)
        if not chunk:
            break
        said += chunk
    os.close(r)
    try:
        ready = json.loads(said or b"{}")
    except ValueError:
        ready = {}
    if "port" not in ready:
        if proc.poll() is None:
            proc.kill()
        sys.stderr.write((ready.get("error") or "the memory page could not start") + "\n")
        sys.stderr.flush()
        os._exit(1)
    with os.fdopen(os.open(_state_path(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600), "w") as f:
        json.dump({"pid": proc.pid, "port": ready["port"], "token": ready["token"]}, f)
    os._exit(0)                                # the server lives on; nothing here waits for it


def run(path: str, ready_fd: int, idle: float = IDLE) -> int:
    """The server itself (`python3 -m luoke_mem run`): open the memory, bind, say so on ready_fd, and answer
    until nobody has asked for `idle` seconds (no tab open), or it is stopped with SIGTERM."""
    path = os.path.abspath(path)
    with os.fdopen(ready_fd, "w") as ready:
        try:
            server = make_server(path)
        except (OSError, RuntimeError) as ex:  # a memory this luoke-mem cannot open, say
            ready.write(json.dumps({"error": str(ex)}))
            return 1
        ready.write(json.dumps({"port": server.server_port, "token": server.token}))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # stopped from outside: still tidy up below
    try:
        server.timeout = 5
        while not server.done(idle):
            server.handle_request()
    finally:
        server.server_close()
        server.mem.close()
        with os.fdopen(os.open(_state_path(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)      # as ensure() does: not between its read and a new server's write
            if (_read_state(path) or {}).get("pid") == os.getpid():
                lock.truncate(0)
    return 0
