#!/usr/bin/env python3
"""Run the evaluation set of luoke-mem.topics (bench/topics_set.py) through 螺壳主控 and score it.

  python3 bench/run_topics.py --hub SOCKET --label "..." [--out DIR] [--cases N]

The hub must be named (a lab hub of luoke-core's release script, say), never found on its own: an evaluation
does not go to a hub in daily use. Each case goes through the app's own code: topics.usable() and
topics.sources() read the records into sources with their mails, topics.messages() builds the request, batched by
the hub's own limits; topics.parse() reads the answer. Scored on the model's answer, before the library's checks:
  hard     a topic or dislike containing a `never` word (a one-off matter, someone else's, health, a credential),
           or a topic that is only a source's name or Latin words (topics.brand): passes only with none at all
  soft     interest_recall (planted interests found by a field word), specific (of those, with an object word),
           dislike_recall, dislikes_unsaid (dislikes resting on nothing said 没兴趣)
Results -> bench/results/topics/<label>.json with the set's sha256 (the evidence record); `--out DIR` writes
<DIR>/topics/<label>.json instead. Report as run_typing.py's. Stdlib only, Python 3.9."""
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

from luoke_mem import __version__, topics  # noqa: E402
from luoke_mem.hub import Hub, Stop  # noqa: E402
import topics_set as S  # noqa: E402

PATIENCE = 7200


def set_hash(cases) -> str:
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def ask(hub: Hub, messages: list, items: int, waited: list, patience: float = PATIENCE) -> str:
    while True:
        try:
            return hub.chat(topics.TASK, messages, topics.SCHEMA, items=items)
        except Stop as ex:
            wait = ex.retry_after or 30
            if waited[0] + wait > patience:
                raise
            print(f"hub says {ex.code}: waiting {wait:.0f} s", file=sys.stderr)
            time.sleep(wait)
            waited[0] += wait


def filters() -> list[str]:
    """The app's filters on answers given before (S.KEEP, S.DROP): what they wrongly drop or let through, each a
    hard failure. Needs no model."""
    bad = []
    for text, names in S.KEEP:
        kept = topics.parse(json.dumps({"interests": [{"text": text, "evidence": [1]}]}, ensure_ascii=False), 1)["interests"]
        if not kept or topics.brand(text, names):
            bad.append(f"{text}（筛选误伤）")
    bad += [f"{text}（筛选漏过）" for text, names in S.DROP if not topics.brand(text, names)]
    return bad


def run_case(hub: Hub, case: dict, params: dict, waited: list, patience: float = PATIENCE) -> dict:
    rows = [dict(r, ref=r.get("ref")) for r in case["records"] if topics.usable(r["topic"], r["text"], r["data"])]
    srcs = topics.sources(rows)
    overhead = len(topics.PROMPT) + 200
    interests, dislikes, seconds = [], [], 0.0
    for batch in topics.batches(srcs, int(params.get("max_chars") or topics.MAX_CHARS),
                                int(params.get("batch") or topics.BATCH), overhead):
        t0 = time.time()
        content = ask(hub, topics.messages(batch, [], []), len(batch), waited, patience)
        seconds += time.time() - t0
        out = topics.parse(content, len(batch))
        interests += [(i["text"], [batch[n - 1] for n in i["evidence"]]) for i in out["interests"]]
        dislikes += [(d["text"], [batch[n - 1] for n in d["evidence"]]) for d in out["dislikes"]]
    named = [t for t, _ in interests + dislikes]
    hard = [f"{n}（含「{w}」）" for n in named for w in case["never"] if w.lower() in n.lower()]
    hard += [f"{t}（只是来源的名字）" for t, cited in interests
             if topics.brand(t, [s["name"] for s in cited] + case["brands"])]
    found, specific = 0, 0
    for field, obj in case["expect"]:
        hits = [t for t, _ in interests if any(w.lower() in t.lower() for w in field)]
        found += bool(hits)
        specific += any(any(w.lower() in t.lower() for w in obj) for t in hits)
    said = sum(any(any(w in t for w in words) for t, _ in dislikes) for words in case["dislikes"])
    unsaid = [t for t, cited in dislikes if not any("digest-feedback" in s["topics"] for s in cited)]
    return {"case": case["name"], "interests": [t for t, _ in interests], "dislikes": [t for t, _ in dislikes],
            "hard_failures": hard, "interests_found": f"{found}/{len(case['expect'])}", "specific": f"{specific}/{len(case['expect'])}",
            "dislikes_found": f"{said}/{len(case['dislikes'])}", "dislikes_unsaid": unsaid, "seconds": round(seconds, 1),
            "_score": (found, len(case["expect"]), specific, said, len(case["dislikes"]), len(unsaid))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hub", required=True, help="螺壳主控的 socket（实验室实例），必须指名")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", help="写到 <DIR>/topics/<label>.json")
    ap.add_argument("--cases", type=int, help="只跑前 N 条（试跑用）")
    ap.add_argument("--patience", type=float, default=PATIENCE, help="主控说忙时一共最多等多少秒（默认两小时）")
    args = ap.parse_args()
    if not os.path.exists(args.hub):
        print(f"没有这个 socket：{args.hub}", file=sys.stderr)
        return 1
    hub = Hub(args.hub)
    params = hub.task(topics.TASK).get("params") or {}
    cases = S.CASES[:args.cases] if args.cases else S.CASES
    checked = filters()
    for line in checked:
        print(f"filters: {line}", file=sys.stderr)
    waited, items = [0.0], []
    for case in cases:
        items.append(run_case(hub, case, params, waited, args.patience))
        print(f"{items[-1]['case']}: interests {items[-1]['interests_found']} (specific {items[-1]['specific']}), "
              f"dislikes {items[-1]['dislikes_found']}, "
              f"hard {len(items[-1]['hard_failures'])}", file=sys.stderr)
    scores = [i.pop("_score") for i in items]
    sums = [sum(sc[k] for sc in scores) for k in range(6)]
    hard = sum(len(i["hard_failures"]) for i in items) + len(checked)
    report = {"task": f"luoke-mem.{topics.TASK}", "label": args.label, "accessory_version": __version__,
              "set": "bench/topics_set.py", "set_sha256": set_hash(S.CASES), "cases": len(items),
              "filters_sha256": set_hash([S.KEEP, S.DROP]), "filter_failures": checked,
              "combo": hub.model, "params": params, "machine": {"model": platform.machine(), "system": platform.platform()},
              "date": time.strftime("%Y-%m-%d %H:%M:%S"), "hard_pass": hard == 0,
              "score": {"hard_failures": hard, "interest_recall": round(sums[0] / max(1, sums[1]), 3),
                        "specific": round(sums[2] / max(1, sums[1]), 3),
                        "dislike_recall": round(sums[3] / max(1, sums[4]), 3), "dislikes_unsaid": sums[5],
                        "seconds": round(sum(i["seconds"] for i in items), 1)},
              "items": items}
    folder = os.path.join(args.out or os.path.join(ROOT, "bench", "results"), "topics")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{args.label}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(json.dumps(report["score"], ensure_ascii=False), "->", path)
    return 0 if report["hard_pass"] else 2


if __name__ == "__main__":
    sys.exit(main())
