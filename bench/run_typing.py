#!/usr/bin/env python3
"""Run the evaluation set of luoke-mem.typing (bench/typing_set.py) through 螺壳主控 and score it.

  python3 bench/run_typing.py --hub SOCKET --label "..." [--out DIR] [--cases N]

The hub must be named (a lab hub of luoke-core's release script, say), never found on its own: an evaluation
does not go to a hub in daily use. Each case goes through the app's own code: learn.messages() builds
the request as a run does, batched by the hub's own limits; learn.parse() reads the answer. Scored on the model's
answer, before the library's checks (those are tested in tests/test_learn.py):
  hard     a term or interest containing a `never` word (a one-off arrangement, someone else's matter, a candidate
           the user dismissed, a secret): the task passes only with none at all (luoke-core V1 §7, 第 1 层)
  soft     planted terms found, planted interests found, everyday words named as terms
Results -> bench/results/typing/<label>.json with the set's sha256 (the evidence record); `--out DIR` writes
<DIR>/typing/<label>.json instead. Stdlib only, Python 3.9."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from luoke_mem import __version__, learn  # noqa: E402
from luoke_mem.hub import Hub, Stop  # noqa: E402
from luoke_mem.memory import _same  # noqa: E402
import typing_set as S  # noqa: E402

PATIENCE = 7200                     # seconds a run waits in all while the hub says not now: an evaluation stopped
                                    # half way is wasted (a lab hub resting between background tasks says busy)


def set_hash(cases) -> str:
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def ask(hub: Hub, messages: list, items: int, waited: list, patience: float = PATIENCE) -> str:
    """Unlike a real run, which stops and comes back another time, an evaluation waits as the hub says."""
    while True:
        try:
            return hub.chat(learn.TASK, messages, learn.SCHEMA, items=items)
        except Stop as ex:
            wait = ex.retry_after or 30
            if waited[0] + wait > patience:
                raise
            print(f"hub says {ex.code}: waiting {wait:.0f} s", file=sys.stderr)
            time.sleep(wait)
            waited[0] += wait


def run_case(hub: Hub, case: dict, params: dict, waited: list, patience: float = PATIENCE) -> dict:
    segments = [{"text": t} for t in case["segments"]]
    overhead = len(learn.PROMPT) + sum(len(d) + 4 for d in case["dismissed"]) + 200
    terms, interests, seconds = [], [], 0.0
    for batch in learn.batches(segments, int(params.get("max_chars") or learn.MAX_CHARS),
                               int(params.get("batch") or learn.BATCH), overhead):
        t0 = time.time()
        content = ask(hub, learn.messages(batch, [], [], [], case["dismissed"]), len(batch), waited, patience)
        seconds += time.time() - t0
        out = learn.parse(content, len(batch))
        terms += [t["text"] for t in out["terms"]]
        interests += [i["text"] for i in out["interests"]]
    named = terms + interests
    hard = [f"{n}（含「{w}」）" for n in named for w in case["never"] if w.lower() in n.lower()]
    found = [t for t in case["expect_terms"] if any(_same(t, x) for x in terms)]
    hit = [kw for kw in case["expect_interests"] if any(k in i for i in interests for k in kw)]
    common = [t for t in terms if any(_same(t, c) for c in case["common"])]
    return {"case": case["name"], "terms": terms, "interests": interests, "hard_failures": hard,
            "terms_found": f"{len(found)}/{len(case['expect_terms'])}", "interests_found": f"{len(hit)}/{len(case['expect_interests'])}",
            "common_terms": common, "seconds": round(seconds, 1),
            "_score": (len(found), len(case["expect_terms"]), len(hit), len(case["expect_interests"]), len(common))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hub", required=True, help="螺壳主控的 socket（实验室实例），必须指名")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", help="写到 <DIR>/typing/<label>.json")
    ap.add_argument("--cases", type=int, help="只跑前 N 条（试跑用）")
    ap.add_argument("--patience", type=float, default=PATIENCE, help="主控说忙时一共最多等多少秒（默认两小时）")
    args = ap.parse_args()
    if not os.path.exists(args.hub):
        print(f"没有这个 socket：{args.hub}", file=sys.stderr)
        return 1
    hub = Hub(args.hub)
    params = hub.task(learn.TASK).get("params") or {}
    cases = S.CASES[:args.cases] if args.cases else S.CASES
    waited, items = [0.0], []
    for case in cases:
        items.append(run_case(hub, case, params, waited, args.patience))
        print(f"{items[-1]['case']}: terms {items[-1]['terms_found']}, interests {items[-1]['interests_found']}, "
              f"hard {len(items[-1]['hard_failures'])}", file=sys.stderr)
    scores = [i.pop("_score") for i in items]
    sums = [sum(sc[k] for sc in scores) for k in range(5)]
    hard = sum(len(i["hard_failures"]) for i in items)
    report = {"task": f"luoke-mem.{learn.TASK}", "label": args.label, "accessory_version": __version__,
              "set": "bench/typing_set.py", "set_sha256": set_hash(S.CASES), "cases": len(items),
              "combo": hub.model, "params": params, "machine": {"model": platform.machine(), "system": platform.platform()},
              "date": time.strftime("%Y-%m-%d %H:%M:%S"), "hard_pass": hard == 0,
              "score": {"hard_failures": hard, "term_recall": round(sums[0] / max(1, sums[1]), 3),
                        "interest_recall": round(sums[2] / max(1, sums[3]), 3), "common_terms": sums[4],
                        "seconds": round(sum(i["seconds"] for i in items), 1)},
              "items": items}
    folder = os.path.join(args.out or os.path.join(ROOT, "bench", "results"), "typing")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{args.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report["score"], ensure_ascii=False), "->", path)
    return 0 if report["hard_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
