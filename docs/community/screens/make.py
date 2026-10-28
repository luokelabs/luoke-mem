#!/usr/bin/env python3
"""The website's screenshots of the memory page (docs/community/luoke-mem.md), on a made-up memory: a backend
engineer, all synthetic, built in a temporary folder -- never anyone's real memory. Headless Chrome with a profile
of its own, driven over the DevTools protocol; stdlib only. Writes the PNGs next to this file.

    python3 docs/community/screens/make.py

Retake after the page changes. Needs Google Chrome in /Applications."""
import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

OUT = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(OUT)))
HOME = tempfile.mkdtemp(prefix="luoke-mem-shots-home-")
os.environ["LUOKE_HOME"] = HOME                      # nothing falls back to ~/.luoke
sys.path.insert(0, ROOT)

from luoke_mem import Memory, WholeMemory, ui  # noqa: E402
from luoke_mem import learn as L  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H, SCALE = 1180, 820, 2


def day(n, hh=10, mm=0):
    t = time.time() - 86400 * n
    return time.strftime(f"%Y-%m-%d {hh:02d}:{mm:02d}:00", time.localtime(t))


def build(path):
    """The demo memory: what the mail assistant and the input method wrote, and what was understood."""


    mail, ime, whole = Memory(path, tool="luoke-mail"), Memory(path, tool="luoke-ime"), WholeMemory(path)

    # the mail assistant: what it writes, and what it understood
    sent = [mail.record("user", "statement", f"你发出的邮件：「{s}」", topic="sent-mail", happened_at=day(n), now=day(n, 18))
            for s, n in (("前缀缓存的压测结果", 12), ("本周推理服务的排期", 9), ("4-bit 量化的对比", 6), ("推理服务的显存预算", 2))]
    bill = [mail.record("third_party", "statement", "某云服务发来：「本月账单已生成」", topic="received-mail", happened_at=day(n), now=day(n, 9))
            for n in (40, 10)]
    liked = mail.record("user", "statement", "对「端侧大模型的 KV 缓存复用」：有用", topic="digest-feedback", now=day(3, 21))
    nope = mail.record("user", "statement", "对「交易所新币上线活动」：没兴趣", topic="digest-feedback", now=day(5, 22))
    opened = mail.record("user", "activity", "订阅「本地推理周报」最近打开了 6 封", topic="mail-read", now=day(1, 8))
    for n, k in ((13, 3), (12, 5), (11, 2), (9, 4), (8, 6), (6, 3), (5, 7), (3, 2), (2, 5), (1, 4), (0, 3)):
        for i in range(k):                                         # the day's other mail it handled
            mail.record("system", "activity", "分拣了一封邮件", topic="triage", now=day(n, 9 + i))
    mail.conclude("fact", "在做本地大模型推理服务", sent[:3], topic="工作", why="3 封你发出的邮件里提到", now=day(6, 19))
    mail.conclude("interest", "端侧大模型推理", [liked, opened, sent[2]], why="你收藏和常打开这类内容", now=day(1, 9))
    mail.conclude("dislike", "交易所营销活动", [nope], why="你在精选里点了没兴趣", now=day(5, 22))
    mail.propose("fact", "在某云服务有账户", bill, topic="账户:某云服务", why="这个机构多次来信", now=day(1, 9))
    shop = [mail.record("third_party", "statement", "某书店发来：「你的订单已发货」", topic="received-mail", happened_at=day(n),
                        data={"count": 14, "months": 6, "opened": 9, "last": day(0)[:7]}, now=day(n, 9)) for n in (20, 4)]
    mail.propose("fact", "在某书店有账户", shop, topic="账户:某书店", about="account", why="根据你收到的邮件", now=day(0, 8))

    # the input method: what was typed (synthetic), one of them sensitive
    typing = {
        6: ["luoke-core 那边先合进去", "推理速度还是不够快", "好的收到"],
        5: ["luoke-core 的接口再确认一下", "试试 4-bit 量化", "今天有点累"],
        3: ["oMLX 的前缀缓存再看看", "显存占用降下来了", "luoke-core 发版了", "晚上去做理疗"],
        2: ["前缀缓存命中率高了不少", "推理的时候前缀能复用", "luoke-core 的日志改一下", "嗯嗯"],
        1: ["oMLX 升级以后前缀缓存更稳了", "量化以后显存省了一半", "luoke-core 先这样", "理疗师说要坚持三周", "明天再说吧"],
        0: ["推理服务的压测", "我看看"],
    }
    ids = {}
    for n, lines in typing.items():
        for i, t in enumerate(lines):
            rid = ime.record("user", "statement", t, topic="ime-commit", data={"app": "com.apple.Notes"},
                             sensitive=["health"] if "理疗" in t else None, happened_at=day(n, 10 + i), now=day(n, 10 + i))
            ids.setdefault(t, rid)

    # what luoke-mem drew from the typing (as learn does, without a model)
    stamp = day(0, 9, 30)
    for text in ("luoke-core", "前缀缓存", "oMLX"):
        L._term(whole, text, [], stamp)
    whole.propose("interest", "本地大模型推理优化", [ids["推理的时候前缀能复用"], ids["量化以后显存省了一半"]],
                  why="在 2 天的打字里谈到（跨 3 天就不用问你了）", now=stamp)
    whole.propose("interest", "康复理疗", [ids["晚上去做理疗"], ids["理疗师说要坚持三周"]],
                  why="在 2 天的打字里谈到（跨 3 天就不用问你了）", now=stamp)
    L._put(whole, "typing.last_attempt", time.time() - 3600 * 2)
    L._put(whole, "typing.last_outcome", "归纳了 20 段，得出 3 条常用说法、1 个话题（3 条等你确认）；4 条说法只出现过一次，接着数")
    ime.ask("health", "给常打的药名和医院名加分")                    # a request in 授权

    for m in (mail, ime, whole):
        m.close()


class WS:
    def __init__(self, url):
        hostport, path = url[len("ws://"):].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(4096)
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        assert b" 101 " in head.split(b"\r\n")[0], head
        self.n = 0

    def _read(self, k):
        while len(self.buf) < k:
            more = self.s.recv(1 << 16)
            if not more:
                raise EOFError
            self.buf += more
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        head = bytes([0x81])
        n = len(data)
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                                   else bytes([0x80 | 127]) + struct.pack(">Q", n))
        mask = os.urandom(4)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        parts = []
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            parts.append(self._read(n))
            if b1 & 0x80:
                return json.loads(b"".join(parts))

    def call(self, method, **params):
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params})
        while True:
            m = self.recv()
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})


def main():
    work = tempfile.mkdtemp(prefix="luoke-mem-shots-")
    path = os.path.join(work, "mem.db")
    build(path)
    url = ui.ensure(path)
    profile = os.path.join(work, "chrome-profile")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={profile}",
                               "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                               "--hide-scrollbars", "--lang=zh-CN", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = os.path.join(profile, "DevToolsActivePort")
        for _ in range(100):
            if os.path.exists(port_file) and open(port_file).read().strip():
                break
            time.sleep(0.1)
        port = open(port_file).read().split()[0]
        pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
        ws = WS(next(p for p in pages if p["type"] == "page")["webSocketDebuggerUrl"])
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=SCALE, mobile=False)

        def js(expr, wait=0.8):
            r = ws.call("Runtime.evaluate", expression=expr, awaitPromise=True)
            time.sleep(wait)
            return r

        def go(hash_="", dark=False):
            ws.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])
            ws.call("Page.navigate", url="about:blank")
            time.sleep(0.3)
            ws.call("Page.navigate", url=url + hash_)
            time.sleep(2.0)

        def shot(name, full=False):
            clip = None
            if full:
                m = ws.call("Page.getLayoutMetrics")
                h = max(H, int(m["cssContentSize"]["height"]))
                clip = {"x": 0, "y": 0, "width": W, "height": h, "scale": 1}
            params = {"format": "png", "captureBeyondViewport": bool(full)}
            if clip:
                params["clip"] = clip
            data = ws.call("Page.captureScreenshot", **params)["data"]
            with open(os.path.join(OUT, name + ".png"), "wb") as f:
                f.write(base64.b64decode(data))
            print("saved", name)

        nav = "[...document.querySelectorAll('#nav button')].find(b => b.textContent.startsWith('{}')).click()"
        go()
        shot("overview", full=True)
        go(dark=True)
        shot("overview-dark", full=True)
        go()
        js(nav.format("猜测"))
        js("document.querySelector('#list .note .top').click()", 1.0)
        shot("proposed")
        go("#view=recent")
        shot("recent")
        go("#note=6")
        shot("evidence")
        go("#note=9")
        shot("sensitive")
        go("#note=5")
        js("[...document.querySelectorAll('.acts button')].find(b => b.textContent === '从记忆里忘掉').click()", 0.6)
        shot("forget")
        go()
        js(nav.format("来源"))
        shot("sources")
        go()
        js(nav.format("授权"))
        shot("grants")
    finally:
        chrome.send_signal(signal.SIGTERM)
        chrome.wait(timeout=10)
        state = path + "-ui.json"                          # the demo page's server: stop it by its own pid
        try:
            pid = json.load(open(state)).get("pid")
            if pid:
                os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(HOME, ignore_errors=True)


if __name__ == "__main__":
    main()
