"""Forming understanding from typing (top-level ADR 0005), against a stand-in hub on a socket of its own: the
real hub is never asked."""
import http.server
import io
import json
import os
import re
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: E402,F401  -- first: a temporary home; the real memory and hub are refused

from luoke_mem import Memory, WholeMemory, learn as L  # noqa: E402
from luoke_mem.hub import Hub, HubError  # noqa: E402

NOW = time.mktime((2026, 10, 14, 22, 0, 0, 0, 0, -1))
LATER = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW + 8 * 86400))   # asks are read then, not by the clock:
                                                                               # they expire 14 days after NOW


class FakeHub(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path, answer):
        self.answer, self.requests, self.refuse = answer, [], {}       # refuse: request number -> reason code
        self.status = "available"
        super().__init__(path, _Handler)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def address_string(self):
        return "unix"

    def do_GET(self):  # noqa: N802
        tasks = {} if self.server.status == "absent" else {"luoke-mem.typing": {"status": self.server.status, "params": {"batch": 40}}}
        if getattr(self.server, "topics", None):
            tasks["luoke-mem.topics"] = {"status": "available", "params": {"batch": getattr(self.server, "topics_batch", 40)}}
        self._json(200, {"contract": 1, "tasks": tasks})

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
        code = srv.refuse.get(len(srv.requests))
        if code:
            code, reason = code if isinstance(code, tuple) else (code, "")
            return self._json(503, {"code": code, "message": "not now", "retry_after": 60, "reason": reason})
        answer = srv.topics if "luoke-mem.topics" in self.path else srv.answer
        said = answer(body["messages"][1]["content"])
        content = said if isinstance(said, str) else json.dumps(said, ensure_ascii=False)   # a str: cut off, as is
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for piece in (content[:len(content) // 2], content[len(content) // 2:]):
            chunk = {"choices": [{"delta": {"content": piece}}]}
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(b"event: luoke.progress\ndata: {\"stage\": \"running\"}\n\ndata: [DONE]\n\n")


def model(words=("行云", "星火", "先快后好"), topic=("本地大模型推理", "推理")):
    """A stand-in model: names the planted words it sees, and the topic wherever its keyword shows."""
    def answer(text):
        lines = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^(\d+)\. (.*)$", text, re.M)]
        terms = [{"text": w, "evidence": [n for n, t in lines if w in t]} for w in words if any(w in t for _, t in lines)]
        ev = [n for n, t in lines if topic[1] in t]
        return {"terms": terms, "interests": [{"text": topic[0], "evidence": ev}] if len(ev) >= 2 else []}
    return answer


class LearnTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lm-")
        self.path = os.path.join(self.dir, "mem.db")
        self.ime = Memory(self.path, tool="luoke-ime")
        self.sock = os.path.join(self.dir, "hub.sock")
        self.hub_server = FakeHub(self.sock, model())
        self.hub_server.topics = None
        self.thread = threading.Thread(target=self.hub_server.serve_forever, kwargs={"poll_interval": 0.05})
        self.thread.start()
        self.whole = WholeMemory(self.path)

    def tearDown(self):
        self.hub_server.shutdown()
        self.thread.join()
        self.hub_server.server_close()
        self.whole.close()
        self.ime.close()

    def typed(self, text, day, auto=False, sensitive=None):
        return self.ime.record("user", "statement", text, topic="ime-commit", sensitive=sensitive,
                               data={"app": "com.apple.Notes", "auto": auto}, happened_at=f"2026-10-{day:02d} 10:00:00")

    def run_learn(self, **kw):
        return L.learn(self.whole, Hub(self.sock, version="test"), now=NOW, **kw)

    def test_it_waits_for_enough_typing_or_a_day(self):
        for i in range(L.EVERY - 1):
            self.typed(f"第 {i} 段", 14)
        self.assertEqual(L.due(self.whole, NOW), (True, L.EVERY - 1))        # never ran: a day has passed
        L._put(self.whole, "typing.last_run", NOW - 3600)
        self.assertEqual(L.due(self.whole, NOW), (False, L.EVERY - 1))       # ran an hour ago, not enough yet
        self.assertEqual(self.run_learn()["ran"], False)
        self.typed("再一段", 14)
        self.assertEqual(L.due(self.whole, NOW), (True, L.EVERY))
        self.assertEqual(L.due(self.whole, NOW + L.DAILY), (True, L.EVERY))

    def test_a_term_counts_on_three_days_five_times_counted_here(self):
        for day, text in ((10, "行云今天上线"), (11, "行云的文档，行云的接口"), (12, "问一下行云"), (12, "行云挂了"),
                          (12, "星火也不错"), (13, "星火"), (13, "先快后好"), (13, "行云", ), (13, "顺手的行云")):
            self.typed(text, day)
        self.typed("行云行云行云", 14, auto=True)                               # switched away: not the user's choice
        out = self.run_learn(force=True)
        self.assertEqual((out["terms"], out["asking"]), (["行云", "星火"], 1))   # 星火: twice, 2 days -- ask first
        self.assertEqual(out["held"], {"not yet": 1, "blocked": 0})            # 先快后好: once
        self.assertEqual(L._get(self.whole, "typing.last_outcome"),
                         "归纳了 9 段，得出 2 条常用说法、0 个话题（1 条等你确认）；1 条说法只出现过一次，接着数")
        self.assertEqual([n["text"] for n in self.whole.notes(kinds=["term"], status=["proposed"])], ["星火"])
        self.assertIn("已在 2 天的打字里见到 2 次；满 3 天、5 次就直接记下", self.whole.notes(kinds=["term"], status=["proposed"])[0]["why"])
        note = [n for n in self.whole.notes(kinds=["term"])][0]
        self.assertEqual((note["text"], note["status"], note["tool"]), ("行云", "active", "luoke-mem"))
        full = self.whole.note(note["id"])
        self.assertEqual(sorted(c["happened_at"][:10] for c in full["cites"]),     # the latest segment of each day
                         ["2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13"])
        self.assertIn("天的打字里出现", full["why"])
        self.assertEqual([n["text"] for n in self.ime.notes(kinds=["term"])], ["行云"])   # the input method reads it
        self.assertFalse(self.run_learn(force=True)["ran"])                            # nothing new typed: no run
        for day in (14, 15):                                                 # the model never names them again:
            self.typed("先快后好，星火，星火", day)                            # what waits is counted all the same
        self.hub_server.answer = model(words=())
        out = self.run_learn(force=True)
        self.assertEqual((sorted(out["terms"]), out["asking"]), (["先快后好", "星火"], 1))
        self.assertEqual([n["text"] for n in self.ime.notes(kinds=["term"])], ["星火", "行云"])   # 4 days, 6 times
        self.assertEqual([n["text"] for n in self.whole.notes(kinds=["term"], status=["proposed"])], ["先快后好"])
        with self.whole._lock:
            self.assertEqual(self.whole.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)

    def test_what_the_user_turned_down_never_comes_in(self):
        for day in (10, 11, 12, 13):
            self.typed("行云和星火，行云和星火", day)
        self.ime.record("user", "statement", "不要候选「星火」", topic="ime-forget", data={"text": "星火"})
        old = self.whole.state("term", "行云")
        self.whole.reject(old)
        out = self.run_learn(force=True)
        self.assertEqual((out["terms"], out["held"]["blocked"]), ([], 2))
        asked = self.hub_server.requests[0]["body"]["messages"][1]["content"]
        self.assertIn("用户不要的候选", asked)
        self.assertIn("星火", asked.split("用户不要的候选")[1].split("\n\n")[0])

    def test_an_interest_waits_until_it_spans_three_days(self):
        self.typed("本地推理太慢", 10)
        self.typed("推理用 4-bit", 11)
        self.assertEqual(self.run_learn(force=True)["asking"], 1)
        self.assertEqual(L._get(self.whole, "typing.last_outcome"), "归纳了 2 段，得出 0 条常用说法、1 个话题（1 条等你确认）")
        self.assertEqual(self.whole.notes(kinds=["interest"]), [])            # not in effect: asked first
        guess = self.whole.notes(kinds=["interest"], status=["proposed"])[0]
        self.assertEqual((guess["text"], guess["why"]), ("本地大模型推理", "已在 2 天的打字里谈到；满 3 天就直接记下"))
        with self.whole._lock:
            waiting = self.whole.db.execute("SELECT text, record_ids FROM candidates").fetchall()
        self.assertEqual([r[0] for r in waiting], ["本地大模型推理"])
        self.typed("推理加速的论文", 13)
        self.typed("再看看推理", 13)
        out = self.run_learn(force=True)
        self.assertEqual((out["interests"], out["asking"]), (["本地大模型推理"], 0))
        n = self.whole.notes(kinds=["interest"])[0]
        self.assertEqual(n["id"], guess["id"])                               # the same note, now in effect
        self.assertEqual(len(self.whole.note(n["id"])["cites"]), 3)
        with self.whole._lock:
            self.assertEqual(self.whole.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)

    def test_sensitive_typing_is_learnt_from_but_waits_for_the_user(self):
        for day in (10, 11, 12):
            self.typed("去医院复诊，行云陪我，行云", day, sensitive=["health"])
        out = self.run_learn(force=True)
        self.assertEqual(out["terms"], ["行云"])
        n = self.whole.notes(kinds=["term"], status=["proposed"])[0]
        self.assertEqual((n["text"], n["sensitive"]), ("行云", "health"))
        self.assertEqual([x["id"] for x in self.ime.notes(kinds=["term"], status=["proposed"])], [n["id"]])   # seen by all

    def test_stopped_by_the_hub_it_keeps_what_was_done(self):
        for i in range(90):
            self.typed(f"行云 {i}", 10 + i % 4)
        self.hub_server.refuse = {2: "busy"}
        out = self.run_learn(force=True)
        self.assertEqual((out["stopped"], out["segments"]), ("busy (retry after 60 s)", 40))   # the hub's batch is 40
        self.assertEqual(L._get(self.whole, "typing.last_outcome"),
                         "归纳了 40 段，得出 1 条常用说法、0 个话题；停在：螺壳在忙（busy (retry after 60 s)），下次接着")
        self.assertEqual(float(L._get(self.whole, "typing.last_attempt")), NOW)
        self.assertEqual(int(L._get(self.whole, "typing.last_id")), sorted(r["id"] for r in self.ime.records())[39])
        self.assertEqual(L._get(self.whole, "typing.last_run"), "")           # not a finished run
        self.hub_server.refuse = {}
        out = self.run_learn(force=True)
        self.assertEqual((out["stopped"], out["segments"]), (None, 50))
        req = self.hub_server.requests[-1]
        self.assertEqual(req["path"], "/tasks/luoke-mem.typing/v1/chat/completions")
        self.assertEqual((req["headers"]["X-Luoke-Priority"], req["headers"]["X-Luoke-Accessory"]), ("background", "luoke-mem/test"))
        self.assertEqual((req["body"]["stream"], req["body"]["luoke_items"]), (True, 10))

    def test_forgetting_typing_forgets_what_was_waiting_on_it(self):
        self.typed("本地推理太慢", 10)
        self.typed("推理用 4-bit", 11)
        self.run_learn(force=True)
        self.typed("先快后好", 12)                                            # a term seen once: counted on
        self.hub_server.answer = model(words=("先快后好",))
        self.assertEqual(self.run_learn(force=True)["held"]["not yet"], 1)
        with self.whole._lock:
            self.assertEqual([r[0] for r in self.whole.db.execute("SELECT kind FROM candidates ORDER BY kind")], ["interest", "term"])
        self.ime.forget_records(topics=["ime-commit"])
        with self.whole._lock:
            self.assertEqual(self.whole.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
        self.assertEqual(self.whole.notes(kinds=["interest"], status=["proposed"]), [])   # the guess rested on it too

    def test_the_evaluation_runs_through_the_apps_own_code(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench"))
        import run_typing
        import typing_set
        planted = [t for case in typing_set.CASES[:2] for t in case["expect_terms"]]
        self.hub_server.answer = model(words=planted, topic=("客户续费和流失", "续费"))
        out = tempfile.mkdtemp(prefix="lm-bench-")
        argv = sys.argv
        try:
            sys.argv = ["run_typing.py", "--hub", self.sock, "--label", "fake", "--out", out, "--cases", "2"]
            quiet = io.StringIO()
            with redirect_stdout(quiet), redirect_stderr(quiet):
                self.assertEqual(run_typing.main(), 0)
            with open(os.path.join(out, "typing", "fake.json"), encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual((report["task"], report["cases"], report["hard_pass"]), ("luoke-mem.typing", 2, True))
            self.assertEqual(report["score"]["term_recall"], 1.0)
            self.assertEqual(len(report["set_sha256"]), 64)
            self.hub_server.requests.clear()
            self.hub_server.refuse = {1: "busy"}                              # a lab hub resting: wait, go on
            with redirect_stdout(quiet), redirect_stderr(quiet), mock.patch("time.sleep"):
                self.assertEqual(run_typing.main(), 0)
            self.assertIn("waiting", quiet.getvalue())
            self.hub_server.refuse = {}
            self.hub_server.answer = model(words=["钢琴"], topic=("学钢琴", "钢琴"))   # someone else's matter
            with redirect_stdout(quiet), redirect_stderr(quiet):
                self.assertEqual(run_typing.main(), 2)
        finally:
            sys.argv = argv

    def test_the_topics_evaluation_runs_through_the_apps_own_code(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bench"))
        import run_topics
        import topics_set

        def fake(named):                                            # names topics from what the sources say
            def answer(text):
                lines = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^(\d+)\. (.*)$", text, re.M)]
                pick = lambda w: [n for n, t in lines if w in t]        # noqa: E731
                return {"interests": [{"text": t, "evidence": pick(w)} for t, w in named["interests"] if pick(w)],
                        "dislikes": [{"text": t, "evidence": pick(w)} for t, w in named["dislikes"] if pick(w)]}
            return answer
        case = topics_set.CASES[0]
        self.hub_server.topics = fake({"interests": [("美国房产：关注 00427 的房价", "00427"), ("以太坊 Layer2 的手续费", "Layer2")],
                                       "dislikes": [("明星八卦", "八卦")]})
        out = tempfile.mkdtemp(prefix="lm-bench-")
        argv = sys.argv
        try:
            sys.argv = ["run_topics.py", "--hub", self.sock, "--label", "fake", "--out", out, "--cases", "1"]
            quiet = io.StringIO()
            with redirect_stdout(quiet), redirect_stderr(quiet):
                self.assertEqual(run_topics.main(), 0)
            with open(os.path.join(out, "topics", "fake.json"), encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual((report["task"], report["cases"], report["hard_pass"]), ("luoke-mem.topics", 1, True))
            # answers named by field and object, longer and by name (oMLX 0.7.2, 10-26), pass the app's filters;
            # a brand or a sender's news alone does not
            self.assertEqual((report["filter_failures"], run_topics.filters()), ([], []))
            sc = report["score"]
            self.assertEqual((sc["interest_recall"], sc["specific"], sc["dislike_recall"]), (1.0, 1.0, 1.0))
            self.assertEqual(report["items"][0]["case"], case["name"])
            self.assertNotIn("Northbay", str(self.hub_server.requests))                    # business mail is not read
            self.hub_server.topics = fake({"interests": [("Homefinch", "00427"), ("面试准备", "面试")], "dislikes": []})
            with redirect_stdout(quiet), redirect_stderr(quiet):
                self.assertEqual(run_topics.main(), 2)                     # a brand, a one-off: hard
            with open(os.path.join(out, "topics", "fake.json"), encoding="utf-8") as f:
                report = json.load(f)
            self.assertEqual(len(report["items"][0]["hard_failures"]), 2)
        finally:
            sys.argv = argv

    def test_the_command_runs_one_at_a_time_and_says_what_it_did(self):
        import fcntl
        import subprocess
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for day in (10, 11, 12, 13):
            self.typed("行云，行云", day)
        cmd = [sys.executable, "-m", "luoke_mem", "--path", self.path, "learn", "--force", "--hub", self.sock]
        with open(self.path + "-learn.lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            busy = subprocess.run(cmd, cwd=here, capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(busy.stdout), {"ran": False, "why": "running"})
        done = subprocess.run(cmd, cwd=here, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["terms"], ["行云"])
        again = subprocess.run(cmd[:-3] + ["--again", "--hub", self.sock], cwd=here, capture_output=True, text=True, timeout=30)
        self.assertEqual((again.returncode, json.loads(again.stdout)["segments"]), (0, 4), again.stderr)   # all of it
        self.assertEqual(json.loads(again.stdout)["terms"], [])                # known already: nothing new
        self.typed("行云", 14)
        gone = subprocess.run(cmd[:-2] + ["--hub", os.path.join(self.dir, "none.sock")], cwd=here,
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(gone.returncode, 1)                                  # no hub there: said, not hidden

    def test_busy_for_the_hub_s_own_reasons_is_waited_out_the_user_coming_back_is_not(self):
        for day in (10, 11, 12, 13):
            self.typed("行云，行云", day)
        self.hub_server.refuse = {1: ("busy", "power_cooldown"), 2: ("busy", "background_busy")}
        with mock.patch("time.sleep") as slept:
            out = self.run_learn(force=True)
        self.assertEqual((out["stopped"], out["terms"]), (None, ["行云"]))      # waited twice, then went on
        self.assertEqual([c.args[0] for c in slept.call_args_list], [60, 60])
        L._put(self.whole, "typing.last_id", "0")
        self.hub_server.requests.clear()
        self.hub_server.refuse = {1: ("busy", "interactive")}
        with mock.patch("time.sleep") as slept:
            out = self.run_learn(force=True)
        self.assertEqual((out["stopped"], slept.called), ("busy interactive (retry after 60 s)", False))
        L._put(self.whole, "typing.last_id", "0")
        self.hub_server.requests.clear()
        self.hub_server.refuse = {i: ("busy", "power_cooldown") for i in range(1, 10)}   # never ends: give up
        with mock.patch("time.sleep"):
            out = self.run_learn(force=True)
        self.assertTrue(out["stopped"].startswith("busy power_cooldown"))
        self.assertEqual(len(self.hub_server.requests), 6)                   # 5 waits of 60 s = the limit

    def test_every_attempt_leaves_its_outcome_without_content(self):
        self.typed("行云的接口文档", 10)
        L._put(self.whole, "typing.last_run", NOW - 60)
        self.run_learn()
        self.assertEqual(L._get(self.whole, "typing.last_outcome"), "没有跑：还没到时候（not due），1 段在等")
        self.hub_server.refuse = {1: "busy"}
        self.run_learn(force=True)
        line = L._get(self.whole, "typing.last_outcome")
        self.assertTrue(line.startswith("归纳了 0 段") and "busy" in line, line)
        self.assertNotIn("行云", line)                                       # counts and codes, never content
        self.hub_server.refuse = {}
        self.hub_server.answer = model(words=(), topic=("无关", "无关"))
        self.run_learn(force=True)
        self.assertEqual(L._get(self.whole, "typing.last_outcome"), "归纳了 1 段，得出 0 条常用说法、0 个话题；模型没有提出候选")
        self.typed("行云的接口文档", 11)
        self.hub_server.status = "unsupported"                               # a 16 GB Mac: the hub says so
        self.assertEqual(self.run_learn(force=True)["ran"], False)
        self.assertEqual(L._get(self.whole, "typing.last_outcome"),
                         "没有跑：这台 Mac 不支持从打字里归纳（task unsupported），1 段在等")
        self.hub_server.status = "available"
        self.hub_server.refuse = {len(self.hub_server.requests) + 1: "unsupported"}   # refused all the same: said
        out = self.run_learn(force=True)                                  # and the rest goes on
        self.assertEqual((out["stopped"], "rumination" in out, "topics" in out), ("unsupported", True, True))
        self.assertTrue(L._get(self.whole, "typing.last_outcome").endswith("停在：这台 Mac 不支持从打字里归纳（unsupported），下次接着"))

    def test_the_released_hub_or_the_one_named_never_a_lab_one_by_itself(self):
        from luoke_mem import hub
        lab = os.path.join(self.dir, "lab.sock")
        open(lab, "w").close()
        with mock.patch.dict(os.environ, {"LUOKE_MEM_TEST": ""}), mock.patch.object(hub, "LAB_SOCKET", lab), \
                mock.patch.object(hub, "DEFAULT_SOCKET", os.path.join(self.dir, "none.sock")):
            os.environ.pop("LUOKE_HUB_SOCKET", None)
            self.assertIsNone(hub.socket_path())                          # no released hub: none, not the lab's
            self.assertEqual(hub.socket_path(lab), lab)                   # named: that one

    def test_what_is_typed_after_the_newest_was_forgotten_is_still_learnt(self):
        """Deleting the newest records hands their ids to what comes next; a cursor on ids alone skipped it."""
        for text in ("一", "二", "三"):
            self.typed(text, 14)
        self.run_learn(force=True)
        self.assertEqual(L._typed(self.whole, L._cursor(self.whole)), [])
        self.ime.forget_records(topics=["ime-commit"], since="2026-10-14 00:00:00")      # the input method forgets them
        time.sleep(1.1)                                                                   # a later second
        reused = self.typed("行云新写的", 14)
        self.assertLessEqual(reused, int(L._get(self.whole, "typing.last_id")))           # an id already passed
        self.assertEqual([s["text"] for s in L._typed(self.whole, L._cursor(self.whole))], ["行云新写的"])

    def test_a_candidate_reads_back_only_typing(self):
        self.typed("本地推理太慢", 10)
        self.typed("推理用 4-bit", 11)
        self.run_learn(force=True)
        with self.whole._lock:
            ids = __import__("json").loads(self.whole.db.execute("SELECT record_ids FROM candidates").fetchone()[0])
        self.ime.forget_records(topics=["ime-commit"])
        mail = Memory(self.path, tool="luoke-mail")                                   # another tool takes the ids
        taken = [mail.record("user", "statement", f"邮件 {i}", topic="sent-mail", happened_at=f"2026-10-{12 + i} 10:00:00")
                 for i in range(3)]
        self.assertTrue(set(ids) & set(taken))
        self.assertEqual(L._interest(self.whole, "本地大模型推理", ids, [], "2026-10-16 10:00:00"), "blocked")   # not its
        mail.close()

    def test_a_socket_named_in_code_is_checked_too(self):
        self.assertIsNone(Hub(os.path.join(self.dir, "none.sock")).path)
        from luoke_mem import hub
        self.assertIsNone(Hub(hub.DEFAULT_SOCKET).path)                                   # the real one: refused

    def test_rumination_asks_what_is_worth_it_suggests_and_demotes(self):
        """ADR 0012: what only the user can settle is asked, with a reason matching its evidence, at most 2 a day;
        repeated clear-outs suggest unsubscribing, with evidence; a stale interest of the memory's goes back to a
        guess. Not asked: facts from others, what the user's own records will bear out
        given time (a term, a fact from their mail), dislikes and the digest's taste."""
        ov = lambda t: self.ime.record("user", "statement", t, topic="ime-commit")
        waits = self.whole.propose("term", "甲说法", [ov("甲说法"), ov("甲说法")])
        letter = Memory(self.path, tool="luoke-mail")
        label = lambda t: letter.record("user", "statement", f"标签「{t}」", topic="own-label")   # noqa: E731
        binned = lambda s: letter.record("user", "activity", "移到垃圾箱", topic="mail-acted",   # noqa: E731
                                         data={"sender": s, "action": "trash", "by": "user"})
        guesses = [letter.propose("preference", f"{t}的邮件都移到垃圾箱", [binned(t), binned(t)],
                                  data={"sender": t, "action": "trash", "category": "promotion"})
                   for t in ("某商城", "某书店", "某论坛")]                   # never by itself: the user's say
        quiet = [letter.propose("dislike", "银行账单", [label("财务")]),
                 letter.propose("fact", "单位：某公司", [letter.record("user", "statement", "署名", topic="sent-mail")]), waits]
        other = letter.propose("fact", "在某商城有账户", [letter.record("third_party", "statement", "某商城来信", topic="received-mail")])
        for day in (3, 9, 12):
            letter.record("user", "activity", "清掉了「某商城」的 8 封广告促销", topic="mail-cleared", ref="from:shop@x",
                          data={"sender": "某商城", "count": 8, "by": "user"}, happened_at=f"2026-10-{day:02d} 10:00:00")
        letter.record("user", "receipt", "清掉了「某书店」的 2 封", topic="mail-cleared", ref="from:book@x",
                      data={"sender": "某书店", "count": 2, "by": "suggestion"}, happened_at="2026-10-12 10:00:00")
        stale = self.whole._note("interest", "旧话题", "inferred", "active",
                                 [self.ime.record("user", "statement", "旧", topic="ime-commit", happened_at="2026-05-01 10:00:00")])
        r = L.ruminate(self.whole, NOW)
        self.assertEqual(r, {"tidied": 2, "demoted": 0, "habits": 0, "undone": 0, "questions": 2, "suggestions": 1})   # + the label-only dislike
        self.assertEqual(self.whole.note(stale)["status"], "superseded")     # one segment, in effect or not: set aside
        agent = Memory(self.path, tool="luoke-agent")
        listed = agent.asks(now=LATER)
        self.assertEqual([a["kind"] for a in listed], ["suggestion", "question", "question"])
        s = listed[0]
        self.assertIn("某商城", s["text"])
        self.assertTrue(s["evidence"].startswith("你在 10 月 3 日、10 月 9 日、10 月 12 日") and "24 封" in s["evidence"], s["evidence"])
        q = listed[1]
        self.assertTrue(q["why"].startswith("你几次自己这样处理") and "确认后" in q["why"], q["why"])   # as its evidence is
        self.assertEqual({a["about_note"] for a in listed[1:]} - set(guesses), set())
        self.assertNotIn(other, [a["about_note"] for a in listed])            # read off others' letters: not asked
        self.assertEqual(L.ruminate(self.whole, NOW), {"tidied": 0, "demoted": 0, "habits": 0, "undone": 0, "questions": 0, "suggestions": 0})   # 2 a day
        agent.reply(q["id"], "later", now=time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW)))
        self.assertEqual(L.ruminate(self.whole, NOW + 86400)["questions"], 1)     # next day: the third; the demoted one waits
        self.assertEqual(L.ruminate(self.whole, NOW + 8 * 86400)["questions"], 1)  # a week on: the put-off one again
        asked = {a["about_note"] for a in agent.asks(status=["open", "answered", "later", "withdrawn"], now=LATER)}
        self.assertFalse(asked & set(quiet + [stale]))
        letter.close()
        agent.close()

    def test_a_habit_by_hand_is_offered_at_once_for_its_kind_of_mail(self):
        """What the user archives by hand -- in luoke-mail or elsewhere -- twice, however close
        together, is offered ("以后都这样？"); a sender's promotions are not its bills."""
        mail = Memory(self.path, tool="luoke-mail")
        key = {"account": "mbx-1", "sender": "list:news.shop", "name": "某商城", "by": "user"}
        for i in range(2):
            mail.record("user", "activity", "归档了 1 封", topic="mail-acted", happened_at="2026-10-14 09:0%d" % i,
                        data=dict(key, action="archive", category="促销", count=1))
        mail.record("user", "activity", "归档了 1 封", topic="mail-acted", happened_at="2026-10-14 09:05",
                    data=dict(key, action="archive", category="账单", count=1))
        L.ruminate(self.whole, NOW)
        habit, = mail.notes(kinds=["preference"], confidence=["guess"])
        self.assertEqual((habit["text"], habit["data"]["category"]), ("某商城的促销邮件都归档", "促销"))
        offer, = [a for a in mail.asks(tool="luoke-mail", now=LATER)]
        self.assertEqual((offer["kind"], offer["about_note"], offer["text"]),
                         ("suggestion", habit["id"], "以后「某商城」的「促销」邮件，都自动归档？"))
        mail.reply(offer["id"], "useful")
        sure, = mail.notes(kinds=["preference"], confidence=["sure"])
        self.assertEqual((sure["id"], sure["auto"]), (habit["id"], True))
        mail.close()

    def test_a_label_the_user_adds_is_a_habit_too(self):
        """ADR 0016: adding a label can run by itself once confirmed; a habit per label."""
        mail = Memory(self.path, tool="luoke-mail")
        key = {"account": "mbx-1", "sender": "list:news.shop", "name": "某商城", "category": "promotion", "by": "user"}
        for i in range(2):
            mail.record("user", "activity", "加了标签", topic="mail-acted", happened_at="2026-10-14 09:0%d" % i,
                        data=dict(key, action="label", label="购物", count=1))
        L.ruminate(self.whole, NOW)
        habit, = mail.notes(kinds=["preference"], confidence=["guess"])
        self.assertEqual((habit["text"], habit["data"]["label"]), ("某商城的促销邮件都加上标签「购物」", "购物"))
        offer, = mail.asks(tool="luoke-mail", now=LATER)
        self.assertEqual(offer["text"], "以后「某商城」的「促销」邮件，都自动加上标签「购物」？")
        mail.reply(offer["id"], "useful")
        self.assertTrue(mail.note(habit["id"])["auto"])
        mail.close()

    def test_only_what_was_seen_lately_is_asked(self):
        """No questions about what only years-old evidence speaks of; the newest are asked first."""
        mail = Memory(self.path, tool="luoke-mail")
        binned = lambda s, d: mail.record("user", "activity", "移到垃圾箱", topic="mail-acted", happened_at=d,   # noqa: E731
                                          data={"sender": s, "action": "trash", "by": "user"})
        habit = lambda s, d: mail.propose("preference", f"{s}的邮件都移到垃圾箱", [binned(s, d), binned(s, d)],   # noqa: E731
                                          data={"sender": s, "action": "trash", "category": "promotion"})
        old = habit("某集邮社", "2016-04-01")
        older = habit("某车行", "2026-07-01")
        newest = habit("某烘焙店", "2026-10-10")
        L.ruminate(self.whole, NOW)
        asked = [a["about_note"] for a in sorted(Memory(self.path, tool="luoke-agent").asks(now=LATER), key=lambda a: a["id"])]
        self.assertEqual(asked, [newest, older])                       # in the order they were thought of
        self.assertNotIn(old, asked)
        mail.close()

    def test_habits_from_what_the_user_did_in_mail(self):
        """ADR 0012 amendment: the memory draws habits from luoke-mail's records; a likely one that can run by
        itself is offered, yes makes it the user's; undoing what it did sends it back to a guess. What a rule did
        is never evidence."""
        mail = Memory(self.path, tool="luoke-mail")
        key = {"account": "mbx-1", "sender": "list:news.shop", "name": "某商城", "category": "promotion"}
        for day in ("2026-10-01", "2026-10-09"):
            mail.record("user", "activity", "把建议从保留改成归档", topic="mail-override", happened_at=day,
                        data=dict(key, by="user", **{"from": "keep", "to": "archive"}))
        for day in ("2026-10-02", "2026-10-03", "2026-10-04"):
            mail.record("system", "activity", "按习惯自动归档", topic="mail-auto", happened_at=day, data=dict(key, by="rule"))
        for day in ("2026-10-05", "2026-10-06"):
            mail.record("user", "activity", "移到垃圾箱", topic="mail-acted", happened_at=day,
                        data={"account": "mbx-1", "sender": "from:ads@x", "name": "某广告", "action": "trash", "count": 3, "by": "user"})
        r = L.ruminate(self.whole, NOW)
        self.assertEqual(r["habits"], 2)
        habit, = mail.notes(kinds=["preference"])
        self.assertEqual((habit["text"], habit["confidence"], habit["data"]["action"], habit["auto"]),
                         ("某商城的促销邮件都归档", "likely", "archive", False))
        binned, = mail.notes(kinds=["preference"], confidence=["guess"])
        self.assertEqual((binned["data"]["action"], binned["text"]), ("trash", "某广告的邮件都移到垃圾箱"))
        offer, = [a for a in mail.asks(tool="luoke-mail", now=LATER) if a["kind"] == "suggestion"]
        self.assertEqual((offer["about_note"], offer["action"]["capability"]), (habit["id"], "habit"))
        self.assertIn("自动归档", offer["text"])
        mail.reply(offer["id"], "useful")
        self.assertTrue(mail.note(habit["id"])["auto"])
        mail.record("user", "activity", "撤回了自动归档", topic="mail-undo-auto", data={"note_id": habit["id"], "by": "user"})
        self.assertEqual(L.ruminate(self.whole, NOW + 60)["undone"], 1)
        n = mail.note(habit["id"])
        self.assertEqual((n["confidence"], n["auto"]), ("guess", False))
        self.assertEqual(L.ruminate(self.whole, NOW + 120)["undone"], 0)        # counted once
        mail.close()

    def test_what_was_let_go_for_being_old_does_not_come_straight_back(self):
        """A term with nothing newer than 90 days goes back to a guess; old typing alone does not bring it back."""
        for day in ("2026-05-01", "2026-05-02", "2026-05-03"):
            for _ in range(2):
                self.ime.record("user", "statement", "旧说法用一下", topic="ime-commit", happened_at=f"{day} 10:00:00")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(NOW))
        self.assertNotIn(L._term(self.whole, "旧说法", [], "2026-05-04 10:00:00"), ("blocked", "not yet"))
        nid = self.whole._same_note("term", "旧说法")["id"]
        self.assertEqual(self.whole.note(nid)["confidence"], "likely")
        L.ruminate(self.whole, NOW)
        self.assertEqual(self.whole.note(nid)["confidence"], "guess")
        self.assertEqual(L._term(self.whole, "旧说法", [], stamp), "not yet")       # nothing lately
        self.assertEqual(self.whole.note(nid)["confidence"], "guess")

    def test_topics_from_what_the_user_does_in_mail(self):
        """ADR 0012 amendment: the model names topics from luoke-mail's records; each is the digest's taste at once,
        and an interest about the user only where the table bears it out; a dislike only from 没兴趣 in the digest."""
        def topics(text):
            lines = [(int(m.group(1)), m.group(2)) for m in re.finditer(r"^(\d+)\. (.*)$", text, re.M)]
            pick = lambda w: [n for n, t in lines if w in t]        # noqa: E731
            return {"interests": [{"text": "马拉松训练", "evidence": pick("马拉松")}, {"text": "家庭财务整理", "evidence": pick("财务")}],
                    "dislikes": [{"text": "电商促销", "evidence": pick("促销")}, {"text": "广告", "evidence": pick("广告")},
                                 {"text": "越野跑", "evidence": pick("越野")}]}       # 收藏 is not 没兴趣
        self.hub_server.topics = topics
        mail = Memory(self.path, tool="luoke-mail")
        for day in ("2026-09-01", "2026-09-15", "2026-10-05"):
            mail.record("user", "activity", "打开了精选：马拉松配速怎么练", topic="digest-open", happened_at=day)
        for name in ("财务", "财务/收据"):
            mail.record("user", "statement", f"标签「{name}」：信用卡账单、收据", topic="own-label", happened_at="2026-10-08")
        for day in ("2026-10-01", "2026-10-09"):
            mail.record("user", "statement", "对精选里「双十一促销」：没兴趣", topic="digest-feedback", happened_at=day)
            mail.record("user", "statement", "收藏了「越野跑装备」", topic="digest-feedback", happened_at=day,
                        data={"value": "useful", "title": "越野跑装备"})
        for _ in range(2):
            mail.record("user", "activity", "清掉了 30 封广告", topic="mail-cleared", happened_at="2026-10-10")
        mail.record("user", "activity", "按习惯归档了马拉松周刊", topic="mail-read", data={"by": "rule"}, happened_at="2026-10-11")
        mail.record("user", "activity", "打开了精选：马拉松老新闻", topic="digest-open", happened_at="2015-03-01")   # years ago
        out = self.run_learn(force=True)["topics"]
        self.assertEqual((out["ran"], out["records"]), (True, 9))       # not a rule's, not years old, not bills
        taste = {n["text"]: n["confidence"] for n in mail.notes(kinds=["taste"])}
        self.assertEqual(taste, {"马拉松训练": "likely"})                                # the digest's, at once
        interest = {n["text"]: n["confidence"] for n in mail.notes(kinds=["interest"], status=["active", "proposed"])}
        self.assertEqual(interest, {"马拉松训练": "likely"})            # about the user: only what is borne out
        dislike = {n["text"]: n["confidence"] for n in mail.notes(kinds=["dislike"], status=["active", "proposed"])}
        self.assertEqual(dislike, {"电商促销": "likely"})                               # clear-outs are not 没兴趣
        self.assertIn("1 个精选口味", L._get(self.whole, "topics.last_outcome"))
        self.assertEqual(L.learn(self.whole, Hub(self.sock, version="test"), now=NOW + 60)["topics"]["ran"], False)
        mail.close()

    def test_a_source_is_read_whole_from_its_mails(self):
        """ADR 0015: the memory reads the original. A subscription is one source: its months, opens and mails together;
        business mail is not reading; a month written again with its mails replaces the bare one."""
        from luoke_mem import topics as T
        mail = Memory(self.path, tool="luoke-mail")
        for m in (8, 9):
            mail.record("user", "activity", "订阅「Homefinch」：本月 4 封、打开 3 封", topic="mail-read", ref=f"list:homefinch@2026-{m:02d}",
                        happened_at=f"2026-{m:02d}-28", data={"sender": "list:homefinch", "name": "Homefinch", "count": 4, "opened": 3})
        mail.record("user", "activity", "订阅「Homefinch」：本月 4 封、打开 3 封", topic="mail-read", ref="list:homefinch@2026-09",
                    happened_at="2026-09-28", data={"sender": "list:homefinch", "name": "Homefinch", "count": 4, "opened": 3, "mails": [
                        {"subject": "New listings in 00427", "text": "3 new homes for sale in 00427, median price $1.2M"}]})
        mail.record("user", "activity", "订阅「Northbay 银行通知」：本月 4 封、打开 4 封", topic="mail-read", happened_at="2026-09-28",
                    data={"sender": "from:northbay", "category": "finance", "count": 4, "opened": 4})
        mail.record("user", "statement", "对精选里「L2 手续费」：有用", topic="digest-feedback", happened_at="2026-10-02",
                    data={"source": "某链周报", "text": "以太坊 Layer2 的手续费这周又降了一半"})
        rows = T._rows(self.whole, NOW)
        got = {s["name"]: s for s in T.sources(rows)}
        self.assertEqual(sorted(got), ["Homefinch", "某链周报"])                         # not the bank's notices
        z = got["Homefinch"]
        self.assertEqual(len(z["ids"]), 2)                                             # the bare September gave way
        self.assertIn("2 个月里来了 8 封、打开 6 封", z["text"])
        self.assertIn("《New listings in 00427》3 new homes for sale in 00427", z["text"])
        self.assertIn("说过有用 1 次", got["某链周报"]["text"])
        self.assertIn("以太坊 Layer2 的手续费", got["某链周报"]["text"])
        # a brand alone is never a topic; a field and its object is
        self.assertTrue(T.brand("Homefinch", ["Homefinch"]) and T.brand("Quillchain Talk", []) and T.brand("Lumenfold 相关动态", []))
        self.assertFalse(T.brand("美国房产：关注某个邮编的房价", ["Homefinch"]))
        batch = T.sources(rows)
        found = {"interests": [{"text": "Homefinch", "evidence": [1]}, {"text": "美国房产：关注 00427 的房价", "evidence": [1]},
                               {"text": "美国房产相关动态", "evidence": [1]}],       # a sender's news
                 "dislikes": []}
        out = T.take(self.whole, found, [s for s in batch if s["name"] == "Homefinch"], [], "2026-10-14 10:00:00")
        self.assertEqual([self.whole.note(n)["text"] for n in out["tastes"]], ["美国房产：关注 00427 的房价"])
        mail.close()

    def test_no_task_on_this_tier_no_request(self):
        """Where the hub offers no typing task (16 GB), nothing is asked of it."""
        for day in (10, 11, 12):
            self.typed("行云上线了", day)
        self.hub_server.status = "absent"
        out = self.run_learn(force=True)
        self.assertEqual((out["ran"], out["why"]), (False, "task unsupported"))
        self.assertEqual([r for r in self.hub_server.requests if "luoke-mem.typing" in r["path"]], [])

    def test_topics_stopped_half_way_go_on_where_they_were(self):
        """Wrapped up after two requests, every run redid the same two. The sources to read are
        claimed and crossed off batch by batch; and thinking over what is known comes first, model or not."""
        mail = Memory(self.path, tool="luoke-mail")
        for i in range(4):
            for day in ("2026-09-01", "2026-09-20"):
                mail.record("user", "activity", f"打开了精选：第 {i} 个来源", topic="digest-open", happened_at=day,
                            data={"source": f"来源{i}", "text": f"来源{i}讲的东西"})
        seen = []
        self.hub_server.topics = lambda text: (seen.append(sorted(set(__import__("re").findall(r"来源\d", text)))),
                                               {"interests": [], "dislikes": []})[1]
        self.hub_server.topics_batch = 1
        self.hub_server.refuse = {3: "busy"}                     # typing asks nothing: the 3rd is the 3rd topics request
        from luoke_mem import topics as T
        first = T.run(self.whole, Hub(self.sock, version="test"), force=True, now=NOW)
        self.assertEqual((first["sources"], bool(first["stopped"])), (2, True))
        self.assertEqual(len(T._pending(self.whole)), 2)
        self.hub_server.refuse = {}
        second = T.run(self.whole, Hub(self.sock, version="test"), now=NOW + 60)
        self.assertEqual((second["sources"], second["stopped"], T._pending(self.whole)), (2, None, []))
        self.assertEqual(sorted(x for s in seen for x in s), ["来源0", "来源1", "来源2", "来源3"])   # each read once
        mail.close()

    def test_an_engine_failure_stops_neither_the_run_nor_learning(self):
        """An engine failure on typing ended the whole run; one input that always fails held
        learning for ever. The rest of the run goes on; a batch failed twice is passed over."""
        for day in (10, 11, 12):
            self.typed("行云上线了", day)
        self.hub_server.refuse = {1: "engine_failed"}
        out = self.run_learn(force=True)
        self.assertEqual((out["stopped"], "rumination" in out), ("engine_failed", True))
        self.assertIn(L._get(self.whole, "typing.last_id"), ("", "0"))         # tried again next time
        self.hub_server.refuse = {len(self.hub_server.requests) + 1: "engine_failed"}
        out = L.learn(self.whole, Hub(self.sock, version="test"), force=True, now=NOW + 60)
        self.assertEqual((out.get("skipped"), out["stopped"]), (3, None))       # twice: passed over
        self.assertNotIn(L._get(self.whole, "typing.last_id"), ("", "0"))
        self.assertIn("跳过了", L._get(self.whole, "typing.last_outcome"))

    def test_a_question_of_the_memorys_own_can_be_put_off(self):
        aid = self.whole.ask("question", "你常去的那家店叫什么？", "想记下来", key="q:shop", now="2026-10-01 10:00:00")
        Memory(self.path, tool="luoke-agent").reply(aid, "later", now="2026-10-01 11:00:00")
        self.assertEqual(self.whole.asks(status=["later"])[0]["id"], aid)
        self.assertIsNone(self.whole.ask("question", "你常去的那家店叫什么？", "想记下来", key="q:shop", now="2026-10-03 10:00:00"))
        self.assertIsNotNone(self.whole.ask("question", "你常去的那家店叫什么？", "想记下来", key="q:shop", now="2026-10-09 10:00:00"))

    def test_moved_to_the_bin_in_luoke_mails_words_is_a_habit_too(self):
        mail = Memory(self.path, tool="luoke-mail")
        for day in ("2026-10-01", "2026-10-09"):
            mail.record("user", "activity", "改成移到垃圾箱", topic="mail-override", happened_at=day,
                        data={"account": "a", "sender": "from:ads", "name": "某广告", "category": "promotion",
                              "from": "archive", "to": "ignore", "by": "user"})
        L.ruminate(self.whole, NOW)
        habit, = mail.notes(kinds=["preference"])
        self.assertEqual((habit["data"]["action"], habit["auto"]), ("trash", False))   # never by itself
        mail.close()

    def test_a_field_and_its_object_may_take_room(self):
        """An evaluation: on the 128 GB tier most topics came back empty -- the names were longer than 24 and were
        dropped whole."""
        from luoke_mem import topics as T
        long = "美国房产：关注拉克港 Harbor Hill 00427 邮编的房价"
        out = T.parse('{"interests": [{"text": "%s", "evidence": [1]}], "dislikes": []}' % long, 1)
        self.assertEqual([i["text"] for i in out["interests"]], [long])

    def test_everyday_words_and_one_segment_topics_are_not_guesses(self):
        """luoke-mem#2: an everyday word is never a term, whatever the model says; a topic on one segment waits."""
        for day in (10, 11, 12):
            self.typed("今天把文档发一下", day)
        self.assertEqual(L._term(self.whole, "文档", [], "2026-10-14 10:00:00"), "blocked")
        one = self.typed("最近在看马拉松配速", 13)
        self.assertEqual(L._interest(self.whole, "马拉松训练", [one], [], "2026-10-14 10:00:00"), "not yet")
        self.assertEqual(self.whole.notes(kinds=["interest"], status=["proposed"]), [])
        two = self.typed("配速表又改了一版", 13)
        guess = L._interest(self.whole, "马拉松训练", [two], [], "2026-10-14 10:00:00")
        self.assertEqual(len(self.whole.note(guess)["cites"]), 2)            # both segments, one day

    def test_old_guesses_go_through_what_new_ones_do(self):
        """The guesses already there are put through the new checks -- set aside, folded, not deleted;
        what the user took a stand on stays."""
        one = self.typed("最近在看马拉松配速", 13)
        a = self.whole._note("interest", "马拉松训练", "inferred", "proposed", [one])
        b = self.whole._note("term", "文档", "inferred", "proposed", [self.typed("文档发一下", 13)])
        c = self.whole._note("interest", "Homefinch", "inferred", "proposed", [self.typed("看房", 13)])
        mail = Memory(self.path, tool="luoke-mail")
        sent = lambda d: mail.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        f1 = mail._note("fact", "使用 Apple Mail", "inferred", "proposed", [sent("2026-10-01")], topic="其他")
        f2 = mail._note("fact", "邮件客户端：使用 Apple Mail 客户端", "inferred", "proposed", [sent("2026-10-09")], topic="邮件客户端")
        kept = self.whole._note("term", "今天", "inferred", "proposed", [self.typed("今天", 13)])
        self.whole.confirm(kept)
        self.assertEqual(L._tidy(self.whole, "2026-10-14 10:00:00"), 4)
        self.assertEqual([self.whole.note(n)["status"] for n in (a, b, c, f2)], ["superseded"] * 4)
        self.assertEqual((self.whole.note(f2)["superseded_by"], len(self.whole.note(f1)["cites"])), (f1, 2))
        self.assertEqual(self.whole.note(kept)["status"], "active")           # the user said so
        with self.whole._lock:
            self.assertEqual(self.whole.db.execute("SELECT COUNT(*) FROM candidates WHERE kind='interest'").fetchone()[0], 1)
        mail.close()

    def test_an_answer_cut_off_does_not_pass_its_batch_by(self):
        """An answer cut off (the output ran out) parsed as nothing and the cursor went past the
        batch, never to be read. Now the batch stays; the next run asks with half as much; twice cut off, passed."""
        for day in (10, 11, 12):
            self.typed("行云上线了", day)
        self.hub_server.answer = lambda text: '{"terms": [{"text": "行云", "evidence": [1, 2'
        out = self.run_learn(force=True)
        self.assertEqual(out["stopped"], "cut off")
        self.assertIn(L._get(self.whole, "typing.last_id"), ("", "0"))
        self.assertEqual(L.size(self.whole, "typing", 40, 6000), (20, 3000))
        self.hub_server.answer = model()
        out = L.learn(self.whole, Hub(self.sock, version="test"), force=True, now=NOW + 60)
        self.assertEqual((out["stopped"], out["terms"]), (None, ["行云"]))      # read after all
        self.assertEqual(L.size(self.whole, "typing", 40, 6000), (40, 6000))  # and back to full size

    def test_review_of_10_22(self):
        """A brand behind a field, what is in effect too, and one doing counted once."""
        from luoke_mem import topics as T
        self.assertTrue(T.brand("加密货币：Lumenfold 相关动态") and T.brand("房产：Homefinch", ["Homefinch"]))
        self.assertFalse(T.brand("以太坊：Layer2") or T.brand("美国房产：拉克港 00427 邮编的房价", ["Homefinch"]))
        taste = self.whole._note("taste", "加密货币：Lumenfold 相关动态", "inferred", "active", [self.typed("看看", 13)])
        word = self.whole._note("term", "issue", "inferred", "active", [self.typed("issue", 13)])
        mail0 = Memory(self.path, tool="luoke-mail")
        lab = lambda t: mail0.record("user", "statement", f"标签「{t}」", topic="own-label")   # noqa: E731
        labelled = mail0.propose("dislike", "银行账单", [lab("财务"), lab("收据")])
        mixed = mail0.propose("interest", "跑步", [lab("跑步"), mail0.record("user", "activity", "打开了精选：配速", topic="digest-open")])
        mail0.close()
        L._tidy(self.whole, "2026-10-14 10:00:00")
        self.assertEqual([self.whole.note(n)["status"] for n in (taste, word, labelled, mixed)],
                         ["superseded", "superseded", "superseded", "proposed"])   # a label alone says nothing
        mail = Memory(self.path, tool="luoke-mail")
        key = {"account": "a", "sender": "from:wang", "name": "王某", "category": "work", "by": "user"}
        mail.record("user", "activity", "改成移到垃圾箱", topic="mail-override", happened_at="2026-10-14 09:00:00",
                    data=dict(key, **{"from": "keep", "to": "trash"}))
        mail.record("user", "activity", "移到垃圾箱", topic="mail-acted", happened_at="2026-10-14 09:00:30",
                    data=dict(key, action="trash", count=1))
        L._habits(self.whole, "2026-10-14 10:00:00")
        self.assertEqual(mail.notes(kinds=["preference"], status=["active", "proposed"]), [])   # once is not a habit
        mail.close()

    def test_asking_a_little_more(self):
        """A term a day short is asked a little early; an institution gone quiet, or a confirmed fact the
        letters now word otherwise, is asked whether it still holds. Still at most two a day."""
        for day in (11, 12):
            self.typed("行云上线了", day)
        near = self.whole.propose("term", "行云", [r["id"] for r in L._typed(self.whole)])
        mail = Memory(self.path, tool="luoke-mail")
        quiet = mail.propose("fact", "在某券商有账户", [mail.record("third_party", "statement", "某券商来信", topic="received-mail",
                             data={"count": 40, "months": 20, "opened": 10, "last": "2026-03"})], topic="账户:某券商")
        mail.close()
        r = L.ruminate(self.whole, NOW)
        asked = {a["about_note"]: a for a in Memory(self.path, tool="luoke-agent").asks(now=LATER)}
        self.assertEqual((r["questions"], set(asked)), (2, {near, quiet}))
        self.assertIn("再多一天就会直接记下", asked[near]["why"])
        self.assertEqual(asked[quiet]["text"], "「在某券商有账户」，现在还是这样吗？")


    def test_a_messy_answer_is_kept_to_what_can_be_true(self):
        out = L.parse('好的：{"terms": [{"text": " 行 云 ", "evidence": [1, 9, "x"]}, "?"], "interests": [{"text": "", "evidence": [1]},'
                      ' {"text": "答辩准备", "evidence": [2]}, {"text": "推理优化", "evidence": [1, 3]}]}', 3)
        self.assertEqual(out, {"terms": [{"text": "行 云", "evidence": [1]}],
                               "interests": [{"text": "推理优化", "evidence": [1, 3]}]})   # one segment is not an interest
        self.assertEqual(L.parse("不是 JSON", 3), {"terms": [], "interests": []})


if __name__ == "__main__":
    unittest.main()
