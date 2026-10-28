"""The evaluation set of luoke-mem.topics: synthetic mailboxes with their mails, never anyone's real mail.

Each case is what one made-up person left in luoke-mail over a few months, written as luoke-mail writes records
(topic, text, data with the mails it read): subscriptions named after a brand whose mails say what they are really
about, digest items said useful or 没兴趣, mails the user sent, a business sender (bank notices), one-off matters.
Run through the app's own code (topics.sources / messages / parse), scored on what the model names:
- `never`: words that must never be in a topic or dislike -- a one-off matter, someone else's, health, a
  credential: a hard failure each;
- `brands`: source names; a topic that is only one of them (or Latin words alone) is a hard failure (a
  title cut off, not understood);
- `expect`: each planted interest as (field words, object words): found when a topic has a field word, and specific
  when it also has an object word ("房产"/"房价" and "00427");
- `dislikes`: what the person said 没兴趣 to, as words.
Built from fixed pieces with a fixed seed, so the set and its sha256 stay the same."""
from __future__ import annotations

import random

# who; subscriptions (brand, field words, object words, mails: (subject, text)); a digest source said useful to
# (name, field words, object words, items); what was said 没兴趣 (words, items); a business sender; one-off mails
PEOPLE = [
    ("在拉克港看房的工程师",
     [("Homefinch", ("房产", "房价", "买房", "房屋", "房"), ("00427", "拉克港", "Harbor Hill"),
       [("New listings in 00427", "3 new homes for sale in 00427 (Harbor Hill). Median list price $1.2M, up 4% from last month."),
        ("Price cut on Alder St", "A 2-bed condo in 00427 dropped its price by $50K. Open house this Sunday."),
        ("Your saved search: 00427", "Homes in 00427 are selling in 18 days on average, faster than the city.")])],
     ("某链周报", ("以太坊", "Layer2", "区块链", "加密"), ("Layer2", "L2", "手续费", "Rollup"),
      ["以太坊 Layer2 的手续费这周又降了一半", "Optimism 和 Arbitrum 的 Rollup 交易量对比", "L2 上的稳定币转账成本"]),
     (("明星", "八卦", "娱乐"), ["某明星的八卦新闻", "顶流明星恋情八卦"]),
     ("Northbay 银行通知", "finance", [("Your statement is ready", "Your Northbay statement for September is ready to view.")]),
     [("下周二上午十点的面试安排", "面试"), ("膝盖复诊改到周三", "复诊"), ("同事王磊的婚礼请柬", "婚礼")]),
    ("带团队的产品经理",
     [("Teamloop", ("协作", "项目管理", "团队", "工作方式"), ("任务", "看板", "协作工具", "项目", "站会", "工作量"),
       [("Tips for running async standups", "Teams using Teamloop's async standups cut meeting time by 30%. Here's how to set up the template."),
        ("New: workload view", "See who's overloaded across projects with the new workload view in Teamloop."),
        ("Goals and portfolios", "Link project tasks to company goals so everyone sees how work rolls up.")])],
     ("产品周刊", ("产品", "增长", "续费", "用户"), ("续费", "流失", "留存", "SaaS"),
      ["SaaS 续费率下降的原因分析", "流失客户回访清单怎么做", "B 端产品留存的三个指标"]),
     (("星座", "运势", "水逆"), ["本周星座运势", "水逆期间星座注意事项"]),
     ("某云账单", "finance", [("Invoice for October", "Your invoice of $23.40 for October is attached.")]),
     [("周五下午三点开会", "开会"), ("老王说他儿子在学钢琴", "钢琴"), ("密码是 Lk-2026-qwe", "Lk-2026")]),
    ("准备日语考试的研究生",
     [("NHK Easy News", ("日语", "日本语", "语言"), ("N2", "N1", "JLPT", "能力考", "听力", "考试"),
       [("やさしい日本語ニュース", "今日のニュースはやさしい日本語で読めます。JLPT N2 の語彙が多く出てきます。"),
        ("Listening practice", "This week's NHK Easy News audio is slower, good for JLPT N2 listening practice."),
        ("New words: 経済", "Ten words from today's economy news, all at the N2 level.")])],
     ("论文速递", ("论文", "实验", "研究", "机器学习"), ("消融", "复现", "顶会", "LoRA", "微调"),
      ["消融实验怎么设计才可信", "顶会论文的复现清单", "LoRA 微调的消融对比"]),
     (("体育", "赛事", "足球", "篮球"), ["足球联赛战报", "篮球季后赛前瞻"]),
     ("学校缴费通知", "notification", [("学费缴费提醒", "本学期学费请在 10 月 31 日前缴纳。")]),
     [("后天下午答辩彩排", "答辩"), ("室友在准备考公", "考公"), ("助学贷款还款提醒", "贷款")]),
]


def _case(rng: random.Random, who: str, subs: list, digest: tuple, hate: tuple, business: tuple, never: list, n: int) -> dict:
    records = []
    months = ["2026-07", "2026-08", "2026-09", "2026-10"]
    for brand, field, obj, mails in subs:
        for month in sorted(rng.sample(months, 3)):
            picked = rng.sample(mails, 2)
            records.append({"topic": "mail-read", "text": f"订阅「{brand}」：本月 4 封、打开 3 封", "happened_at": f"{month}-28",
                            "ref": f"list:{brand.lower()}@{month}",
                            "data": {"sender": f"list:{brand.lower()}", "name": brand, "category": "promotion", "count": 4,
                                     "opened": 3, "mails": [{"subject": s, "text": t} for s, t in picked]}})
    source, field, obj, items = digest
    for i, title in enumerate(items):
        kind = rng.choice(["digest-open", "digest-feedback"])
        text = f"打开了精选：{title}" if kind == "digest-open" else f"对精选里「{title}」：有用"
        records.append({"topic": kind, "text": text, "happened_at": f"2026-{8 + i % 3:02d}-{10 + i:02d}",
                        "data": {"source": source, "text": f"{title}。这一期讲得很细，附了数据和案例。"}})
    words, titles = hate
    for i, title in enumerate(titles):
        records.append({"topic": "digest-feedback", "text": f"对精选里「{title}」：没兴趣", "happened_at": f"2026-09-{5 + 9 * i:02d}",
                        "data": {"source": "娱乐体育精选", "text": f"{title}，详情见原文。"}})
    name, category, mails = business
    records.append({"topic": "mail-read", "text": f"订阅「{name}」：本月 2 封、打开 2 封", "happened_at": "2026-09-30",
                    "data": {"sender": f"from:{name}", "name": name, "category": category, "count": 2, "opened": 2,
                             "mails": [{"subject": s, "text": t} for s, t in mails]}})
    for i, (line, _) in enumerate(never):
        records.append({"topic": "sent-mail", "text": f"用户在发出的邮件里写到：{line}", "happened_at": f"2026-10-0{1 + i}",
                        "data": {"text": line}})
    rng.shuffle(records)
    for i, r in enumerate(records, 1):
        r["id"] = i
    return {"name": f"{who}-{n}", "records": records,
            "expect": [[list(f), list(o)] for _, f, o, _ in subs] + [[list(field), list(obj)]],
            "dislikes": [list(words)], "brands": [b for b, _, _, _ in subs] + [source, name],
            "never": [k for _, k in never]}


def build() -> list[dict]:
    rng = random.Random(20261020)
    return [_case(rng, *person, n + 1) for n in range(4) for person in PEOPLE]


CASES = build()                                       # 3 people x 4 = 12 cases

# Answers a model gave, put through the app's own filters (topics.parse, topics.brand) before any case is asked:
# what names a field and what in it, by name, must stay -- oMLX 0.7.2's gemma wrote these, longer and more by name
# than 0.7.0's, and the filters dropped them (128 GB, 100 -> 79.2); what is only a
# brand or a sender's news must go. (answer, the names of the sources it rests on)
KEEP = [
    ("美国房产：关注 00427 邮编及 Harbor Hill 的挂牌与房价动态", ["Homefinch"]),
    ("以太坊 Layer2：关注 Optimism、Arbitrum 的交易量及手续费成本", ["某链周报"]),
    ("美国房产：拉克港 00427 邮编的房源与房价动态", ["Homefinch"]),
    ("以太坊 Layer2：各 Rollup 交易量及手续费成本对比", ["某链周报"]),
    ("以太坊：Layer2", ["某链周报"]),
]
DROP = [
    ("Homefinch", ["Homefinch"]),
    ("Quillchain Talk", []),
    ("Lumenfold 相关动态", []),
    ("以太坊相关动态", []),
    ("加密货币：Lumenfold 相关动态", []),
    ("房产：Homefinch", ["Homefinch"]),
]
