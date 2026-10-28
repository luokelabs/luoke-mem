import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: E402,F401  -- first: a temporary home; the real memory and hub are refused

from luoke_mem import Memory, ui  # noqa: E402
from luoke_mem.__main__ import main as cli  # noqa: E402


class PageTests(unittest.TestCase):
    """The whole memory's page, served in a thread here; ensure() below starts the real thing."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="luoke-mem-ui-")
        self.path = os.path.join(self.dir, "mem.db")
        mail, ime = Memory(self.path, tool="luoke-mail"), Memory(self.path, tool="luoke-ime")
        sent = [mail.record("user", "statement", f"你发出的邮件：「杭州行程 {d}」", topic="sent-mail", happened_at=f"2026-10-{d}")
                for d in ("01", "09")]                       # two mails a week apart: likely (GRADES)
        typed = ime.record("user", "statement", "下周去杭州出差", topic="ime-commit", data={"app": "com.apple.Notes"})
        self.fact = mail.conclude("fact", "常去杭州", sent + [typed])
        theirs = mail.record("third_party", "statement", "某银行发来：「账单」", topic="received-mail")
        mail.propose("fact", "在某银行有账户", [theirs])                 # from others: 观察中, not asked (ADR 0011)
        own = mail.record("user", "statement", "你改过的建议：某商城的邮件改成归档", topic="override")
        self.guess = mail.propose("preference", "某商城的邮件通常要归档", [own])
        for i in range(60):                                  # the input method's own (the user's own words would be
            ime.conclude("term", f"说法{i:02d}",                 # the memory's, top-level ADR 0009), on three days
                         [ime.record("user", "statement", f"说法{i:02d}", topic="ime-commit", data={"app": "com.apple.Notes"},
                                     happened_at=f"2026-10-0{d} 10:00:00") for d in (1, 2, 3)])
        mail.close()
        ime.close()
        self.server = ui.make_server(self.path)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        self.server.mem.close()

    def call(self, path, body=None, token=None, host=None, origin=None):
        headers = {"X-Luoke-Token": self.server.token if token is None else token}
        if host:
            headers["Host"] = host
        if origin:
            headers["Origin"] = origin
        data = None
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as ex:
            with ex:
                return ex.code, ex.headers, ex.read()

    def get(self, path, **kw):
        status, _, body = self.call(path, **kw)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_only_this_machine_with_the_token(self):
        self.assertEqual(self.call("/?t=" + self.server.token)[0], 200)
        self.assertEqual(self.call("/?t=wrong")[0], 200)                  # the page holds nothing: ⌘R opens it
        self.assertEqual(self.call("/")[0], 200)                           # (luoke-mem#5); the data needs the token
        self.assertEqual(self.call("/", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("/api/summary", token="")[0], 403)
        self.assertEqual(self.call("/api/summary", host="evil.example:80")[0], 403)     # DNS rebinding
        self.assertEqual(self.call("/api/forget-all", {"confirm": "清空"}, origin="http://evil.example")[0], 403)
        status, headers, page = self.call("/?t=" + self.server.token)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("螺壳记忆".encode(), page)
        self.assertNotIn(b"{{VERSION}}", page)                              # the version, under the name (#6)
        status, headers, icon = self.call("/icon.png", token="")            # the icon holds nothing either
        self.assertEqual((status, headers["Content-Type"], icon[:8]), (200, "image/png", b"\x89PNG\r\n\x1a\n"))

    def test_only_the_token_keeps_it_up_and_an_open_tab_keeps_it_as_long_as_it_is_open(self):
        self.server.last = 0
        self.call("/api/summary", token="wrong")
        self.call("/?t=wrong")
        self.assertEqual(self.server.last, 0)
        self.get("/api/ping")
        t = self.server.last
        self.assertGreater(t, 0)
        self.assertFalse(self.server.done(now=t + 60))
        self.assertTrue(self.server.done(now=t + ui.IDLE))                # no tab asked: closed
        for minute in range(1, 120):                                     # a tab left open for two hours
            self.server.last = t + 60 * minute                           # in the background, pinging
            self.assertFalse(self.server.done(now=t + 60 * minute + 59))  # keeps it

    def test_the_whole_memory_whoever_wrote_it(self):
        s = self.get("/api/summary")
        self.assertEqual((s["proposed"], s["others"]), (1, 1))           # the habit; the bank one apart
        own = self.get("/api/notes?status=proposed,observed&from=own")["notes"]
        others = self.get("/api/notes?status=proposed,observed&from=others")["notes"]
        self.assertEqual(([n["text"] for n in own], [n["text"] for n in others]), (["某商城的邮件通常要归档"], ["在某银行有账户"]))
        self.assertEqual({k["kind"]: k["active"] for k in s["kinds"]}["term"], 60)
        self.assertEqual([(x["name"], x["records"]) for x in s["sources"]], [("螺壳输入法", 181), ("邮件助手", 4)])
        n = self.get(f"/api/note?id={self.fact}")
        self.assertEqual(sorted(c["name"] for c in n["cites"]), ["螺壳输入法", "邮件助手", "邮件助手"])    # all its evidence
        self.assertIn("下周去杭州出差", [c["text"] for c in n["cites"]])
        self.assertEqual([c["data"] for c in n["cites"] if c["topic"] == "ime-commit"], [{"app": "com.apple.Notes"}])   # shown
        self.assertEqual(sorted(x["text"] for x in self.get("/api/notes?status=proposed")["notes"]), ["在某银行有账户", "某商城的邮件通常要归档"])

    def test_sensitive_context_is_marked_on_the_page(self):
        ime = Memory(self.path, tool="luoke-ime")
        typed = ime.record("user", "statement", "下周三去医院复诊", topic="ime-commit", sensitive=["health"])
        term = ime.conclude("term", "复诊", [typed])
        self.assertEqual(self.get("/api/summary")["asked"], 0)                  # no leave to give (ADR 0013)
        n = self.get(f"/api/note?id={term}")
        self.assertEqual((n["sensitive_labels"], n["cites"][0]["sensitive_labels"]), (["健康"], ["健康"]))
        plain = self.get("/api/notes?kind=term&q=" + urllib.parse.quote("说法01"))["notes"][0]["id"]
        new = self.get("/api/change", body={"op": "revise", "id": plain, "text": "说法01：去医院复诊"})["id"]
        self.assertEqual(self.get(f"/api/note?id={new}")["sensitive_labels"], ["健康"])
        self.assertEqual(self.call("/api/change", {"op": "revise", "id": new, "text": "验证码 123456"})[0], 400)
        ime.close()

    def test_the_overview_shows_what_waits_and_what_was_written(self):
        o = self.get("/api/overview")
        self.assertEqual(len(o["days"]), ui.DAYS)
        self.assertEqual((o["proposed_total"], sorted(n["text"] for n in o["proposed"])), (1, ["某商城的邮件通常要归档"]))
        self.assertEqual({n["confidence"] for n in o["proposed"]}, {"guess"})
        today = {t["name"]: t["counts"][-1] for t in o["tools"]}
        self.assertEqual(today, {"螺壳输入法": 181, "邮件助手": 4})     # 181 typed; 4 mail
        ime = [t for t in o["tools"] if t["tool"] == "luoke-ime"][0]
        page = self.get("/api/records?" + urllib.parse.urlencode({"tool": "luoke-ime", "day": o["days"][-1], "limit": 50}))
        self.assertEqual((len(page["records"]), page["more"]), (50, True))
        rest = self.get("/api/records?" + urllib.parse.urlencode({"tool": "luoke-ime", "day": o["days"][-1], "offset": 50, "limit": 200}))
        self.assertEqual(len(rest["records"]) + 50, ime["total"])
        typed = [r for r in page["records"] + rest["records"] if r["text"] == "下周去杭州出差"][0]
        self.assertEqual((typed["topic"], typed["said"]), ("打的字", "你说的"))
        self.assertNotIn("data", typed)                                 # a tool's own data is for that tool
        self.assertEqual(self.get("/api/records?" + urllib.parse.urlencode({"tool": "luoke-ime", "day": o["days"][0]}))["records"], [])

    def test_many_notes_page_and_search(self):
        page = self.get("/api/notes?kind=term&limit=50")
        self.assertEqual((page["total"], len(page["notes"])), (60, 50))
        self.assertEqual(len(self.get("/api/notes?kind=term&offset=50&limit=50")["notes"]), 10)
        self.assertEqual([n["text"] for n in self.get("/api/notes?kind=term&q=" + urllib.parse.quote("说法05"))["notes"]], ["说法05"])

    def test_change_and_forget_from_the_page(self):
        self.get("/api/change", body={"op": "confirm", "id": self.guess})
        self.assertEqual(self.get(f"/api/note?id={self.guess}")["status"], "active")
        new = self.get("/api/change", body={"op": "revise", "id": self.fact, "text": "常去杭州和上海"})["id"]
        self.assertEqual(self.get(f"/api/note?id={new}")["tool"], "luoke-mem")
        preview = self.get("/api/forget-tool", body={"tool": "luoke-ime", "dry_run": True})
        self.assertEqual((preview["name"], preview["records"], preview["notes"]), ("螺壳输入法", 181, 60))
        self.assertEqual(self.get("/api/forget-tool", body={"tool": "luoke-ime"})["records"], 181)
        self.assertEqual(self.call("/api/forget-tool", {"tool": "luoke-mem", "dry_run": True})[0], 400)   # the user's
        self.assertIsNotNone(self.get(f"/api/note?id={new}"))                     # own words stay
        self.assertEqual(self.call("/api/forget-all", {"confirm": "好"})[0], 400)       # has to type the word
        self.get("/api/forget-all", body={"confirm": ui.CLEAR_WORD})
        self.assertEqual(self.get("/api/summary")["sources"], [])

    def test_a_connection_that_says_nothing_does_not_hold_the_page(self):
        import socket
        hang = socket.create_connection(("127.0.0.1", self.port))      # a browser's preconnect, say
        try:
            t0 = time.time()
            self.assertIn("proposed", self.get("/api/summary"))
            self.assertLess(time.time() - t0, 5)
        finally:
            hang.close()

    def test_what_was_taken_down_unasked_is_listed_with_its_source(self):
        """ADR 0011: 最近记下的, on the overview and in a view of its own; striking one rejects it."""
        mail = Memory(self.path, tool="luoke-mail")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        letters = [mail.record("third_party", "statement", f"某商城来信 {i}", topic="received-mail",
                               data={"count": 9, "months": 4, "opened": 3, "last": now[:7]}) for i in range(2)]
        nid = mail.propose("fact", "用某邮箱服务", letters, about="service", why="根据你收到的邮件", now=now)
        mail.close()
        o = self.get("/api/overview")
        self.assertEqual((o["recent_total"], len(o["recent"]), o["recent"][0]["text"]), (62, 5, "用某邮箱服务"))  # + 60 terms, 常去杭州
        r = self.get("/api/recent")
        self.assertEqual((r["notes"][0]["source"], r["notes"][-1]["source"]), ("根据你收到的邮件", "根据你打的字、根据你发出的邮件和你在邮件助手里的操作"))
        self.get("/api/change", body={"op": "reject", "id": nid})
        self.assertNotIn(nid, [n["id"] for n in self.get("/api/recent")["notes"]])

    def test_facts_on_one_topic_sit_together(self):
        mail = Memory(self.path, tool="luoke-mail")
        r = mail.record("third_party", "statement", "Apple 来信", topic="received-mail")
        ids = [mail._note("fact", t, "inferred", "active", [r], topic=topic) for t, topic in
               (("拥有 Apple ID", "账户:Apple"), ("用某邮箱", "账户:Proton"), ("拥有 Apple ID 和 iCloud", "账户:Apple"))]
        mail.close()
        notes = self.get("/api/notes?kind=fact")["notes"]
        mine = [n for n in notes if n["id"] in ids]
        self.assertEqual([(n["id"], n["alike"]) for n in mine], [(ids[2], 1), (ids[0], 1), (ids[1], 0)])

    def test_every_topic_the_memory_and_its_tools_write_has_a_name(self):
        for topic in ("note-decision", "note-later", "ask-answer", "ask-dismissed", "mail-cleared"):   # mail-cleared:
            self.assertIn(topic, ui.TOPICS)                       # luoke-mail's bulk clears (2026-10-16)

    def test_evidence_says_which_tool_the_user_said_it_in(self):
        mail = Memory(self.path, tool="luoke-mail")
        mail.confirm(self.guess)                                      # said in the mail assistant: the memory's
        mail.close()
        said = [c for c in self.get(f"/api/note?id={self.guess}")["cites"] if c["tool"] == "luoke-mem"]
        self.assertEqual([(c["name"], c["via"], c["text"]) for c in said], [("螺壳记忆", "邮件助手", "确认：某商城的邮件通常要归档")])
        self.assertEqual(said[0]["data"], {"via": "luoke-mail"})            # shown with the evidence (ADR 0013)


class EnsureTests(unittest.TestCase):
    """ensure() starts a server on its own, finds it again, and the server leaves when nobody looks."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="luoke-mem-ensure-")
        self.path = os.path.join(self.dir, "mem.db")
        Memory(self.path, tool="luoke-mail").close()

    def tearDown(self):
        st = ui._read_state(self.path)
        if st:
            try:
                os.kill(st["pid"], signal.SIGTERM)
            except OSError:
                pass

    def test_one_server_per_memory_file(self):
        url = ui.ensure(self.path)
        self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/\?t=[\w-]+$")
        self.assertEqual(ui.ensure(self.path), url)
        with urllib.request.urlopen(url, timeout=5) as r:
            self.assertIn("螺壳记忆", r.read().decode())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli(["--path", self.path, "ui"]), 0)
        self.assertEqual(out.getvalue().strip(), url)
        self.assertEqual(os.stat(ui._state_path(self.path)).st_mode & 0o777, 0o600)

    def test_opening_the_app_shows_the_page_even_before_any_memory(self):
        fresh = os.path.join(self.dir, "fresh", "mem.db")
        opened, said = [], []
        self.assertEqual(ui.app(fresh, open_url=opened.append, alert=said.append), 0)
        self.assertTrue(os.path.exists(fresh))
        self.assertEqual(said, [])
        self.assertRegex(opened[0], r"^http://127\.0\.0\.1:\d+/\?t=")
        os.kill(ui._read_state(fresh)["pid"], signal.SIGTERM)

    def test_the_app_and_the_command_open_at_one_note(self):
        opened = []
        self.assertEqual(ui.app(self.path, open_url=opened.append, alert=lambda m: None, note=42), 0)
        self.assertEqual(ui.app(self.path, open_url=opened.append, alert=lambda m: None, view="recent"), 0)
        self.assertTrue(opened[-1].endswith("#view=recent"))
        self.assertRegex(opened[0], r"^http://127\.0\.0\.1:\d+/\?t=[\w-]+#note=42$")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli(["--path", self.path, "ui", "--note", "7"]), 0)
        self.assertTrue(out.getvalue().strip().endswith("#note=7"))

    def test_the_app_says_what_is_wrong_in_a_dialog(self):
        import sqlite3
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA user_version = 9000")               # written by a far newer luoke-mem
        db.commit()
        db.close()
        opened, said = [], []
        self.assertEqual(ui.app(self.path, open_url=opened.append, alert=said.append), 1)
        self.assertEqual(opened, [])
        self.assertIn("更新", said[0])

    def test_the_server_writes_no_bytecode(self):
        """Inside the signed 螺壳记忆.app a __pycache__ breaks the seal (luoke-core V1 §8), whoever starts it."""
        self.assertEqual(ui._env()["PYTHONDONTWRITEBYTECODE"], "1")
        with mock.patch("subprocess.Popen", side_effect=OSError("stop here")) as popen:
            with self.assertRaises(OSError):
                ui.ensure(self.path)
        self.assertEqual(popen.call_args[0][0][1], "-B")

    def test_no_memory_no_page(self):
        with self.assertRaises(FileNotFoundError):
            ui.ensure(os.path.join(self.dir, "none.db"))

    def test_the_server_leaves_when_idle(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        done = subprocess.run([sys.executable, "-m", "luoke_mem", "--path", self.path, "serve", "--idle", "1"],
                              cwd=here, capture_output=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        st = ui._read_state(self.path)
        self.assertTrue(ui._answers(st))
        deadline = time.time() + 15
        while ui._read_state(self.path) and time.time() < deadline:
            time.sleep(0.2)
        self.assertIsNone(ui._read_state(self.path))
        self.assertFalse(ui._answers(st))

    def test_a_stopped_server_tidies_up_and_a_new_one_starts(self):
        url = ui.ensure(self.path)
        os.kill(ui._read_state(self.path)["pid"], signal.SIGTERM)
        deadline = time.time() + 10
        while ui._read_state(self.path) and time.time() < deadline:
            time.sleep(0.1)
        self.assertIsNone(ui._read_state(self.path))
        self.assertNotEqual(ui.ensure(self.path), url)


if __name__ == "__main__":
    unittest.main()
