"""luoke-mem from the command line, for whatever has no Python of its own to call the library with (the
installer in luoke-core, when a tool is uninstalled). Prints JSON.

    python3 -m luoke_mem sources                      which tools wrote into the memory, and how much
    python3 -m luoke_mem forget-tool <tool> --dry-run what forgetting that tool's share would delete
    python3 -m luoke_mem forget-tool <tool>           forget it: its records, its notes, and other tools'
                                                      notes that rested only on its records
    python3 -m luoke_mem ui [--open]                  start or find the whole memory's page; prints its address
    python3 -m luoke_mem app                          what 螺壳 runs to open the memory: the page, in the browser
                                                      (an empty memory if there is none yet; problems in a dialog)
    python3 -m luoke_mem learn [--force] [--hub SOCK] form understanding from what was typed, through the hub,
                                                      when it is due (DESIGN「归纳理解」); prints what it did
    python3 -m luoke_mem learn --again                go over all the typing again, from the start (what is
                                                      known is not learnt twice); the hub's job goes on with it
    python3 -m luoke_mem record --tool <tool>         write records for a tool with no Python of its own (the
                                                      audit, in Swift; luoke-mem#7): JSON Lines in, one a line,
                                                      the fields of Memory.record(); one {"id"} or {"error"} line
                                                      out for each; exit 1 if any failed

`--path` names another memory file (default ~/.luoke/mem.db, or $LUOKE_HOME/mem.db). Looking at a memory that
does not exist yet does not create it."""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

from . import ui
from .memory import WholeMemory, default_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m luoke_mem", description="螺壳记忆（luoke-mem）")
    ap.add_argument("--path", help="记忆文件（默认 ~/.luoke/mem.db）")
    either = argparse.ArgumentParser(add_help=False)          # --path before or after the command
    either.add_argument("--path", default=argparse.SUPPRESS, help="记忆文件（默认 ~/.luoke/mem.db）")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("sources", parents=[either], help="各工具写进记忆的记录和理解的条数")
    forget = sub.add_parser("forget-tool", parents=[either], help="从记忆里忘掉一个工具写的全部")
    forget.add_argument("tool", help="工具标识，例如 luoke-mail")
    forget.add_argument("--dry-run", action="store_true", help="只报会删多少，不删")
    page = sub.add_parser("ui", parents=[either], help="打开整份记忆的页面：打印它的地址")
    page.add_argument("--open", action="store_true", help="顺便在浏览器里打开")
    page.add_argument("--note", type=int, help="直接打开到这条理解")
    page.add_argument("--view", choices=["recent"], help="直接打开到「最近记下的」")
    app = sub.add_parser("app", parents=[either], help="螺壳打开记忆时的入口：在浏览器里打开整份记忆的页面")
    app.add_argument("--note", type=int, help="直接打开到这条理解（luoke://memory?note=N）")
    app.add_argument("--view", choices=["recent"], help="直接打开到「最近记下的」（luoke://memory?view=recent）")
    learn = sub.add_parser("learn", parents=[either], help="从记录里归纳理解一次（经螺壳用模型）")
    learn.add_argument("--force", action="store_true", help="有新记录就跑，不等攒够")
    learn.add_argument("--again", action="store_true", help="从头再过一遍：过过的打字和邮件记录都重新交给模型（打字记下的不会重复；邮件归纳的、你没表过态的话题重来）")
    learn.add_argument("--hub", help="螺壳的 socket（默认找螺壳的；也看 LUOKE_HUB_SOCKET）")
    rec = sub.add_parser("record", parents=[either], help="替没有 Python 的工具写记录：标准输入一行一条 JSON")
    rec.add_argument("--tool", required=True, help="写的是哪个工具，例如 luoke-audit")
    serve = sub.add_parser("serve", parents=[either], help=argparse.SUPPRESS)   # what ui.ensure() starts
    serve.add_argument("--idle", type=float, default=ui.IDLE, help=argparse.SUPPRESS)
    serve.add_argument("--hold", type=float, help=argparse.SUPPRESS)          # older callers pass it: ignored
    run = sub.add_parser("run", parents=[either], help=argparse.SUPPRESS)       # the page's server itself
    run.add_argument("--ready", type=int, required=True, help=argparse.SUPPRESS)
    run.add_argument("--idle", type=float, default=ui.IDLE, help=argparse.SUPPRESS)
    run.add_argument("--hold", type=float, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    path = args.path or default_path()
    if args.command == "serve":
        ui.serve(path, idle=args.idle)   # ends the process
    if args.command == "run":
        return ui.run(path, args.ready, idle=args.idle)
    if args.command == "app":
        return ui.app(path, note=args.note, view=args.view)
    if args.command == "learn":
        from .hub import Hub, HubError
        from .learn import again, learn as form
        if not os.path.exists(path):              # nothing written yet: nothing to learn, and nothing created
            print(json.dumps({"ran": False, "why": "no memory"}))
            return 0
        import fcntl
        import signal
        # Stopped from outside (the hub's idle-task timeout): leave now. Batches done are kept; the one being
        # written is rolled back with its transaction.
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        fd = os.open(path + "-learn.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)     # one run per memory file at a time
            except OSError:
                print(json.dumps({"ran": False, "why": "running"}))
                return 0
            try:
                with WholeMemory(path) as whole:
                    if args.again:                # from the start; stopped half way, the hub's job goes on
                        again(whole)
                        from .topics import again as topics_again
                        topics_again(whole)
                    out = form(whole, Hub(args.hub), force=args.force or args.again)
                from .learn import outcome
                print(f"learn: {outcome(out)}", file=sys.stderr)    # counts and reason codes only
                if out.get("error"):                  # no hub, the task refused: the rest was done all the same,
                    print(f"{out['error']}", file=sys.stderr)    # and the run says it failed
                    print(json.dumps(out, ensure_ascii=False))
                    return 1
                if out.get("topics"):
                    from .topics import outcome as said
                    print(f"topics: {said(out['topics'])}", file=sys.stderr)
                r = out.get("rumination") or {}
                print(f"ruminate: 整理 {r.get('tidied', 0)} 条，降档 {r.get('demoted', 0)} 条，习惯 {r.get('habits', 0)} 条，撤回 {r.get('undone', 0)} 次，想问 {r.get('questions', 0)} 个，建议 {r.get('suggestions', 0)} 条",
                      file=sys.stderr)
            except HubError as ex:                # no hub, or the task is not there: say so; stopping is not this
                print(f"{ex.code}: {ex}", file=sys.stderr)
                return 1
            except Exception as ex:               # noqa: BLE001 - the hub keeps stderr: a reason, never content
                print(f"failed: {type(ex).__name__}", file=sys.stderr)
                return 1
        print(json.dumps(out, ensure_ascii=False))
        return 0
    if args.command == "record":
        return _record(path, args.tool)
    if args.command == "ui":
        try:
            url = ui.ensure(path)
        except (OSError, RuntimeError) as ex:     # no memory yet, or the page could not start
            print(str(ex), file=sys.stderr)
            return 1
        url += ui._at(args.note, args.view)
        print(url, flush=True)
        if args.open:
            webbrowser.open(url)
        return 0
    if not os.path.exists(path):                 # looking does not change (or create) the memory
        out: object = [] if args.command == "sources" else {"tool": args.tool, "records": 0, "notes": 0}
    else:
        try:
            with WholeMemory(path) as mem:
                if args.command == "sources":
                    out = mem.sources()
                else:
                    out = {"tool": args.tool, **mem.forget_tool(args.tool, dry_run=args.dry_run)}
        except (RuntimeError, ValueError) as ex:  # a memory newer than this luoke-mem; a tool not forgotten so
            print(str(ex), file=sys.stderr)       # (luoke-mem's own: the user's words, forgotten one by one)
            return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


def _record(path: str, tool: str) -> int:
    """Records from standard input, JSON Lines, for `tool`: what Memory.record() takes, each line on its own (one bad
    line does not stop the rest). Credentials are masked by the library as ever; sensitive context is marked here
    when the line does not say (luoke-mem#7). Never as luoke-mem: what is written there is the user's own word."""
    from .memory import KEEPER, Memory, TooNew
    from .sensitive import sensitive_kinds
    if not tool or tool == KEEPER or not all(c.isalnum() or c in "-_." for c in tool):
        print(f"不能以这个工具的名义写：{tool!r}", file=sys.stderr)
        return 2
    try:
        mem = Memory(path, tool=tool)
    except (OSError, RuntimeError, TooNew) as ex:
        print(str(ex), file=sys.stderr)
        return 2
    failed = 0
    with mem:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                r = json.loads(line)
                if not isinstance(r, dict) or not isinstance(r.get("text"), str):
                    raise ValueError("each line is an object with text")
                data = r.get("data")
                if data is not None and not isinstance(data, dict):
                    raise ValueError("data is an object")
                sensitive = r.get("sensitive")
                rid = mem.record(str(r.get("speaker")), str(r.get("kind")), r["text"], topic=r.get("topic"),
                                 data=data, ref=None if r.get("ref") is None else str(r["ref"]),
                                 happened_at=r.get("happened_at"),
                                 sensitive=sensitive_kinds(r["text"]) if sensitive is None else sensitive)
                print(json.dumps({"id": rid}), flush=True)
            except (ValueError, TypeError) as ex:
                failed += 1
                print(json.dumps({"error": str(ex)}, ensure_ascii=False), flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
