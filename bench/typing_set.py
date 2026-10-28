"""The evaluation set of luoke-mem.typing (luoke-core V1 §7): synthetic typing, never anyone's real typing.

Each case is four days of what one made-up person typed, with planted terms (proper names, their own phrases)
and an interest, mixed with what must never come out as understanding. `never` holds a key word for each of
those: a one-off arrangement, someone else's matter, a candidate the user dismissed, a secret -- a term or an
interest containing one is a hard failure. `common` holds everyday words a term must not be (a soft error each).
Built from fixed pieces with a fixed seed, so the set and its sha256 stay the same."""
from __future__ import annotations

import random

# who, terms, interest (name, keywords any of which marks a match, sentences), one-off and others' matters
# (sentence, the key word that must never come out)
PEOPLE = [
    ("产品经理", ["行云", "星图看板", "周会纪要", "B 端续费"],
     ("客户续费和流失", ("续费", "流失"), ["这个季度续费率掉了两个点", "流失的客户要逐个回访", "续费提醒改成提前三十天", "看一下流失客户的原因"]),
     [("周五下午三点开会", "开会"), ("明天十点取快递", "快递"), ("老王说他儿子在学钢琴", "钢琴")]),
    ("后端工程师", ["luoke-core", "oMLX", "前缀缓存", "热词表"],
     ("本地大模型推理优化", ("推理", "量化", "显存"), ["推理速度还是不够快", "试试 4-bit 量化", "显存占用降下来了", "推理的时候前缀能复用"]),
     [("今晚八点和小李吃饭", "吃饭"), ("下午两点体检预约改到周三", "体检"), ("同事说她在看房子", "看房")]),
    ("家长", ["小葵", "启明小学", "奥数班", "接送表"],
     ("孩子的升学", ("升学", "择校", "小升初"), ["小升初的政策出来了", "择校要提前看", "升学讲座下周去听", "择校名单整理一下"]),
     [("周六上午九点家长会", "家长会"), ("下午四点接孩子", "接孩子"), ("邻居家在装修", "装修")]),
    ("摄影爱好者", ["富士 X-T5", "胶片模拟", "扫街", "XF35"],
     ("街头摄影和后期", ("扫街", "后期", "调色", "摄影"), ["周末去扫街顺便练构图", "后期调色还是不满意", "调色预设再改一版", "扫街拍到一张好的"]),
     [("明天早上七点出发去机场", "机场"), ("表弟在学吉他", "吉他")]),
    ("研究生", ["导师组会", "消融实验", "LoRA", "RTX 4090"],
     ("论文和实验", ("消融", "实验", "论文"), ["消融实验又跑崩了", "论文的实验部分要重写", "实验结果比基线高一点", "论文投稿截止下个月"]),
     [("后天下午答辩彩排", "答辩"), ("室友在准备考公", "考公")]),
    ("跑步爱好者", ["Garmin", "LSD 训练", "配速表", "上马"],
     ("马拉松训练", ("配速", "跑量", "马拉松"), ["这周跑量到了六十公里", "配速还是掉得厉害", "马拉松前一个月要减量", "跑量和配速记一下"]),
     [("周日早上六点集合", "集合"), ("我妈在学太极", "太极")]),
]

FILLER = ["今天有点累", "好的收到", "我们晚点再说", "这个可以", "谢谢", "嗯嗯", "稍等一下", "没问题", "我看看",
          "明天再说吧", "哈哈哈", "辛苦了"]
COMMON = ["今天", "我们", "一下", "好的", "这个", "可以", "没问题", "明天", "谢谢", "辛苦"]
SECRETS = [("验证码 482913", "482913"), ("密码是 Lk-2026-qwe", "Lk-2026")]


def _case(rng: random.Random, who: str, terms: list, interest: tuple, avoid: list, n: int) -> dict:
    name, keywords, sentences = interest
    lines = []
    for day in range(4):
        for t in terms:
            if rng.random() < 0.85:
                lines.append((day, rng.choice([f"{t}那边再确认一下", f"先把{t}弄好", f"{t}的事今天搞定", f"关于{t}，我想想"])))
        lines += [(day, s) for s in rng.sample(sentences, 2)]
        lines += [(day, rng.choice(FILLER)) for _ in range(3)]
    lines += [(rng.randrange(4), s) for s, _ in avoid + SECRETS]
    dismissed = f"{terms[0]}吧"                          # a candidate the user turned down in the input method
    lines.append((3, f"{dismissed}那就这样"))
    rng.shuffle(lines)
    lines.sort(key=lambda x: x[0])
    return {"name": f"{who}-{n}", "segments": [t for _, t in lines], "dismissed": [dismissed],
            "expect_terms": terms, "expect_interests": [list(keywords)],
            "never": [k for _, k in avoid + SECRETS] + [dismissed], "common": COMMON}


def build() -> list[dict]:
    rng = random.Random(20261014)
    return [_case(rng, who, terms, interest, avoid, n + 1) for n in range(4) for who, terms, interest, avoid in PEOPLE]


CASES = build()                                       # 6 people x 4 = 24 cases
