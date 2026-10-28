import os
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: E402,F401  -- first: a temporary home; the real memory and hub are refused

from luoke_mem import Memory, TooNew, WholeMemory  # noqa: E402
from luoke_mem.__main__ import main as cli  # noqa: E402
from luoke_mem import memory as M  # noqa: E402


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="luoke-mem-test-")
        self.mem = Memory(os.path.join(self.dir, "mem.db"), tool="luoke-mail")

    def tearDown(self):
        self.mem.close()

    def test_the_tests_never_reach_the_real_memory(self):
        real = os.path.join(os.path.expanduser("~"), ".luoke")
        self.assertFalse(M.luoke_home().startswith(real), M.luoke_home())

    def test_the_users_own_words_are_a_statement_and_an_active_note(self):
        nid = self.mem.state("fact", "我是产品经理", now="2026-10-06 10:00:00")
        n = self.mem.note(nid)
        self.assertEqual((n["origin"], n["status"], n["decided_at"]), ("user", "active", "2026-10-06 10:00:00"))
        self.assertEqual((n["tool"], [c["tool"] for c in n["cites"]], n["cites_elsewhere"]),   # the memory's, said here
                         ("luoke-mem", ["luoke-mem"], []))                                 # and read by any tool
        whole = WholeMemory(self.mem.path)
        said, = whole.note(nid)["cites"]
        self.assertEqual((said["speaker"], said["kind"], said["tool"], said["data"]), ("user", "statement", "luoke-mem",
                                                                                        {"via": "luoke-mail"}))
        whole.close()

    def test_a_guess_must_cite_and_waits_for_the_user(self):
        with self.assertRaises(ValueError):
            self.mem.propose("interest", "本地大模型", cites=[])
        r1 = self.mem.record("user", "activity", "打开过：本地大模型周报", topic="mail-read", happened_at="2026-09-01 08:00:00")
        nid = self.mem.propose("interest", "本地大模型", cites=[r1], why="常打开这类邮件")
        self.assertEqual(self.mem.note(nid)["status"], "proposed")
        self.assertEqual(self.mem.notes(kinds=["interest"]), [])              # not active until confirmed
        self.assertEqual(self.mem.propose("interest", "本地 大模型", cites=[r1]), nid)   # not raised twice
        self.mem.confirm(nid)
        n = self.mem.note(nid)
        self.assertEqual(n["status"], "active")
        self.assertEqual([(c["kind"], c["tool"]) for c in n["cites"]], [("statement", "luoke-mem"), ("activity", "luoke-mail")])
        self.assertEqual(n["cites_elsewhere"], [])                          # the 确认 is the memory's, read by all

    def test_a_rejected_guess_does_not_come_back(self):
        r = self.mem.record("user", "activity", "打开过：交易所活动", topic="mail-read")
        nid = self.mem.propose("interest", "交易所活动", cites=[r])
        self.mem.reject(nid)
        self.assertEqual(self.mem.note(nid)["status"], "rejected")
        self.assertIsNone(self.mem.propose("interest", "交易所活动", cites=[r]))
        with self.assertRaises(ValueError):
            self.mem.confirm(nid)

    def test_revising_supersedes_and_keeps_the_old_note(self):
        old = self.mem.state("interest", "AI 编程")
        new = self.mem.revise(old, "AI 编程工具和编程智能体")
        self.assertEqual(self.mem.note(old)["status"], "superseded")
        self.assertEqual(self.mem.note(old)["superseded_by"], new)
        self.assertEqual([n["text"] for n in self.mem.notes(kinds=["interest"])], ["AI 编程工具和编程智能体"])
        whole = WholeMemory(self.mem.path)
        said = whole.note(new)["cites"][0]
        self.assertEqual((said["text"], said["ref"]), ("改为：AI 编程工具和编程智能体", str(old)))   # the old words by ref
        self.assertEqual(whole.note(new)["tool"], "luoke-mem")
        whole.close()

    def test_a_statement_can_say_what_it_is_about_and_keeps_it_when_revised(self):
        old = self.mem.state("fact", "我的网站：https://a.example", topic="site")
        new = self.mem.revise(old, "我的网站：https://b.example")
        self.assertEqual([n["topic"] for n in self.mem.notes(kinds=["fact"])], ["site"])
        self.assertEqual(self.mem.note(new)["origin"], "user")

    def test_forgetting_removes_the_chain_and_records_nothing_else_cites(self):
        shared = self.mem.record("user", "activity", "打开过：周报", topic="mail-read")
        a = self.mem.propose("interest", "A", cites=[shared])
        b = self.mem.propose("interest", "B", cites=[shared])
        self.mem.confirm(a)
        a2 = self.mem.revise(a, "A2")
        self.mem.forget(a2)
        self.assertIsNone(self.mem.note(a))
        self.assertIsNone(self.mem.note(a2))
        texts = [r["text"] for r in self.mem.records()]
        self.assertFalse(any("A2" in t or "确认：A" in t for t in texts))
        self.assertIn("打开过：周报", texts)                                 # still cited by B
        self.assertIsNotNone(self.mem.note(b))

    def test_the_whole_memory_counts_what_each_tool_wrote_each_day(self):
        ime = Memory(self.mem.path, tool="luoke-ime")
        ime.record("user", "statement", "a", now="2026-10-13 23:59:00")
        ime.record("user", "statement", "b", now="2026-10-14 00:00:00")
        self.mem.record("user", "statement", "c", now="2026-10-14 09:00:00")
        with WholeMemory(self.mem.path) as whole:
            self.assertEqual(whole.daily("2026-10-14"), [{"day": "2026-10-14", "tool": "luoke-ime", "count": 1},
                                                         {"day": "2026-10-14", "tool": "luoke-mail", "count": 1}])
            self.assertEqual([r["text"] for r in whole.records(tool="luoke-ime", since="2026-10-13", until="2026-10-14")], ["a"])
            self.assertEqual([r["text"] for r in whole.records(limit=1, offset=1)], ["b"])
        ime.close()

    def test_forget_a_tool_or_everything(self):
        self.mem.conclude("fact", "来自邮件工具", [self.mem.record("user", "statement", "你发出的邮件：「排期」", topic="sent-mail")])
        self.mem.state("fact", "我在邮件助手里亲口说的")                  # the user's words: the memory's (ADR 0009)
        other = Memory(self.mem.path, tool="luoke-notes")
        other.conclude("fact", "来自另一个工具", [other.record("user", "statement", "笔记", topic="note")])
        self.assertEqual(self.mem.forget_tool(), {"records": 1, "notes": 1})        # what it wrote itself
        self.assertEqual(sorted(n["text"] for n in self.mem.notes(confidence=["guess", "likely", "sure"])),
                         ["我在邮件助手里亲口说的", "来自另一个工具"])
        self.assertEqual(self.mem.records(), [])
        whole = WholeMemory(self.mem.path)
        self.assertEqual(sorted(r["tool"] for r in whole.records()), ["luoke-mem", "luoke-notes"])
        whole.forget_all()
        self.assertEqual(self.mem.notes(status=[]), [])
        whole.close()
        other.close()

    def test_a_tool_reads_every_record_and_all_the_evidence(self):
        """Top-level ADR 0013: the machine is the boundary; any tool reads what any tool wrote."""
        ime = Memory(self.mem.path, tool="luoke-ime")
        typed = ime.record("user", "statement", "下周去杭州出差", topic="ime-commit")
        sent = self.mem.record("user", "statement", "你发出的邮件：「杭州行程」", topic="sent-mail")
        nid = ime.conclude("fact", "常去杭州", [typed, sent])
        self.assertEqual([r["id"] for r in self.mem.records()], [sent])                   # its own, by default
        self.assertEqual([r["id"] for r in self.mem.records(tool="luoke-ime")], [typed])
        self.assertEqual(sorted(r["id"] for r in self.mem.records(tool=None)), [typed, sent])
        n = self.mem.note(nid)
        self.assertEqual((sorted(c["id"] for c in n["cites"]), n["cites_elsewhere"]), ([typed, sent], []))
        self.assertIn("杭州出差", str(n))
        whole = WholeMemory(self.mem.path)
        self.assertEqual(whole.sources(), [{"tool": "luoke-ime", "records": 1, "notes": 1},
                                           {"tool": "luoke-mail", "records": 1, "notes": 0}])
        whole.close()
        ime.close()

    def test_a_tool_does_not_clear_others(self):
        other = Memory(self.mem.path, tool="luoke-ime")
        other.record("user", "statement", "x")
        with self.assertRaises(ValueError):
            self.mem.forget_tool("luoke-ime")
        self.assertFalse(hasattr(self.mem, "forget_all"))                    # only luoke-mem's own page clears
        self.assertEqual(len(other.records()), 1)
        other.close()

    def test_two_kinds_of_time(self):
        rid = self.mem.record("user", "activity", "打开过：账单", happened_at="2025-01-02 09:00:00", now="2026-10-06 12:00:00")
        r = self.mem.records()[0]
        self.assertEqual((r["id"], r["happened_at"], r["recorded_at"]), (rid, "2025-01-02 09:00:00", "2026-10-06 12:00:00"))
        self.assertEqual(self.mem.records(since="2026-10-07 00:00:00"), [])

    def test_context_is_read_only_newest_first_and_bounded(self):
        self.mem.state("fact", "我是产品经理", now="2026-10-01 10:00:00")
        self.mem.state("fact", "家里有一个上小学的孩子", now="2026-10-02 10:00:00")
        self.mem.state("interest", "本地大模型")
        before = (len(self.mem.records()), len(self.mem.notes(status=[])))
        text = self.mem.context(["fact", "interest"])
        self.assertEqual(text, "关于用户：\n- 家里有一个上小学的孩子\n- 我是产品经理\n关注的话题：\n- 本地大模型")
        self.assertEqual((len(self.mem.records()), len(self.mem.notes(status=[]))), before)
        self.assertEqual(self.mem.context(["fact"], limit=20), "关于用户：\n- 家里有一个上小学的孩子")
        self.assertEqual(self.mem.context(["dislike"]), "")

    def test_records_keep_tool_data(self):
        self.mem.record("user", "statement", "对「X」：有用", topic="digest-feedback", data={"value": "useful"}, ref="42")
        r = self.mem.records(topic="digest-feedback", ref="42")[0]
        self.assertEqual(r["data"], {"value": "useful"})
        with self.assertRaises(ValueError):
            self.mem.record("someone", "statement", "x")

    def test_conclusions_from_the_users_own_words_take_effect(self):
        r1 = self.mem.record("user", "statement", "你发出的邮件：「周报」", topic="sent-mail", happened_at="2026-01-05 10:00:00")
        r2 = self.mem.record("user", "statement", "你发出的邮件：「排期」", topic="sent-mail", happened_at="2026-02-05 10:00:00")
        nid = self.mem.conclude("fact", "职位：产品经理", [r1, r2], topic="职位")
        n = self.mem.note(nid)
        self.assertEqual((n["origin"], n["status"], n["decided_at"], n["topic"]), ("inferred", "active", None, "职位"))
        self.assertEqual(self.mem.conclude("fact", "职位：产品经理", [r2], topic="职位"), nid)     # more evidence, same note
        r3 = self.mem.record("user", "statement", "你发出的邮件：「新岗位」", topic="sent-mail", happened_at="2026-03-01 10:00:00")
        new = self.mem.conclude("fact", "职位：产品负责人", [r3], topic="职位")                  # the job changed: once is a guess
        self.assertEqual((self.mem.note(new)["status"], self.mem.note(nid)["status"]), ("proposed", "active"))
        r4 = self.mem.record("user", "statement", "你发出的邮件：「交接」", topic="sent-mail", happened_at="2026-03-10 10:00:00")
        self.assertEqual(self.mem.conclude("fact", "职位：产品负责人", [r4], topic="职位"), new)   # a week on: it holds
        self.assertEqual((self.mem.note(nid)["status"], self.mem.note(nid)["superseded_by"]), ("superseded", new))
        self.mem.reject(new)
        self.assertIsNone(self.mem.conclude("fact", "职位：产品负责人", [r3], topic="职位"))

    def test_a_guess_the_users_own_words_bear_out_takes_effect(self):
        typed = [self.mem.record("user", "statement", f"行云 {d}", topic="ime-commit", happened_at=f"2026-10-0{d} 10:00:00")
                 for d in (1, 2, 3)]
        guess = self.mem.propose("term", "行云", typed[:1], why="出现 2 次，先问你")
        self.assertEqual(self.mem.conclude("term", "行云", typed[1:2]), guess)
        self.assertEqual(self.mem.note(guess)["status"], "proposed")          # two days: not yet (GRADES)
        self.assertEqual(self.mem.conclude("term", "行云", typed[2:], why="在 3 天的打字里出现 5 次"), guess)
        n = self.mem.note(guess)
        self.assertEqual((n["status"], n["decided_at"], n["why"], len(n["cites"])), ("active", None, "在 3 天的打字里出现 5 次", 3))
        ill = self.mem.propose("fact", "在复诊", [self.mem.record("user", "statement", "去医院复诊", sensitive=["health"])])
        self.mem.conclude("fact", "在复诊", [self.mem.record("user", "statement", "复诊改到周三")])
        status = self.mem.db.execute("SELECT status FROM notes WHERE id=?", (ill,)).fetchone()[0]
        self.assertEqual(status, "proposed")                                 # sensitive: only the user says so (ADR 0004)

    def test_what_others_said_never_takes_effect_by_itself(self):
        theirs = self.mem.record("third_party", "statement", "某银行发来：「信用卡账单」", topic="received-mail")
        with self.assertRaises(ValueError):
            self.mem.conclude("fact", "在某银行有信用卡", [theirs])
        nid = self.mem.propose("fact", "在某银行有信用卡", [theirs], topic="账户:某银行")
        self.assertEqual(self.mem.note(nid)["status"], "proposed")    # one letter: not in effect, and not asked
        more = self.mem.record("third_party", "statement", "某银行发来：「还款提醒」", topic="received-mail")
        self.assertEqual(self.mem.propose("fact", "在某银行有信用卡", [more]), nid)
        self.assertEqual(len(self.mem.note(nid)["cites"]), 2)

    def test_an_old_database_gains_topics(self):
        path = os.path.join(self.dir, "old.db")
        db = sqlite3.connect(path)
        db.executescript(M.SCHEMA.replace("why TEXT, topic TEXT", "why TEXT"))
        db.close()
        m = Memory(path)
        self.assertIsNotNone(m.state("fact", "x"))
        self.assertEqual(m.db.execute("PRAGMA user_version").fetchone()[0], M.SCHEMA_VERSION)
        m.close()

    def test_a_newer_file_is_used_as_it_is(self):
        """Another tool carries a newer luoke-mem: it added a column and a kind this one does not know."""
        path = os.path.join(self.dir, "newer.db")
        Memory(path).close()
        db = sqlite3.connect(path)
        db.execute("ALTER TABLE records ADD COLUMN later TEXT")
        db.execute("INSERT INTO notes (tool, kind, text, origin, status, formed_at) VALUES"
                   " ('luoke-later', 'mood', '心情不错', 'inferred', 'proposed', '2027-01-01 00:00:00')")
        db.execute(f"PRAGMA user_version = {M.SCHEMA_VERSION + 5}")
        db.commit()
        db.close()
        m = Memory(path, tool="luoke-mail")
        rid = m.record("user", "statement", "x")
        self.assertIsNone(m.records()[0]["later"])
        self.assertEqual(m.records()[0]["id"], rid)
        self.assertEqual([n["kind"] for n in m.notes(status=["proposed"])], ["mood"])
        self.assertEqual(m.context(["fact", "interest"]), "")
        self.assertEqual(m.db.execute("PRAGMA user_version").fetchone()[0], M.SCHEMA_VERSION + 5)   # never lowered
        m.close()
        db = sqlite3.connect(path)
        db.execute(f"PRAGMA user_version = {(M.PROTECTED + 1) * 1000}")   # a change this library cannot live with
        db.commit()
        db.close()
        with self.assertRaises(TooNew):
            Memory(path)

    def test_two_tools_open_a_fresh_file_at_once(self):
        path = os.path.join(self.dir, "fresh.db")
        a, b = Memory(path, tool="luoke-mail"), Memory(path, tool="luoke-ime")
        a.record("user", "statement", "a")
        b.record("user", "statement", "b")
        self.assertEqual([r["text"] for r in a.records() + b.records()], ["a", "b"])
        a.close()
        b.close()

    def test_default_place_is_private(self):
        home = tempfile.mkdtemp(prefix="luoke-home-")
        before = os.environ["LUOKE_HOME"]
        os.environ["LUOKE_HOME"] = os.path.join(home, "luoke")
        try:
            m = Memory()
            m.record("user", "statement", "x")         # the -wal and -shm exist now
            self.assertEqual(m.path, os.path.join(home, "luoke", "mem.db"))
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(home, "luoke")).st_mode), 0o700)
            for p in (m.path, m.path + "-wal", m.path + "-shm"):
                self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
            m.close()
        finally:
            os.environ["LUOKE_HOME"] = before
        with mock.patch.dict(os.environ):                         # only the name: nothing is opened there
            del os.environ["LUOKE_HOME"]
            self.assertTrue(M.default_path().endswith(os.path.join(".luoke", "mem.db")))

    def test_forgotten_is_gone_from_the_file_too(self):
        """ARCHITECTURE §4: deleted with what it rests on -- not left in the database's free pages or its log."""
        marker = "一段只出现一次的话-7f3c9a"
        for forget in ("note", "records", "tool", "all"):
            path = os.path.join(tempfile.mkdtemp(prefix="luoke-wipe-"), "mem.db")
            mem, whole = Memory(path, tool="luoke-mail"), WholeMemory(path)
            for i in range(40):                                        # enough to fill pages that then fall free
                mem.record("user", "statement", f"填充 {i} " + "x" * 200, topic="sent-mail")
            nid = mem.conclude("fact", marker, [mem.record("user", "statement", marker, topic="sent-mail")])
            {"note": lambda: mem.forget(nid), "records": lambda: mem.forget_records(topics=["sent-mail"]),
             "tool": lambda: whole.forget_tool("luoke-mail"), "all": whole.forget_all}[forget]()
            mem.close()
            whole.close()
            for p in (path, path + "-wal"):
                if os.path.exists(p):
                    with open(p, "rb") as f:
                        self.assertNotIn(marker.encode("utf-8"), f.read(), (forget, p))

    def test_forgetting_a_note_lets_go_of_what_pointed_at_its_records(self):
        """Ids come back (no AUTOINCREMENT): a candidate or an answer left pointing at a deleted record would one
        day point at another tool's new record (review, 2026-10-16)."""
        from luoke_mem import learn as L
        ime, whole = Memory(self.mem.path, tool="luoke-ime"), WholeMemory(self.mem.path)
        typed = ime.record("user", "statement", "推理太慢", topic="ime-commit")
        nid = ime.conclude("term", "推理", [typed])
        L._candidate(whole, "interest", "本地推理", [typed], "2026-10-16 10:00:00")
        whole.forget(nid)
        self.assertEqual(whole.db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            whole.forget_tool()                                  # the memory's own: not in one go (ADR 0009)
        with self.assertRaises(ValueError):
            whole.forget_tool("luoke-mem")
        whole.close()
        ime.close()

    def test_a_symbolic_link_beside_the_memory_is_never_followed(self):
        """A component in the input method's sandbox could make -wal a link (luoke-ime review, 2026-10-16)."""
        folder = tempfile.mkdtemp(prefix="luoke-links-")
        target = os.path.join(folder, "zshenv")
        with open(target, "w") as f:
            f.write("export PATH=x\n")
        os.chmod(target, 0o644)
        path = os.path.join(folder, "mem.db")
        Memory(path).close()
        for side in ("-wal", "-shm", "-journal", ""):
            p = path + side
            if os.path.exists(p):
                os.remove(p)
            os.symlink(target, p)
            with self.assertRaises(RuntimeError):
                Memory(path)
            os.remove(p)
        self.assertEqual((open(target).read(), stat.S_IMODE(os.stat(target).st_mode)), ("export PATH=x\n", 0o644))
        link = os.path.join(folder, "link")
        os.symlink(target, link)
        M._private(link, 0o600)                                           # never the mode of what it points at
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o644)

    def test_facts_from_others_take_effect_by_their_evidence(self):
        """One rule for what others say -- how often, over how long, opened, lately -- whatever it is
        about, money included; health and the like wait for the user's word. Never asked about."""
        def mails(count, months, opened, last, n=1):
            return [self.mem.record("third_party", "statement", f"某商城来信 {i}", topic="received-mail",
                                    data={"count": count, "months": months, "opened": opened, "last": last})
                    for i in range(n)]
        now = "2026-10-17 10:00:00"
        strong = mails(12, 5, 4, "2026-09")
        nid = self.mem.propose("fact", "用某邮箱服务", strong, about="service", why="根据你收到的邮件", now=now)
        self.assertEqual((self.mem.note(nid)["status"], self.mem.note(nid)["decided_at"], self.mem.note(nid)["data"]),
                         ("active", None, {"about": "service"}))
        for text, cites in (("在某银行有账户", mails(12, 5, 4, "2026-09")), ("在某交易所有数字货币账户", mails(12, 5, 4, "2026-09")),
                            ("在某学校上学", mails(12, 5, 4, "2026-09"))):
            self.assertEqual(self.mem.note(self.mem.propose("fact", text, cites, now=now))["status"], "active", text)
        for text, cites in (("在某书店有会员", mails(3, 1, 0, "2026-09")),          # too little
                            ("在某航司有会员", mails(12, 5, 4, "2025-01")),          # long ago
                            ("在某医院看病", mails(12, 5, 4, "2026-09"))):           # health: the user's word
            self.assertEqual(self.mem.note(self.mem.propose("fact", text, cites, now=now))["status"], "proposed", text)
        waiting = self.mem.propose("fact", "常坐某航司的航班", mails(2, 1, 1, "2026-09"), now=now)
        later = "2026-10-20 10:00:00"
        self.assertEqual(self.mem.propose("fact", "常坐某航司的航班", mails(9, 4, 3, "2026-10"), now=later), waiting)
        self.assertEqual(self.mem.note(waiting)["status"], "active")                 # more evidence: it takes effect
        self.assertEqual({n["source"] for n in self.mem.recent(since="2026-10-17 00:00:00")}, {"根据你收到的邮件"})
        self.mem.reject(nid)
        self.assertIsNone(self.mem.propose("fact", "用某邮箱服务", strong, now=later))   # stays struck
        from luoke_mem import KIND_PURPOSES
        self.assertEqual(set(KIND_PURPOSES), set(M.NOTE_KINDS))

    def test_facts_from_others_are_never_asked_and_real_guesses_say_why(self):
        """ADR 0011 amendment: 观察中 instead of a question; the cards keep real guesses,
        and why they are asked is a reason, not a source."""
        letter = self.mem.record("third_party", "statement", "某大学发来：「校友通讯」", topic="received-mail")
        school = self.mem.propose("fact", "曾就读于某大学", [letter], topic="学校")
        own = self.mem.record("user", "statement", "你改过的建议：某商城的邮件改成归档", topic="override")
        habit = self.mem.propose("preference", "某商城的邮件通常要归档", [own])
        self.assertEqual(sorted(n["id"] for n in self.mem.notes(status=["proposed"])), sorted([habit, school]))
        self.assertEqual(self.mem.notes(), [])                                                # neither in effect
        why = M.ask_reason(self.mem.note(habit))
        self.assertTrue(why.startswith("你几次都这样处理") and "确认后" in why and "邮件" in why, why)
        self.mem.confirm(school)                                                             # one click on the page
        self.assertEqual(self.mem.note(school)["status"], "active")
        stray = self.mem._note("fact", "在某商城有会员", "inferred", "proposed", [letter])
        WholeMemory(self.mem.path).close()
        self.assertEqual(self.mem.note(stray)["status"], "proposed")        # a guess, not asked (0.23: no 观察中)

    def test_a_schema_4_memory_moves_its_waiting_facts_from_others(self):
        path = os.path.join(tempfile.mkdtemp(prefix="luoke-s4-"), "mem.db")
        m = Memory(path, tool="luoke-mail")
        letter = m.record("third_party", "statement", "某航司来信", topic="received-mail")
        nid = m._note("fact", "常坐某航司的航班", "inferred", "proposed", [letter])
        m.db.execute("PRAGMA user_version = 4")
        m.db.commit()
        m.close()
        m = Memory(path, tool="luoke-mail")                                              # upgrades to 5
        self.assertEqual((m.note(nid)["status"], m.db.execute("PRAGMA user_version").fetchone()[0] % 1000),
                         ("observed", M.SCHEMA_VERSION))
        m.close()

    def test_three_degrees_of_certainty(self):
        """ADR 0012: a guess is not used; likely is in effect without the user's word; sure is what the user said
        or confirmed. What tools read by default is likely and up."""
        own = [self.mem.record("user", "statement", "你发出的邮件：「排期」", topic="sent-mail", happened_at=at)
               for at in ("2026-10-01 10:00", "2026-10-09 10:00")]
        likely = self.mem.conclude("fact", "在做排期", own)
        sure = self.mem.state("fact", "我是产品经理")
        guess = self.mem.propose("preference", "某商城的邮件通常要归档",
                                 [self.mem.record("user", "statement", "改过的建议", topic="override")])
        self.assertEqual({n["id"]: n["confidence"] for n in self.mem.notes()}, {likely: "likely", sure: "sure"})
        self.assertEqual([n["id"] for n in self.mem.notes(confidence=["guess"])], [guess])
        self.assertEqual(self.mem.note(guess)["confidence"], "guess")
        self.mem.confirm(likely)
        self.assertEqual(self.mem.note(likely)["confidence"], "sure")
        self.assertEqual(M.CONFIDENCE_NAMES, {"guess": "猜测", "likely": "比较确定", "sure": "确定"})

    def test_the_agents_list_one_reply_settles_it(self):
        """ADR 0012: questions and suggestions with a real reason, evidence, rank; the user's reply settles the
        note, closes the question, and what they waved off is not raised again for a while."""
        whole = WholeMemory(self.mem.path)
        agent = Memory(self.mem.path, tool="luoke-agent")
        ov = lambda: self.mem.record("user", "statement", "改过的建议", topic="override")
        a, b, c = (self.mem.propose("preference", t, [ov()]) for t in ("甲类邮件要归档", "乙类邮件要归档", "丙类邮件要归档"))
        qa = whole.ask("question", "甲？", "理由甲", about_note=a, rank=1, key=f"note:{a}",
                       answer={"type": "choice", "options": ["对", "不对", "以后再说"]})
        qb = whole.ask("question", "乙？", "理由乙", about_note=b, rank=5, key=f"note:{b}",
                       answer={"type": "choice", "options": ["对", "不对", "以后再说"]})
        sg = whole.ask("suggestion", "退订某商城？", "你一直不看", evidence="你清掉了 3 次", rank=3, key="unsubscribe:x")
        self.assertEqual([x["id"] for x in agent.asks()], [qb, sg, qa])                  # by rank
        self.assertEqual((agent.asks()[1]["evidence"], agent.asks()[1]["action"]), ("你清掉了 3 次", None))
        agent.reply(qb, "yes")
        self.assertEqual(self.mem.note(b)["confidence"], "sure")
        agent.reply(qa, "revise", "甲类邮件要删")
        self.assertEqual(self.mem.note(a)["status"], "superseded")
        agent.reply(sg, "never")
        self.assertEqual(agent.asks(), [])
        self.assertIsNone(whole.ask("suggestion", "退订某商城？（换个说法）", "x", key="unsubscribe:x"))   # never again
        qc = whole.ask("question", "丙？", "理由丙", about_note=c, key=f"note:{c}")
        agent.reply(qc, "later")
        self.assertEqual((self.mem.note(c)["status"], agent.asks()), ("proposed", []))
        self.assertIsNone(whole.ask("question", "丙？？", "x", about_note=c, key=f"note:{c}"))       # put off: waits
        self.mem.reject(c)                                                       # decided on the page instead
        self.assertIn(c, [x["about_note"] for x in whole.asks(status=["answered"])])
        whole.close()
        agent.close()

    def test_the_names_shown_to_the_user_are_one_copy(self):
        from luoke_mem import KIND_NAMES, TOOL_NAMES, ui
        self.assertEqual(set(KIND_NAMES), set(M.NOTE_KINDS))
        self.assertEqual((KIND_NAMES["fact"], TOOL_NAMES["luoke-agent"]), ("关于你", "螺壳"))
        self.assertIs(ui.KINDS, KIND_NAMES)                                     # the page uses the same
        self.assertIs(ui.TOOLS, TOOL_NAMES)

    def test_under_test_the_real_memory_and_hub_are_refused(self):
        """tests/_isolate.py: whatever a test passes or falls back to, never ~/.luoke, never the real hub."""
        home = tempfile.mkdtemp(prefix="luoke-home-")
        with mock.patch.dict(os.environ, {"HOME": home}):         # ~ is a temporary folder: a failing guard harms nothing
            del os.environ["LUOKE_HOME"]
            for p in (None, os.path.join(home, ".luoke", "mem.db"), os.path.join(home, ".luoke", "x", "..", "other.db")):
                with self.assertRaises(RuntimeError):
                    Memory(p)
            self.assertFalse(os.path.exists(os.path.join(home, ".luoke")))
        from luoke_mem import hub
        self.assertIsNone(hub.socket_path())                     # nothing named: no hub at all
        self.assertIsNone(hub.socket_path(hub.DEFAULT_SOCKET))   # the real one, even named: refused
        self.assertIsNone(hub.socket_path(hub.LAB_SOCKET))

    def test_opens_in_a_sandbox_that_cannot_change_the_folder(self):
        """The input method's personal component may write the memory file and its -wal, -shm, -journal only."""
        home = os.path.join(tempfile.mkdtemp(prefix="luoke-home-"), "luoke")
        before = os.environ["LUOKE_HOME"]
        os.environ["LUOKE_HOME"] = home
        try:
            Memory().close()
            os.chmod(home, 0o755)                      # say the input method made ~/.luoke before anyone else
            real = os.chmod

            def sandboxed(path, mode):
                if os.path.isdir(path):
                    raise PermissionError(1, "Operation not permitted", path)
                real(path, mode)
            with mock.patch("os.chmod", sandboxed):
                m = Memory(tool="luoke-ime")
                m.record("user", "statement", "你好", topic="ime-commit")
                m.close()
            os.chmod(home, 0o700)
            with mock.patch("os.chmod", side_effect=AssertionError("nothing to change")):
                Memory(tool="luoke-ime").close()      # already private: not even tried
        finally:
            os.environ["LUOKE_HOME"] = before

    # ---- the input method: a lot of the user's own words, and forgetting part of them -------------------------
    def test_terms_are_the_users_wording(self):
        nid = self.mem.state("term", "行云流水")
        self.assertEqual(self.mem.context(["term"]), "常用说法：\n- 行云流水")
        self.assertEqual(self.mem.note(nid)["status"], "active")

    def _typed(self, ime, text, app, at):
        return ime.record("user", "statement", text, topic="ime-commit", data={"app": app, "source": "xiaohe"},
                          happened_at=at, now=at)

    def test_forget_what_was_typed_in_an_app_or_a_time(self):
        ime = Memory(self.mem.path, tool="luoke-ime")
        w1 = self._typed(ime, "行云今天上线", "com.tencent.xinWeChat", "2026-10-09 10:00:00")
        w2 = self._typed(ime, "行云的文档", "com.tencent.xinWeChat", "2026-10-10 10:00:00")
        n1 = self._typed(ime, "行云的接口", "com.apple.Notes", "2026-10-10 11:00:00")
        only_wechat = ime.conclude("term", "行云", [w1, w2])
        both = ime.conclude("term", "行云的", [w2, n1])
        mail = self.mem.record("user", "statement", "你发出的邮件：「行云」", topic="sent-mail")
        self.assertEqual(ime.forget_records(topics=["ime-commit"], match={"app": "com.tencent.xinWeChat"}), 2)
        self.assertEqual([r["text"] for r in ime.records(topic="ime-commit")], ["行云的接口"])
        self.assertIsNone(ime.note(only_wechat))                     # rested only on what was forgotten
        self.assertEqual([c["id"] for c in ime.note(both)["cites"]], [n1])
        self.assertEqual(self.mem.records(topic="sent-mail")[0]["id"], mail)    # only its own records
        self.assertEqual(ime.forget_records(since="2026-10-10 00:00:00", until="2026-10-11 00:00:00"), 1)
        self.assertEqual(ime.records(topic="ime-commit"), [])
        self.assertIsNone(ime.note(both))
        ime.close()

    def test_a_retention_period_keeps_what_understanding_rests_on(self):
        ime = Memory(self.mem.path, tool="luoke-ime")
        old = [self._typed(ime, f"第 {i} 段", "com.apple.Notes", f"2026-0{i}-01 09:00:00") for i in (1, 2, 3)]
        recent = self._typed(ime, "最近的", "com.apple.Notes", "2026-10-01 09:00:00")
        nid = ime.propose("term", "第 1 段", [old[0]])
        ime.confirm(nid)
        self.assertEqual(ime.forget_records(topics=["ime-commit"], until="2026-07-01 00:00:00", keep_cited=True), 2)
        self.assertEqual([r["id"] for r in ime.records(topic="ime-commit")], [recent, old[0]])
        self.assertEqual(ime.note(nid)["status"], "active")
        ime.close()

    def test_forgetting_a_tool_takes_what_rested_only_on_it(self):
        ime = Memory(self.mem.path, tool="luoke-ime")
        typed = self._typed(ime, "下周去杭州", "com.apple.Notes", "2026-10-10 09:00:00")
        sent = self.mem.record("user", "statement", "你发出的邮件：「杭州行程」", topic="sent-mail")
        alone = self.mem.conclude("fact", "下周去杭州", [typed])
        mixed = self.mem.conclude("fact", "常去杭州", [typed, sent])
        ime.conclude("term", "行云", [self._typed(ime, "行云上线了", "com.apple.Notes", "2026-10-11 09:00:00")])
        whole = WholeMemory(self.mem.path)
        preview = whole.forget_tool("luoke-ime", dry_run=True)
        self.assertEqual(preview, {"records": 2, "notes": 2})          # its term, and the fact resting on it alone
        self.assertIsNotNone(self.mem.note(alone))                      # a dry run deletes nothing
        self.assertEqual(whole.forget_tool("luoke-ime"), preview)
        self.assertIsNone(self.mem.note(alone))
        self.assertEqual([c["id"] for c in self.mem.note(mixed)["cites"]], [sent])
        self.assertEqual(self.mem.note(mixed)["cites_elsewhere"], [])
        whole.close()
        ime.close()

    def test_what_the_user_said_through_a_tool_stays_when_it_is_forgotten(self):
        """Top-level ADR 0009: the user's words about understanding are the memory's, said through whichever tool;
        forgetting that tool keeps them. Forgetting the tool whose understanding it was takes them with it."""
        old = self.mem.conclude("fact", "职位：产品经理", [self.mem.record("user", "statement", "你发出的邮件：「排期」",
                                                                    topic="sent-mail")], topic="职位")
        guess = self.mem.propose("fact", "在某云有账户", [self.mem.record("third_party", "statement", "某云来信",
                                                                       topic="received-mail")])
        agent = Memory(self.mem.path, tool="luoke-agent")
        agent.confirm(guess)
        agent.later(guess)
        new = agent.revise(old, "职位：产品负责人")
        whole = WholeMemory(self.mem.path)
        q = whole.ask("question", "某云的账户还在用吗？", "x", about_note=guess)
        agent.answer(q, "在用")
        self.assertEqual(whole.forget_tool("luoke-agent"), {"records": 0, "notes": 0})   # it wrote nothing of its own
        self.assertEqual(sorted(n["text"] for n in self.mem.notes(kinds=["fact"])), ["在某云有账户", "职位：产品负责人"])
        self.assertEqual((whole.note(old)["superseded_by"], whole.note(new)["tool"]), (new, "luoke-mem"))
        said = whole.records(tool="luoke-mem")
        self.assertEqual({r["topic"] for r in said}, {"note-decision", "note-later", "ask-answer"})
        self.assertEqual({r["data"]["via"] for r in said}, {"luoke-agent"})
        self.assertEqual(whole.forget_tool("luoke-mail"), {"records": 2, "notes": 1})   # its 2 letters, the old 职位;
        self.assertEqual(whole.note(guess)["tool"], "luoke-mem")          # the confirmed one is the user's and stays
        self.assertEqual([r["text"] for r in whole.records(tool="luoke-mem") if r["text"].startswith("改为")], ["改为：职位：产品负责人"])
        self.assertFalse([r for r in whole.records() if "产品经理" in r["text"]])   # the forgotten words are gone
        self.assertEqual(sorted(n["text"] for n in self.mem.notes(kinds=["fact"])), ["在某云有账户", "职位：产品负责人"])
        self.assertEqual([a["id"] for a in whole.asks(status=["answered"])], [q])   # said about a kept note: stays
        whole.close()
        agent.close()

    def test_what_the_user_took_a_stand_on_stays_when_its_tool_is_forgotten(self):
        """ADR 0009 amendment: confirmed or rejected is the user's own; guesses the user never
        answered go with the tool."""
        bill = lambda: self.mem.record("third_party", "statement", "某云来信", topic="received-mail")
        yes, no, open_ = (self.mem.propose("fact", t, [bill()]) for t in ("在某云有账户", "在某银行有账户", "在某商城有会员"))
        self.mem.confirm(yes)
        self.mem.reject(no)
        old = self.mem._insert("luoke-mail", "user", "statement", "确认：旧版库记在邮件名下的", topic="note-decision")
        mine = self.mem._note("fact", "旧版库记在邮件名下的", "inferred", "active", [bill(), old], decided=True)
        whole = WholeMemory(self.mem.path)
        self.assertEqual(whole.forget_tool("luoke-mail", dry_run=True), {"records": 4, "notes": 1})   # 4 letters, 1 guess
        self.assertEqual(whole.forget_tool("luoke-mail"), {"records": 4, "notes": 1})
        self.assertEqual({n["id"]: (n["status"], n["tool"]) for n in whole.notes(status=["active", "rejected"])},
                         {yes: ("active", "luoke-mem"), no: ("rejected", "luoke-mem"), mine: ("active", "luoke-mem")})
        self.assertIsNone(whole.note(open_))
        said = [r for r in whole.records(tool="luoke-mem") if r["id"] == old][0]
        self.assertEqual(said["data"], {"via": "luoke-mail"})                     # the older one, moved to the memory
        self.assertIsNone(self.mem.propose("fact", "在某银行有账户", [bill()]))      # the user's no still holds
        whole.close()

    def test_a_confirmed_note_of_a_forgotten_tool_keeps_the_users_yes(self):
        theirs = self.mem.record("third_party", "statement", "某银行发来：账单", topic="received-mail")
        nid = self.mem.propose("fact", "在某银行有账户", [theirs])
        whole = WholeMemory(self.mem.path)
        whole.confirm(nid)                                              # the user said yes on luoke-mem's page
        preview = whole.forget_tool("luoke-mail", dry_run=True)
        self.assertEqual(preview, {"records": 1, "notes": 0})          # the letter goes; the confirmed note does not
        self.assertEqual(whole.forget_tool("luoke-mail"), preview)
        self.assertEqual([c["text"] for c in whole.note(nid)["cites"]], ["确认：在某银行有账户"])   # the yes, its evidence
        whole.close()

    def test_forgetting_a_tool_leaves_nothing_pointing_at_its_notes(self):
        """Ids can come back once deleted: a note left pointing at one could later be taken for its history."""
        def sent(mem, text):
            return [mem.record("user", "statement", text, topic="sent-mail", happened_at=at) for at in ("2026-10-01", "2026-10-09")]
        old = self.mem.conclude("fact", "职位：产品经理", sent(self.mem, "署名：产品经理"), topic="职位")
        ime = Memory(self.mem.path, tool="luoke-ime")
        new = ime.conclude("fact", "职位：产品负责人", sent(ime, "我是产品负责人"), topic="职位")
        self.assertEqual(self.mem.note(old)["superseded_by"], new)
        ime.forget_tool()
        self.assertEqual((self.mem.note(old)["status"], self.mem.note(old)["superseded_by"]), ("superseded", None))
        ime.close()

    def test_an_understanding_cites_only_records_in_the_memory(self):
        mine = self.mem.record("user", "statement", "你发出的邮件：「周报」", topic="sent-mail")
        with self.assertRaises(ValueError):
            self.mem.conclude("fact", "x", [mine, 99999])
        with self.assertRaises(ValueError):
            self.mem.propose("fact", "x", [99999])
        nid = self.mem.conclude("fact", "x", [mine])
        with self.assertRaises(ValueError):
            self.mem.add_cites(nid, [99999])

    # ---- sensitive context: marked, never hidden (top-level ADR 0013) ------------------------------------------
    def test_every_tool_reads_everything_sensitive_or_not(self):
        ime = Memory(self.mem.path, tool="luoke-ime")
        typed = ime.record("user", "statement", "下周三去医院复诊", topic="ime-commit", sensitive=["health"])
        plain = ime.record("user", "statement", "下周三开会", topic="ime-commit")
        term = ime.conclude("term", "复诊", [typed])
        self.assertEqual(sorted(r["id"] for r in ime.records()), sorted([typed, plain]))
        self.assertEqual(self.mem.note(term)["sensitive"], "health")          # luoke-mail reads it too
        self.assertEqual([c["id"] for c in self.mem.note(term)["cites"]], [typed])
        self.assertEqual((ime.allowed(), ime.ask("health"), ime.withheld()), (sorted(M.SENSITIVE_KINDS), "granted", 0))
        self.assertEqual(self.mem.note(term)["status"], "proposed")           # health: waits for the user's word
        ime.close()

    def test_only_health_legal_and_belief_wait_for_the_users_word(self):
        """Money is not among them any more."""
        sent = lambda t, d, k=None: self.mem.record("user", "statement", t, topic="sent-mail", happened_at=d, sensitive=k)   # noqa: E731
        ill = self.mem.conclude("fact", "在做慢病管理", [sent("请假去体检", "2026-10-01", ["health"]),
                                                      sent("复诊", "2026-10-09", ["health"])])
        self.assertEqual(self.mem.note(ill)["status"], "proposed")
        pay = self.mem.conclude("fact", "常处理报销", [sent("报销 3200 元", "2026-10-01", ["finance"]),
                                                    sent("报销单", "2026-10-09")])
        self.assertEqual(self.mem.note(pay)["status"], "active")
        self.mem.confirm(ill)
        n = self.mem.note(ill)
        self.assertEqual((n["status"], n["sensitive"]), ("active", "health"))
        said = self.mem.state("fact", "对青霉素过敏", sensitive=["health"])
        self.assertEqual(self.mem.note(said)["status"], "active")             # the user's own words take effect
        with self.assertRaises(ValueError):
            self.mem.record("user", "statement", "x", sensitive=["secrets"])

    def test_credentials_are_masked_whatever_the_tool_writes(self):
        rid = self.mem.record("third_party", "statement", "验证码 482913，5 分钟内有效", topic="received-mail",
                              data={"mails": [{"subject": "登录", "text": "你的验证码是 482913"}]})
        r, = self.mem.records()
        self.assertEqual((r["id"], r["text"], r["data"]["mails"][0]["text"]), (rid, "验证码 〔验证码〕，5 分钟内有效", "你的验证码是 〔验证码〕"))
        self.assertNotIn("482913", str(self.mem.db.execute("SELECT * FROM records").fetchall()))

    def test_files_once_moved_for_sensitive_context_still_open(self):
        version = lambda: self.mem.db.execute("PRAGMA user_version").fetchone()[0]  # noqa: E731
        self.mem.record("user", "statement", "下周复诊", sensitive=["health"])
        self.assertEqual(version(), M.SCHEMA_VERSION)                            # not moved any more
        self.mem.db.execute(f"PRAGMA user_version = {M.PROTECTED * 1000 + M.SCHEMA_VERSION}")
        Memory(self.mem.path).close()                                            # moved before 0.23: opens

    # ---- what the memory would like to ask (top-level ADR 0006, luoke-agent asks) ---------------------------
    def test_put_off_is_a_receipt_not_a_no(self):
        theirs = self.mem.record("third_party", "statement", "某银行发来：账单", topic="received-mail")
        nid = self.mem.propose("fact", "在某银行有账户", [theirs])
        agent = Memory(self.mem.path, tool="luoke-agent")
        rid = agent.later(nid)
        self.assertEqual(self.mem.note(nid)["status"], "proposed")
        whole = WholeMemory(self.mem.path)
        later, = whole.records(topic="note-later")
        self.assertEqual((later["id"], later["kind"], later["ref"], later["tool"], later["data"]),
                         (rid, "receipt", str(nid), "luoke-mem", {"via": "luoke-agent"}))
        self.mem.forget(nid)
        self.assertEqual(whole.records(topic="note-later"), [])             # forgotten with the note
        whole.close()
        agent.close()

    def test_questions_wait_in_the_memory_for_the_agent(self):
        whole = WholeMemory(self.mem.path)
        r = self.mem.record("user", "statement", "你发出的邮件：「行云」", topic="sent-mail")
        nid = self.mem.conclude("fact", "在做行云项目", [r])
        q = whole.ask("question", "「行云」是一个项目，还是一个人？", "你常打「行云」，弄清了输入法和邮件都更准",
                      answer={"type": "choice", "options": ["项目", "人", "别的"]}, about_note=nid, urgency="low")
        self.assertEqual(whole.ask("question", "「行云」是一个项目，还是一个人？", "again"), q)    # not twice
        whole.ask("suggestion", "过期的建议", "x", expires_at="2026-01-01 00:00:00")
        whole.ask("question", "复诊要提醒吗？", "y", about_note=self.mem.state("fact", "每月复诊", sensitive=["health"]))
        agent = Memory(self.mem.path, tool="luoke-agent")
        open_ = agent.asks(now="2026-10-15 09:00:00")
        self.assertEqual([a["text"] for a in open_], ["复诊要提醒吗？", "「行云」是一个项目，还是一个人？"])   # not expired
        self.assertEqual([a["answer"] for a in open_ if a["id"] == q][0], {"type": "choice", "options": ["项目", "人", "别的"]})
        rid = agent.answer(q, "项目")
        self.assertIn("答：项目", whole.records(topic="ask-answer")[0]["text"])
        self.assertEqual(agent.records(topic="ask-answer"), [])             # the memory's, not the agent's (ADR 0009)
        self.assertEqual(whole.asks(status=["answered"])[0]["answer_record"], rid)
        self.assertNotIn(q, [a["id"] for a in agent.asks()])
        with self.assertRaises(ValueError):
            agent.answer(q, "人")                                              # closed
        s2 = whole.ask("suggestion", "把「周会纪要」设成快捷短语？", "每周三都打")
        agent.dismiss(s2)
        self.assertEqual(whole.asks(status=["dismissed"])[0]["id"], s2)
        whole.forget(nid)                                                     # the note goes, its questions too
        self.assertEqual([a["id"] for a in whole.asks(status=["answered"])], [])
        self.assertEqual(agent.records(topic="ask-answer"), [])
        agent.close()
        whole.close()

    def test_what_records_can_tell(self):
        """ADR 0012 amendment (DESIGN「什么记录能推出什么理解」): a label the user made says they keep things in order,
        never what they like or not; mails they sent bear a fact out when two are a week apart; a tool's own taste
        is apart from what is known of the user."""
        def rec(topic, *days):
            return [self.mem.record("user", "statement", f"{topic} {d}", topic=topic, happened_at=f"2026-10-{d:02d} 10:00:00")
                    for d in days]
        for kind in ("interest", "dislike"):
            self.assertEqual(self.mem.note(self.mem.conclude(kind, f"财务 {kind}", rec("own-label", 1, 20)))["confidence"], "guess")
        self.assertEqual(self.mem.note(self.mem.conclude("fact", "单位：某公司", rec("sent-mail", 1, 4)))["confidence"], "guess")
        self.assertEqual(self.mem.note(self.mem.conclude("fact", "居住地：杭州", rec("sent-mail", 1, 9)))["confidence"], "likely")
        self.assertEqual(self.mem.note(self.mem.conclude("taste", "加密货币周报", rec("own-label", 1)))["confidence"], "likely")
        self.assertEqual(self.mem.note(self.mem.conclude("interest", "跑步", rec("mail-read", 1, 9)))["confidence"], "guess")
        def read(sender, *months):
            return [self.mem.record("user", "activity", f"{sender} {m}", topic="mail-read", happened_at=f"2026-{m:02d}-28",
                                    data={"month": f"2026-{m:02d}", "sender": sender, "count": 4, "opened": 2}) for m in months]
        self.assertEqual(self.mem.note(self.mem.conclude("interest", "越野跑", read("list:a", 6, 7, 8)))["confidence"],
                         "guess")                                      # one subscription is that sender, not a topic
        self.assertEqual(self.mem.note(self.mem.conclude("interest", "马拉松", read("list:a", 6, 8) + read("list:b", 9)))
                         ["confidence"], "likely")                    # three months, two senders, 6 opened

    def test_the_bar_follows_the_users_answers(self):
        """先粗后细，按反馈逼近: what this user keeps saying is wrong needs more; what they always
        confirm needs less."""
        def two(text, a, b):
            return self.mem.conclude("fact", text, [self.mem.record("user", "statement", text, topic="sent-mail", happened_at=d)
                                                    for d in (a, b)])
        for i in range(2):
            self.mem.reject(two(f"错的 {i}", "2026-09-01", "2026-09-09"))
        self.assertEqual(self.mem.note(two("又一条", "2026-09-01", "2026-09-09"))["confidence"], "guess")   # now 3, 14 days
        other = Memory(os.path.join(self.dir, "other.db"), tool="luoke-mail")
        for i in range(3):
            other.confirm(other.propose("fact", f"对的 {i}", [other.record("user", "statement", "x", topic="sent-mail")]))
        n = other.conclude("fact", "一封就够", [other.record("user", "statement", "y", topic="sent-mail", happened_at="2026-09-01")])
        self.assertEqual(other.note(n)["confidence"], "likely")                    # now 1 mail
        other.close()

    def test_a_habit_carries_what_a_tool_needs(self):
        """Structured habits (schema 7): {account, sender, action}; only a confirmed one that can be undone runs by itself."""
        r = [self.mem.record("user", "activity", "改成归档", topic="mail-override", happened_at=d) for d in ("2026-10-01", "2026-10-09")]
        data = {"account": "mbx-1", "sender": "list:news.shop", "category": "promotion", "action": "archive"}
        nid = self.mem.conclude("preference", "某商城的邮件都归档", r, data=data)
        n = self.mem.note(nid)
        self.assertEqual((n["confidence"], n["data"], n["auto"]), ("likely", data, False))
        self.mem.confirm(nid)
        sure, = self.mem.notes(kinds=["preference"], confidence=["sure"])
        self.assertEqual((sure["data"], sure["auto"]), (data, True))
        binned = self.mem.propose("preference", "某论坛的邮件都移到垃圾箱", r, data=dict(data, action="trash"))
        self.mem.confirm(binned)
        self.assertFalse(self.mem.note(binned)["auto"])                        # deleting is never done by itself
        self.assertEqual(self.mem.db.execute("PRAGMA user_version").fetchone()[0] % 1000, M.SCHEMA_VERSION)

    def test_switching_copies_the_memory_aside_then_grades_it(self):
        """No preview; a whole copy first, then the table; what the user took a stand on stays."""
        lab = self.mem.record("user", "statement", "标签「财务」", topic="own-label")
        self.mem.db.execute("UPDATE notes SET status='active'")
        labelled = self.mem._note("dislike", "银行账单", "inferred", "active", [lab])
        kept = self.mem._note("interest", "城市骑行", "inferred", "active", [lab])
        self.mem.confirm(kept)
        letter = self.mem.record("third_party", "statement", "某交易所来信", topic="received-mail",
                                 data={"count": 200, "months": 20, "opened": 100, "last": time.strftime("%Y-%m")})
        coins = self.mem._note("fact", "在某交易所有数字货币账户", "inferred", "active", [letter])
        whole = WholeMemory(self.mem.path)
        backup = self.mem.path + M.REGRADE_BACKUP
        self.assertTrue(os.path.exists(backup))
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o600)
        self.assertEqual((whole.note(labelled)["status"], whole.note(kept)["status"], whole.note(coins)["status"]),
                         ("proposed", "active", "active"))           # money, well seen: in effect
        copy = sqlite3.connect(backup)
        self.assertEqual(copy.execute("SELECT status FROM notes WHERE id=?", (labelled,)).fetchone()[0], "active")
        copy.close()
        import json as _json
        self.assertEqual(_json.loads(whole._learning("regrade.0.18"))["ids"], [labelled])
        whole.forget(labelled)                                           # forgetting wins over the copy
        self.assertFalse(os.path.exists(backup))
        whole.close()
        WholeMemory(self.mem.path).close()
        self.assertFalse(os.path.exists(backup))                         # made once only

    def test_a_tool_reads_the_asks_that_concern_it(self):
        """ADR 0012 amendment 6: until luoke-agent is there, a tool shows what concerns it on its own page."""
        whole = WholeMemory(self.mem.path)
        mine = self.mem.propose("interest", "跑步", [self.mem.record("user", "activity", "打开过", topic="mail-read")])
        ime = Memory(self.mem.path, tool="luoke-ime")
        theirs = ime.propose("term", "行云", [ime.record("user", "statement", "行云", topic="ime-commit")])
        a = whole.ask("question", "你在关注跑步吗？", "想确认", about_note=mine)
        b = whole.ask("question", "行云是你的说法吗？", "想确认", about_note=theirs)
        c = whole.ask("suggestion", "退订某商城？", "清了好几次", action={"tool": "luoke-mail", "capability": "unsubscribe"})
        self.assertEqual(sorted(x["id"] for x in self.mem.asks(tool="luoke-mail")), sorted([a, c]))
        self.assertEqual([x["id"] for x in self.mem.asks(tool="luoke-ime")], [b])
        ime.close()
        whole.close()

    def test_what_expired_unanswered_does_not_hold_its_place(self):
        whole = WholeMemory(self.mem.path)
        first = whole.ask("question", "要问的", "因为", key="k", expires_at="2026-10-10 00:00:00", now="2026-10-01 00:00:00")
        self.assertEqual(whole.ask("question", "要问的", "因为", key="k", now="2026-10-05 00:00:00"), first)   # still open
        self.assertIsNone(whole.ask("question", "要问的", "因为", key="k", now="2026-10-12 00:00:00"))    # a week's rest
        again = whole.ask("question", "要问的", "因为", key="k", now="2026-10-20 00:00:00")
        self.assertNotIn(again, (None, first))
        whole.close()

    def test_one_topic_in_other_words_is_one_note(self):
        """电商促销与物流广告 and 电商促销与物流通知 are one; a habit about another sender is not."""
        lab = lambda t: self.mem.record("user", "statement", t, topic="own-label")   # noqa: E731
        a = self.mem.propose("dislike", "电商促销与物流广告", [lab("广告")])
        self.assertEqual(self.mem.propose("dislike", "电商促销与物流通知", [lab("交易")]), a)
        self.assertEqual(len(self.mem.note(a)["cites"]), 2)
        self.mem.reject(a)
        self.assertIsNone(self.mem.propose("dislike", "电商促销和物流", [lab("快递")]))       # said wrong: not back in other words
        shop = self.mem.propose("preference", "某商城的邮件都归档", [lab("x")])
        self.assertNotEqual(self.mem.propose("preference", "某书店的邮件都归档", [lab("y")]), shop)
        # what is there already folds when the whole memory opens
        x = self.mem._note("interest", "网络基础设施与DNS管理", "inferred", "proposed", [lab("DNS")])
        y = self.mem._note("interest", "域名注册与网络基础设施管理", "inferred", "proposed", [lab("域名")])
        z = self.mem._note("interest", "网络工程与交换机配置", "inferred", "proposed", [lab("交换机")])
        WholeMemory(self.mem.path).close()
        self.assertEqual((self.mem.note(y)["status"], self.mem.note(y)["superseded_by"]), ("superseded", x))
        self.assertEqual((len(self.mem.note(x)["cites"]), self.mem.note(z)["status"]), (2, "proposed"))

    def test_a_fact_gains_its_label_and_what_the_user_confirmed_stays(self):
        sent = lambda d: self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        old = self.mem.conclude("fact", "Frank Lou", [sent("2026-01-08")], topic="姓名")
        self.assertEqual(self.mem.conclude("fact", "姓名：Frank Lou", [sent("2026-01-20")], topic="姓名"), old)
        self.assertEqual((self.mem.note(old)["text"], self.mem.note(old)["confidence"]), ("姓名：Frank Lou", "likely"))
        sure = self.mem.conclude("fact", "职位：产品经理", [sent("2026-02-01"), sent("2026-02-10")], topic="职位")
        self.mem.confirm(sure)
        new = self.mem.conclude("fact", "职位：产品负责人", [sent("2026-03-01"), sent("2026-03-10")], topic="职位")
        self.assertEqual((self.mem.note(sure)["status"], self.mem.note(new)["status"]), ("active", "active"))   # theirs to change
        others = [self.mem.conclude("fact", t, [sent("2026-04-01"), sent("2026-04-10")], topic="其他") for t in ("用 Apple Mail", "用番番翻译")]
        self.assertEqual([self.mem.note(n)["status"] for n in others], ["active", "active"])                  # a catch-all topic

    def test_facts_a_catch_all_topic_pushed_out_come_back(self):
        """Before 0.19.1 one fact on 其他 superseded the one before it: restored once, graded by the table."""
        sent = lambda d: self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        a = self.mem._note("fact", "用 Apple Mail", "inferred", "active", [sent("2026-04-01"), sent("2026-04-10")], topic="其他")
        b = self.mem._note("fact", "用番番翻译", "inferred", "active", [sent("2026-04-02")], topic="其他")
        c = self.mem._note("fact", "持有域名", "inferred", "active", [sent("2026-04-03")], topic="其他")
        self.mem.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (b, a))
        self.mem.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (c, b))
        self.mem.db.commit()
        WholeMemory(self.mem.path).close()
        self.assertEqual([self.mem.note(n)["status"] for n in (a, b, c)], ["active", "proposed", "proposed"])
        self.assertIsNone(self.mem.note(a)["superseded_by"])

    def test_what_changes_and_is_old_is_a_thing_of_the_past(self):
        """What was so ten years ago is not asked or used as now. What changes, with nothing newer
        than two years behind it, is 以前的事; a name or a school one went to is not; the user can say it still is."""
        now = "2026-10-19 10:00:00"
        def sent(*days):
            return [self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d) for d in days]
        work = self.mem.conclude("fact", "单位：某公司", sent("2014-03-01", "2014-03-20"), topic="单位")
        school = self.mem.conclude("fact", "曾就读于某大学", sent("2012-05-01", "2012-05-20"), topic="学校:某大学")
        name = self.mem.conclude("fact", "姓名：张三", sent("2013-01-01", "2013-01-20"), topic="姓名")
        city = self.mem.conclude("fact", "居住地：杭州", sent("2026-08-01", "2026-08-20"), topic="城市")
        whole = WholeMemory(self.mem.path)
        whole.age(now=now)
        self.assertEqual([self.mem.note(n)["status"] for n in (work, school, name, city)], ["past", "active", "active", "active"])
        self.assertEqual([n["id"] for n in self.mem.notes(kinds=["fact"])].count(work), 0)      # not used as now
        past, = self.mem.notes(status=["past"])
        self.assertEqual((past["id"], past["fresh"][:4], past["confidence"]), (work, "2014", None))
        self.mem.confirm(work)                                         # still so, the user says
        self.assertEqual(self.mem.note(work)["status"], "active")
        whole.age(now=now)
        self.assertEqual(self.mem.note(work)["status"], "active")      # their word is news
        old = self.mem.conclude("fact", "职位：工程师", sent("2015-01-01", "2015-01-20"), topic="职位")
        whole.age(now=now)
        self.assertEqual(self.mem.note(old)["status"], "past")
        self.assertEqual(self.mem.conclude("fact", "职位：工程师", sent("2026-09-01"), topic="职位"), old)
        self.assertEqual(self.mem.note(old)["status"], "active")       # seen again, and the mails bear it out
        whole.close()

    def test_it_was_so_long_ago_is_not_a_new_understanding(self):
        """#157 (10-19): "改成…" with "那是非常久之前的了" became a sure interest of its own; it says 以前是、现在不是."""
        lab = lambda: self.mem.record("user", "statement", "标签", topic="own-label")   # noqa: E731
        a = self.mem.propose("interest", "编程学习与并行计算", [lab()])
        self.assertEqual(self.mem.revise(a, "那是非常久之前的了"), a)
        self.assertEqual((self.mem.note(a)["status"], self.mem.notes(kinds=["interest"])), ("past", []))
        b = self.mem.propose("interest", "园艺", [lab()])
        self.mem.revise(b, "不是")
        self.assertEqual(self.mem.note(b)["status"], "rejected")
        c = self.mem.propose("interest", "围棋", [lab()])
        self.assertNotEqual(self.mem.revise(c, "围棋和象棋"), c)        # new words: a new understanding
        whole = WholeMemory(self.mem.path)
        aid = whole.ask("question", "你在关注「集邮」吗？", "想确认", about_note=self.mem.propose("interest", "集邮", [lab()]))
        Memory(self.mem.path, tool="luoke-agent").reply(aid, "past")
        self.assertEqual(whole.note(whole.asks(status=["answered"])[0]["about_note"])["status"], "past")
        # what 0.19 kept already is put right once
        d = self.mem.propose("interest", "摄影", [lab()])
        bad = self.mem._note("interest", "很久以前的事了", "user", "active", [lab()], decided=True)
        self.mem.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (bad, d))
        self.mem.db.execute("DELETE FROM learning WHERE name='repair.answers'")
        self.mem.db.commit()
        WholeMemory(self.mem.path).close()
        self.assertEqual((self.mem.note(d)["status"], self.mem.note(bad)["status"]), ("past", "superseded"))
        whole.close()

    def test_a_tool_puts_right_the_time_of_its_own_record(self):
        r = self.mem.record("user", "statement", "标签「旅行」", topic="own-label", happened_at="2026-10-08 17:05:13")
        self.assertTrue(self.mem.redate(r, "2015-06"))
        self.assertEqual(self.mem.records(topic="own-label")[0]["happened_at"], "2015-06")
        ime = Memory(self.mem.path, tool="luoke-ime")
        self.assertFalse(ime.redate(r, "2026-01"))                           # not its own
        with self.assertRaises(ValueError):
            self.mem.redate(r, "昨天")
        ime.close()

    def test_a_brand_alone_is_not_an_interest_about_the_user(self):
        read = lambda s, m: self.mem.record("user", "activity", f"订阅「{s}」", topic="mail-read", happened_at=f"2026-{m:02d}-28",
                                            data={"sender": s, "opened": 3})   # noqa: E731
        ids = [read("list:a", 6), read("list:b", 8), read("list:a", 9)]
        self.assertEqual(self.mem.note(self.mem.conclude("interest", "Northbay Bank", ids))["confidence"], "guess")
        self.assertEqual(self.mem.note(self.mem.conclude("interest", "虚拟银行：香港的数字银行", ids))["confidence"], "likely")

    def test_what_the_user_said_stays_when_a_thing_of_the_past_is_seen_again(self):
        """Coming back from the past keeps the user's word -- a confirmed note is sure
        again; one they said was over is a guess, with their words and when they said it still on it."""
        now = "2026-10-19 10:00:00"
        sent = lambda d: self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        sure = self.mem.conclude("fact", "单位：某公司", [sent("2014-03-01"), sent("2014-03-20")], topic="单位")
        self.mem.confirm(sure, now="2014-04-01 10:00:00")
        over = self.mem.conclude("fact", "职位：工程师", [sent("2026-09-01"), sent("2026-09-20")], topic="职位")
        self.mem.was(over, now="2026-10-01 10:00:00")
        whole = WholeMemory(self.mem.path)
        whole.age(now=now)
        self.assertEqual(self.mem.note(sure)["status"], "past")
        self.mem.conclude("fact", "单位：某公司", [sent("2026-10-10")], topic="单位")
        self.mem.conclude("fact", "职位：工程师", [sent("2026-10-10")], topic="职位")
        a, b = self.mem.note(sure), self.mem.note(over)
        self.assertEqual((a["status"], a["decided_at"], a["confidence"]), ("active", "2014-04-01 10:00:00", "sure"))
        self.assertEqual((b["status"], b["decided_at"]), ("proposed", "2026-10-01 10:00:00"))
        self.assertTrue(any(c["text"].startswith("以前是，现在不是") for c in b["cites"]))
        whole.close()

    def test_the_users_latest_word_decides_when_a_thing_of_the_past_comes_back(self):
        sent = lambda d: self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        n = self.mem.conclude("fact", "职位：工程师", [sent("2014-01-01"), sent("2014-01-20")], topic="职位")
        self.mem.was(n, now="2014-02-01 10:00:00")
        self.mem.confirm(n, now="2014-03-01 10:00:00")                    # 现在还是
        whole = WholeMemory(self.mem.path)
        whole.age(now="2026-10-19 10:00:00")
        self.assertEqual(self.mem.note(n)["status"], "past")
        self.mem.conclude("fact", "职位：工程师", [sent("2026-10-10")], topic="职位")
        self.assertEqual(self.mem.note(n)["confidence"], "sure")
        whole.close()

    def test_one_fact_written_under_its_topic_and_under_other_is_one(self):
        """luoke-mem#2: luoke-mail once filed a fact under 其他 and once under its topic, the value of one holding
        the other's: one note."""
        sent = lambda d: self.mem.record("user", "statement", "署名", topic="sent-mail", happened_at=d)   # noqa: E731
        a = self.mem.conclude("fact", "使用 Apple Mail", [sent("2026-10-01")], topic="其他")
        self.assertEqual(self.mem.conclude("fact", "邮件客户端：使用 Apple Mail 客户端", [sent("2026-10-09")],
                                           topic="邮件客户端"), a)
        b = self.mem.conclude("fact", "职位：产品经理", [sent("2026-10-01")], topic="职位")
        self.assertNotEqual(self.mem.conclude("fact", "职位：产品经理助理", [sent("2026-10-09")], topic="职位"), b)

    def test_a_guess_of_long_ago_is_a_thing_of_the_past_too(self):
        r = self.mem.record("third_party", "statement", "某云来信", topic="received-mail", happened_at="2013-05")
        n = self.mem.propose("fact", "用某云服务", [r])
        with WholeMemory(self.mem.path) as whole:
            whole.age(now="2026-10-22 10:00:00")
        self.assertEqual(self.mem.note(n)["status"], "past")

    def test_one_institution_one_fact(self):
        """Facts about one institution are one note, whatever category the model
        filed them under; a confirmed one takes the new evidence and keeps its words; the user can part them."""
        letter = lambda: self.mem.record("third_party", "statement", "某银行来信", topic="received-mail")   # noqa: E731
        a = self.mem.propose("fact", "在某银行有储蓄账户", [letter()], topic="账户:某银行")
        self.assertEqual(self.mem.propose("fact", "使用某银行的手机银行", [letter()], topic="服务:某银行"), a)
        self.assertEqual(self.mem.propose("fact", "某银行的信用卡", [letter()], topic="机构:某银行"), a)   # luoke-mail from 0.25 on
        self.assertEqual(len(self.mem.note(a)["cites"]), 3)
        self.mem.confirm(a)
        self.assertEqual(self.mem.propose("fact", "在某银行办了房贷", [letter()], topic="账户:某银行"), a)
        n = self.mem.note(a)
        self.assertEqual((n["text"], n["data"]["newer"], n["confidence"]), ("在某银行有储蓄账户", "在某银行办了房贷", "sure"))
        # folded in by the tidy, parted again by the user
        b = self.mem._note("fact", "用某券商炒股", "inferred", "proposed", [letter()], topic="账户:某券商")
        c = self.mem._note("fact", "某券商的基金账户", "inferred", "proposed", [letter()], topic="服务:某券商")
        from luoke_mem import learn as L
        with WholeMemory(self.mem.path) as whole:
            L._tidy(whole, "2026-10-22 10:00:00")
            self.assertEqual([m["id"] for m in whole.merged(b)], [c])
            whole.split(c)
            self.assertEqual((whole.note(c)["status"], whole.merged(b)), ("proposed", []))
            L._tidy(whole, "2026-10-22 11:00:00")
            self.assertEqual(whole.note(c)["status"], "proposed")         # stays apart

    def test_a_person_or_a_matter_is_not_an_institution(self):
        """Topics like "同事:某人", "工作:X" are not institutions -- two things about one colleague are two; a
        fact the user said no to does not stop another about the same institution; and what 0.25.0-0.25.3 folded
        that way comes apart."""
        sent = lambda: self.mem.record("user", "statement", "署名", topic="sent-mail")   # noqa: E731
        letter = lambda: self.mem.record("third_party", "statement", "某银行来信", topic="received-mail")   # noqa: E731
        a = self.mem.conclude("fact", "张三是我的同事", [sent()], topic="同事:张三")
        self.assertNotEqual(self.mem.conclude("fact", "张三负责前端", [sent()], topic="同事:张三"), a)
        w = self.mem.conclude("fact", "在做某银行的项目", [sent()], topic="工作:某银行")
        self.assertNotEqual(self.mem.propose("fact", "在某银行有储蓄账户", [letter()], topic="机构:某银行"), w)
        no = self.mem.propose("fact", "某券商的基金账户", [letter()], topic="机构:某券商")
        self.mem.reject(no)
        self.assertIsNone(self.mem.propose("fact", "某券商的基金账户", [letter()], topic="机构:某券商"))
        self.assertIsNotNone(self.mem.propose("fact", "用某券商炒股", [letter()], topic="机构:某券商"))
        # folded by the old rule: apart again, once
        b = self.mem._note("fact", "李四是客户", "inferred", "active", [sent()], topic="客户:李四")
        c = self.mem._note("fact", "李四在上海", "inferred", "active", [sent()], topic="客户:李四")
        d = self.mem._note("fact", "某银行的信用卡", "inferred", "proposed", [letter()], topic="账户:某银行")
        e = self.mem._note("fact", "某银行的手机银行", "inferred", "proposed", [letter()], topic="服务:某银行")
        with self.mem._lock, self.mem.db:
            self.mem.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (b, c))
            self.mem.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (d, e))
        with WholeMemory(self.mem.path) as whole:
            self.assertEqual(whole.unfold_people(), [c])
            self.assertEqual((whole.note(c)["status"], whole.note(e)["status"]), ("proposed", "superseded"))   # one mail: a guess, by the table


    def test_the_command_line_for_the_installer(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        import json as _json

        def run(*argv):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(cli(["--path", self.mem.path, *argv]), 0)
            return _json.loads(out.getvalue())
        self.mem.conclude("fact", "我是产品经理", [self.mem.record("user", "statement", "你发出的邮件：「排期」", topic="sent-mail")])
        self.assertEqual(run("sources"), [{"tool": "luoke-mail", "records": 1, "notes": 1}])
        self.assertEqual(run("forget-tool", "luoke-mail", "--dry-run"), {"tool": "luoke-mail", "records": 1, "notes": 1})
        self.assertEqual(len(self.mem.notes(status=["proposed"])), 1)
        self.assertEqual(run("forget-tool", "luoke-mail")["notes"], 1)
        self.assertEqual(self.mem.notes(status=["proposed"]), [])
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.assertEqual(cli(["--path", self.mem.path, "forget-tool", "luoke-mem"]), 1)   # said, not a traceback
        self.assertIn("螺壳记忆自己的", err.getvalue())
        missing = os.path.join(self.dir, "none", "mem.db")
        out = io.StringIO()
        with redirect_stdout(out):
            cli(["--path", missing, "sources"])
        self.assertEqual(out.getvalue().strip(), "[]")
        self.assertFalse(os.path.exists(missing))                       # looking does not create it

    def test_the_command_line_writes_for_a_tool_without_python(self):
        """luoke-mem#7: the audit (Swift) writes records through `record`, JSON Lines in, a line out for each; one bad
        line stops nothing; credentials masked, sensitive context marked here; 螺壳体检 is not health; and forgotten by
        source like any tool."""
        import io
        import json as _json
        from contextlib import redirect_stderr, redirect_stdout
        from luoke_mem import sensitive_kinds, TOOL_NAMES

        def run(lines, *extra):
            out, stdin = io.StringIO(), sys.stdin
            sys.stdin = io.StringIO("".join(lines))
            try:
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    code = cli(["--path", self.mem.path, "record", *extra])
            finally:
                sys.stdin = stdin
            return code, [_json.loads(x) for x in out.getvalue().splitlines()]

        line = lambda **r: _json.dumps(r, ensure_ascii=False) + "\n"   # noqa: E731
        code, out = run([line(speaker="user", kind="receipt", text="螺壳体检：清了 pip、npm 的缓存，1.2 GB", topic="audit-cleaned",
                              ref="p-1", happened_at="2026-10-27 10:00:00",
                              data={"category": "cache", "rule": "cache.rebuildable.pip", "items": 2, "bytes": 1288490188}),
                         "\n",
                         line(speaker="user", kind="receipt", text="把「体检报告.pdf」（Win.Test.EICAR）移到了废纸篓，密码是 Lk-2026-qwe",
                              topic="audit-malware", data={"file_name": "体检报告.pdf", "action": "trash"}),
                         "not json\n",
                         line(speaker="nobody", kind="receipt", text="x"),
                         line(speaker="system", kind="activity", text="自动清了 Xcode 的缓存", topic="audit-auto-cleaned",
                              data={"bytes": 1, "by": "rule"})], "--tool", "luoke-audit")
        self.assertEqual(code, 1)
        self.assertEqual([("id" in o, "error" in o) for o in out], [(True, False), (True, False), (False, True), (False, True), (True, False)])
        recs = {r["topic"]: r for r in self.mem.records(tool="luoke-audit")}
        self.assertEqual(sorted(recs), ["audit-auto-cleaned", "audit-cleaned", "audit-malware"])
        self.assertEqual(sensitive_kinds("螺壳体检：清了缓存"), [])
        self.assertEqual(sensitive_kinds("体检报告"), ["health"])
        self.assertIn("〔密码〕", recs["audit-malware"]["text"])
        self.assertNotIn("Lk-2026", recs["audit-malware"]["text"])
        self.assertEqual(TOOL_NAMES["luoke-audit"], "螺壳体检")
        self.assertEqual(run([], "--tool", "luoke-mem")[0], 2)                # never in the memory's own name
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli(["--path", self.mem.path, "forget-tool", "luoke-audit"]), 0)
        self.assertEqual(_json.loads(out.getvalue())["records"], 3)
        self.assertEqual(self.mem.records(tool="luoke-audit"), [])


if __name__ == "__main__":
    unittest.main()
