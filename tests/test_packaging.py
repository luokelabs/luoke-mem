"""packaging/accessory.json: what 螺壳记忆.app declares to the hub (luoke-core V1 §8). Every command it declares
is run here with this Python, the way the hub runs it with the app's own."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: E402,F401  -- first: a temporary home; the real memory and hub are refused

import luoke_mem  # noqa: E402
from luoke_mem import Memory, ui  # noqa: E402

with open(os.path.join(HERE, "packaging", "accessory.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def run(args, memory, tool="luoke-mail", *more):
    argv = [a.replace("{memory}", memory).replace("{tool}", tool) for a in args] + list(more)
    return subprocess.run([sys.executable] + argv, cwd=HERE, capture_output=True, text=True, timeout=30)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.memory = os.path.join(tempfile.mkdtemp(prefix="luoke-mem-pkg-"), "mem.db")
        with Memory(self.memory, tool="luoke-mail") as mem:
            mem.conclude("fact", "我是产品经理", [mem.record("user", "statement", "你发出的邮件：「排期」", topic="sent-mail",
                                                         happened_at=at) for at in ("2026-10-01", "2026-10-09")])

    def test_a_tool_needs_only_three_modules(self):
        """What a tool carries (the input method's sandboxed component takes only these): __init__, memory and
        sensitive; the rest -- learn, hub, ui, __main__ -- is the memory app's own and never imported by them."""
        import shutil
        import subprocess
        pkg = os.path.join(tempfile.mkdtemp(prefix="luoke-mem-tool-"), "luoke_mem")
        os.makedirs(pkg)
        for name in ("__init__.py", "memory.py", "sensitive.py"):
            shutil.copy(os.path.join(HERE, "luoke_mem", name), pkg)
        code = ("import luoke_mem as m, sys; mem = m.Memory(sys.argv[1], tool='luoke-ime'); "
                "r = mem.record('user', 'statement', '行云', topic='ime-commit'); mem.reject(mem.conclude('term', '行云', [r])); "
                "mem.forget_records(topics=['ime-commit'], keep_cited=True); assert m.never_kept('验证码 482913'); "
                "assert m.sensitive_kinds('去医院复诊') == ['health']; assert m.KIND_NAMES and m.TOOL_NAMES; "
                "assert not ({'luoke_mem.learn', 'luoke_mem.hub', 'luoke_mem.ui'} & set(sys.modules)); print('ok')")
        done = subprocess.run([sys.executable, "-B", "-c", code, os.path.join(os.path.dirname(pkg), "mem.db")],
                              cwd=os.path.dirname(pkg), capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, PYTHONPATH=os.path.dirname(pkg)))
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "ok"), done.stderr)

    def test_who_it_is(self):
        """The fields of luoke-core V1 §8「配件清单」, contract 1."""
        self.assertEqual(set(MANIFEST), {"contract", "accessory", "bundle_id", "name", "version", "type", "entry",
                                         "resident", "memory", "ui_element", "tasks", "data", "commands", "jobs"})
        self.assertEqual((MANIFEST["contract"], MANIFEST["accessory"], MANIFEST["bundle_id"], MANIFEST["type"]),
                         (1, "luoke-mem", "ai.luoke.memory", "component"))
        self.assertEqual(MANIFEST["version"], luoke_mem.__version__)
        with open(os.path.join(HERE, "pyproject.toml"), encoding="utf-8") as f:     # one version everywhere
            self.assertIn(f'version = "{luoke_mem.__version__}"', f.read())
        self.assertTrue(MANIFEST["ui_element"])                                 # opens a page and leaves
        self.assertEqual((MANIFEST["resident"], MANIFEST["data"], MANIFEST["memory"]), (False, [], False))
        self.assertEqual(MANIFEST["tasks"], [{"name": "luoke-mem.typing", "kind": "generate", "main": False},   # ADR 0005
                                             {"name": "luoke-mem.topics", "kind": "generate", "main": False}])  # ADR 0012
        self.assertEqual(MANIFEST["entry"], ["-m", "luoke_mem", "app"])
        self.assertEqual(set(MANIFEST["commands"]), {"forget-tool"})   # ui went with the hub's endpoint (2026-10-16)

    def test_the_idle_job_as_the_hub_runs_it(self):
        """luoke-core V1 §8 jobs (top-level ADR 0006): started when the machine is idle, -B, cwd /, a hub socket
        in the environment; nothing due -> done at once, exit 0, one line of reasons on stderr."""
        job, = MANIFEST["jobs"]
        self.assertEqual((job["name"], job["uses_model"], job["allow_battery"]), ("learn", True, False))
        self.assertEqual((job["min_interval_minutes"], job["timeout_minutes"]), (120, 30))
        argv = [a.replace("{memory}", self.memory) for a in job["command"]]
        env = dict(os.environ, PYTHONPATH=HERE, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                   LUOKE_HUB_SOCKET=os.path.join(os.path.dirname(self.memory), "no-hub.sock"))
        done = subprocess.run([sys.executable, "-B"] + argv, cwd="/", env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(done.returncode, 0)
        self.assertRegex(done.stderr, r"^learn: 没有跑：[^\n]*\n(topics: [^\n]*\n)?ruminate: 整理 \d+ 条，降档 \d+ 条，习惯 \d+ 条，撤回 \d+ 次，想问 \d+ 个，建议 \d+ 条\n$")   # reasons, counts
        self.assertEqual(json.loads(done.stdout)["ran"], False)

    def test_forget_tool_as_the_hub_runs_it(self):
        done = run(MANIFEST["commands"]["forget-tool"], self.memory, "luoke-mail", "--dry-run")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"tool": "luoke-mail", "records": 2, "notes": 1})
        self.assertEqual(len(Memory(self.memory, tool="luoke-mail").notes()), 1)      # a dry run
        done = run(MANIFEST["commands"]["forget-tool"], self.memory, "luoke-mail")
        self.assertEqual(json.loads(done.stdout)["notes"], 1)
        self.assertEqual(json.loads(run(["-m", "luoke_mem", "--path", "{memory}", "sources"], self.memory).stdout), [])

    def test_the_ui_command_line_still_answers(self):          # luoke-agent's dev build points people to it
        done = run(["-m", "luoke_mem", "--path", "{memory}", "ui"], self.memory)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertRegex(done.stdout.strip(), r"^http://127\.0\.0\.1:\d+/\?t=[\w-]+$")
        os.kill(ui._read_state(self.memory)["pid"], signal.SIGTERM)

    def test_a_newer_memory_is_exit_code_1(self):
        import sqlite3
        db = sqlite3.connect(self.memory)
        db.execute("PRAGMA user_version = 9000")
        db.commit()
        db.close()
        done = run(MANIFEST["commands"]["forget-tool"], self.memory, "luoke-mail", "--dry-run")
        self.assertEqual(done.returncode, 1)
        self.assertIn("newer", done.stderr)

    def test_the_entry_is_a_command(self):
        done = subprocess.run([sys.executable] + MANIFEST["entry"] + ["--help"], cwd=HERE, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
