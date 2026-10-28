"""The memory store. Records say what happened, notes say what is understood; every note cites the records it
rests on. See DESIGN.md for the principles behind each rule here."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import time

from .sensitive import CONFIRM_KINDS, SENSITIVE_KINDS, mask_secrets, never_kept, sensitive_kinds

SPEAKERS = ("user", "third_party", "system")          # who said it (principle 2)
RECORD_KINDS = ("statement", "activity", "receipt")   # said or chose / did (attention only) / handled a suggestion
# taste: what a tool's own feature goes by (luoke-mail's digest), kept apart from what is understood of the user
# (ADR 0012 amendment): never asked about, never a fact about the person
NOTE_KINDS = ("fact", "interest", "dislike", "preference", "term", "taste")
ORIGINS = ("user", "inferred")
STATUSES = ("proposed", "active", "rejected", "superseded", "observed", "past")   # observed: 观察中 (ADR 0011, 0.16.1);
# past: 以前的事 (0.20) -- what changes, with nothing newer than PAST_DAYS behind it: kept, not used as now, not asked
LABELS = {"fact": "关于用户", "interest": "关注的话题", "dislike": "不感兴趣", "preference": "处理习惯", "term": "常用说法",
          "taste": "订阅精选的口味"}
# What the user reads, one copy for every tool that shows them (the memory page, luoke-mail, luoke-agent): the kinds as
# addressed to the user (LABELS above speak of the user, for a model), and the tools by the names they go by.
KIND_NAMES = {"fact": "关于你", "interest": "关注的话题", "dislike": "不感兴趣", "preference": "处理习惯", "term": "常用说法",
              "taste": "精选的口味"}
TOOL_NAMES = {"luoke-mail": "邮件助手", "luoke-ime": "螺壳输入法", "luoke-mem": "螺壳记忆", "luoke-core": "螺壳",
              "luoke-agent": "螺壳", "luoke-audit": "螺壳体检"}
# What confirming does, by kind: said with a guess when it is asked about (ADR 0011: say why it is asked and what
# confirming changes). One copy, like the names above.
KIND_PURPOSES = {"fact": "确认后，螺壳的工具会把它当作关于你的事实，比如邮件助手分拣、起草回复时",
                 "interest": "确认后，邮件助手挑订阅精选时会优先这个话题",
                 "dislike": "确认后，订阅精选会少挑这类内容",
                 "preference": "确认后，邮件助手会照这个习惯处理（归档、标已读会自动做并告诉你，能撤回；删除照旧要你点）",
                 "term": "确认后，输入法会把它排在候选的前面",
                 "taste": "确认后，邮件助手挑订阅精选时会参考它；它只管精选挑什么，不算关于你的了解"}
ASK_REASONS = {"interest": "你最近常提到「{text}」，想确认是不是你长期关注的",
               "term": "「{text}」你打过好几次，想确认它是你常用的说法",
               "dislike": "你几次跳过「{text}」这类内容，想确认是不是不感兴趣",
               "preference": "你几次都这样处理：{text}，想确认以后照这个习惯建议",
               "fact": "想确认「{text}」这件关于你的事"}


# what is uncertain, by what the guess rests on (the reason must match the evidence)
ASK_REASONS_BY = {("interest", "own-label"): "你在邮箱里建了和「{text}」有关的标签，不过标签多半是整理用的，想确认这是不是你长期关注的",
                  ("interest", "mail-cleared"): "你清理过和「{text}」有关的邮件，想确认这是不是你长期关注的",
                  ("preference", "mail-acted"): "你几次自己这样处理：{text}，想确认以后照这个习惯"}


def ask_reason(note: dict, sources: set | None = None) -> str:
    """Why a guess is asked about, for the user (ADR 0011 amendment): what is uncertain, then what confirming
    changes -- never where it comes from (that is the evidence, shown on its own). `sources`: the topics of the
    records it rests on, so the uncertainty said is the one there is."""
    kind = note.get("kind")
    head = next((ASK_REASONS_BY[(kind, t)] for t in sorted(sources or ()) if (kind, t) in ASK_REASONS_BY),
                ASK_REASONS.get(kind, "想确认「{text}」")).format(text=note.get("text", ""))
    tail = KIND_PURPOSES.get(note.get("kind"))
    return f"{head}。{tail}。" if tail else f"{head}。"


# Where what was taken down directly comes from, as told to the user: (tool, who said the records it rests on).
SOURCE_PHRASES = {("luoke-mail", "third_party"): "根据你收到的邮件", ("luoke-mail", "user"): "根据你发出的邮件和你在邮件助手里的操作",
                  ("luoke-ime", "user"): "根据你打的字", ("luoke-audit", "user"): "根据你在螺壳体检里的选择",
                  ("luoke-audit", "system"): "螺壳体检自动做的"}
# Facts from others that are taken down without asking (ADR 0011), and the evidence that takes: what a cited
# record's data says of how often the sender wrote (count), over how many months (months), how many the user opened
# (opened), and the last month there was one (last, YYYY-MM). Agreed with luoke-mail, 2026-10-17. Anything that
# says who the user is or how they live (school, work, housing) is not here: it is still asked.
# Facts from others take effect by their evidence alone since 0.23. LOW_RISK and MONEY are kept
# for tools that import them; nothing weighs them any more.
LOW_RISK = {"membership": "会员", "travel": "出行", "service": "用的服务"}
EVIDENCE = {"count": 6, "months": 3, "opened": 2, "within_months": 6}
# Money is asked about all the same: where one banks, invests or borrows is not a plain fact to take down unasked,
# though not sensitive context either (ADR 0004 is unchanged; this only keeps such facts among the questions).
# Only a backstop (top-level docs/preset-rules-2026-10-17.md §4): a word list always misses some.
MONEY = re.compile(r"银行|信用卡|储蓄卡|借记卡|证券|股票|基金|期货|保险|贷款|借贷|信托|理财|支付宝|微信支付|PayPal|"
                   r"数字货币|加密货币|虚拟货币|交易所|钱包|比特币|以太坊|"
                   r"(?i:bank|brokerage|credit card|insurance|loan|crypto|wallet|bitcoin)")

# What records can tell (ADR 0012 amendment, DESIGN「什么记录能推出什么理解」): for each kind of understanding, the
# record topics it can rest on and what makes it likely there -- (records, different days, days from first to last).
# Anything else drawn from the user's records is a guess at most, whatever the tool says. Coarse first
# ("先粗后细"): loose defaults, tightened or loosened for each user by their answers (`_bar`).
# Terms and interests from typing: luoke-mem's learn counts the times itself and cites the latest record of each day.
GRADES = {
    "fact": {"sent-mail": (2, 2, 7)},
    "interest": {"sent-mail": (2, 2, 7), "own-site": (2, 2, 7), "digest-feedback": (2, 2, 7), "mail-read": (3, 3, 60),
                 "digest-open": (3, 2, 28), "ime-commit": (3, 3, 0)},
    "dislike": {"digest-feedback": (2, 2, 7)},
    "preference": {"mail-override": (2, 2, 7)},       # testing period: the same change twice, a week apart
    "term": {"ime-commit": (3, 3, 0)},
    "taste": None,                                     # a tool's own feature: anything of the user's will do
}
# what is business, not reading ("某银行通知这种也能算…关注话题"): bills, banks, security, trips,
# orders, service notices -- opening them says nothing of what the user cares about
BUSINESS = re.compile(r"账单|对账|银行|信用卡|支付|付款|收据|发票|订单|发货|物流|快递|行程|航班|机票|登机|酒店|预订|"
                      r"验证|安全|登录|密码|通知|提醒|服务条款|续费|到期|"
                      r"(?i:statement|receipt|invoice|order|shipping|itinerary|booking|flight|security|sign.?in|"
                      r"verify|password|alert|notification|billing|renewal)")
BUSINESS_CATEGORIES = {"账单", "通知", "交易", "安全", "行程", "订单", "收据", "transaction", "notification", "bill",
                       "security", "travel", "receipt", "finance", "phishing"}   # luoke-mail's triage codes (10-19)
LATIN_ONLY = re.compile(r"^[\sA-Za-z0-9.&'+\-]+$")   # "Homefinch", "Northbay Bank": a brand alone, never a topic (10-19)
SENDERISH = re.compile(r"(相关)?(动态|通知|资讯|更新|消息)$")   # "Brightdock 相关动态": a sender, not a topic


def _senderish(text: str | None) -> bool:
    """A name that is a sender's news and nothing else ("Brightdock 相关动态", "以太坊相关动态", "Homefinch"): no field and
    its object. "美国房产：关注 00427 邮编的房价动态" names a field and what in it, and is a topic (the
    rule is for a topic that is only a brand or a source, not for one that says what in it, by name)."""
    text = text or ""
    return ("：" not in text and ":" not in text and bool(SENDERISH.search(text))) or bool(
        LATIN_ONLY.match(SENDERISH.sub("", text).strip() or "x"))
MAX_ABOUT = 8                   # interests about the user drawn from mail, at most: few and right
# and what else a topic needs, from its records' data: so many opened in all, so many senders (one subscription
# read is that sender, not a topic)
ALSO = {"mail-read": {"opened": 5, "senders": 2}}
# What a habit may do by itself once the user confirmed it: only what can be undone, and the user is told (ADR 0012
# amendment 4). Moving to the bin or to spam stays a suggestion.
AUTO_ACTIONS = ("archive", "read", "label", "filter")   # + labelling, a filter suggestion (top-level ADR 0016)
# luoke-mail's triage categories, as the user reads them in a habit ("某商城的促销邮件都归档")
CATEGORY_NAMES = {"work": "工作", "personal": "个人", "finance": "财务", "notification": "通知", "promotion": "促销",
                  "social": "社交", "phishing": "可疑", "other": "其他"}
ACTION_NAMES = {"archive": "归档", "read": "标成已读", "trash": "移到垃圾箱", "spam": "标成垃圾邮件", "flag": "加星标",
                "keep": "留在收件箱", "label": "加上标签", "filter": "按规则处理"}


def action_name(action: str, data: dict | None = None) -> str:
    """A habit's action as the user reads it: 归档, 加上标签「购物」."""
    name = ACTION_NAMES.get(action, action)
    if action == "label" and isinstance(data, dict) and data.get("label"):
        name += f"「{data['label']}」"
    return name

# The schema only ever grows (new tables, nullable columns, indexes, kinds): a tool carrying an older luoke-mem
# keeps working on a file a newer one has touched. PRAGMA user_version says the newest schema that opened the
# file (the remainder after the thousands); a change older libraries could not live with starts a new thousand,
# and they refuse it.
SCHEMA_VERSION = 7          # 5: facts from others waiting to be asked became 观察中 (ADR 0011 amendment); 6: the
                            # agent's list (ADR 0012): asks.evidence, rank, action, key; 7: notes.data_json (habits)
# Sensitive context (top-level ADR 0004) moves a file to the next thousand when the first of it is written:
# libraries before 0.8 would hand it to any tool, so they refuse the file then; until then they still work.
PROTECTED = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
  id INTEGER PRIMARY KEY, tool TEXT NOT NULL, speaker TEXT NOT NULL, kind TEXT NOT NULL, topic TEXT,
  text TEXT NOT NULL, data_json TEXT, ref TEXT, happened_at TEXT NOT NULL, recorded_at TEXT NOT NULL,
  sensitive TEXT
);
CREATE INDEX IF NOT EXISTS records_topic ON records (topic, recorded_at);
CREATE INDEX IF NOT EXISTS records_tool ON records (tool, happened_at);
CREATE TABLE IF NOT EXISTS notes (
  id INTEGER PRIMARY KEY, tool TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, origin TEXT NOT NULL,
  status TEXT NOT NULL, superseded_by INTEGER, valid_from TEXT, valid_to TEXT, formed_at TEXT NOT NULL,
  decided_at TEXT, why TEXT, topic TEXT, sensitive TEXT
);
CREATE TABLE IF NOT EXISTS cites (
  note_id INTEGER NOT NULL, record_id INTEGER NOT NULL, PRIMARY KEY (note_id, record_id)
);
CREATE INDEX IF NOT EXISTS cites_record ON cites (record_id);
CREATE TABLE IF NOT EXISTS grants (
  tool TEXT NOT NULL, category TEXT NOT NULL, status TEXT NOT NULL, why TEXT, at TEXT NOT NULL,
  PRIMARY KEY (tool, category)
);
CREATE TABLE IF NOT EXISTS learning (
  name TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL, record_ids TEXT NOT NULL,
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asks (
  id INTEGER PRIMARY KEY, tool TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, why TEXT,
  about_note INTEGER, about_kind TEXT, urgency TEXT NOT NULL, answer TEXT NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT, decided_at TEXT, answer_record INTEGER, sensitive TEXT
);
"""
ASK_KINDS = ("question", "suggestion")
URGENCY = ("urgent", "normal", "low")
ASK_STATUSES = ("open", "answered", "dismissed", "withdrawn", "later", "never")
COLUMNS = (("notes", "topic"), ("records", "sensitive"), ("notes", "sensitive"),   # added since the first schema
           ("asks", "evidence"), ("asks", "rank"), ("asks", "action"), ("asks", "key"), ("notes", "data_json"))
# How sure the memory is of an understanding (ADR 0012), read off what it already keeps: a guess is not used, only
# shown on the page; likely is in effect without the user's word; sure is what the user confirmed or said.
CONFIDENCE = {"guess": "status IN ('proposed', 'observed')",
              "likely": "status='active' AND decided_at IS NULL AND origin<>'user'",
              "sure": "status='active' AND (decided_at IS NOT NULL OR origin='user')"}
CONFIDENCE_NAMES = {"guess": "猜测", "likely": "比较确定", "sure": "确定"}


def confidence(note: dict) -> str | None:
    """guess / likely / sure, or None for what the user said is wrong (rejected) and older versions."""
    if note.get("status") in ("proposed", "observed"):
        return "guess"
    if note.get("status") == "active":
        return "sure" if note.get("decided_at") or note.get("origin") == "user" else "likely"
    return None


class TooNew(RuntimeError):
    """The memory file was written by a newer luoke-mem in a way this one cannot keep (for example it holds
    sensitive context this one would hand to any tool): the tool should say to update itself."""


KEEPER = "luoke-mem"                 # the memory itself: its own understanding, and the user's words about any (ADR 0009)
TEST_ENV = "LUOKE_MEM_TEST"          # set by the tests: the real memory and hub are refused from then on


def luoke_home() -> str:
    return os.environ.get("LUOKE_HOME") or os.path.join(os.path.expanduser("~"), ".luoke")


def _not_the_real_one(path: str) -> None:
    """Under test, never the user's real memory, whatever path a test passes or falls back to (a
    sibling project's tests once wrote into real settings by the order modules loaded in)."""
    if os.environ.get(TEST_ENV):
        real = os.path.realpath(os.path.join(os.path.expanduser("~"), ".luoke"))
        p = os.path.realpath(os.path.abspath(path))
        if p == real or p.startswith(real + os.sep):
            raise RuntimeError(f"测试里不打开真实的记忆：{path}")


def default_path() -> str:
    return os.path.join(luoke_home(), "mem.db")


def _now(now: str | None) -> str:
    return now or time.strftime("%Y-%m-%d %H:%M:%S")


def _same(a: str, b: str) -> bool:
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    return norm(a) == norm(b)


# What changes and what does not ("有的是十年前的信息"; ADR 0012 item 2): a name, a school one went
# to, family, a birthday stay true; work, the city, interests, the accounts and services one uses, habits change.
# What changes, whose newest evidence is older than PAST_DAYS, is 以前的事 (status 'past'). Coarse first: a kind
# whose past notes the user keeps saying are still so waits longer (`_past_days`).
PAST_DAYS = 730
LASTING_TOPICS = ("姓名", "名字", "学校", "毕业", "生日", "籍贯", "家人", "孩子", "配偶", "父母")
LASTING_WORDS = re.compile(r"^(曾|以前|过去|毕业于)|曾经|曾在|曾就读")


# What the user writes in "改成…" that is not a new understanding but says it is over, or wrong (#157, 10-19:
# "那是非常久之前的了" became a "sure" interest of its own)
WAS_SO = re.compile(r"(很久|好久|非常久|老早|早就|以前|之前|过去|当年|那时|曾经|已经不|现在不|不再|没在|不关注了|不用了|不是了)")
NOT_SO = re.compile(r"^\s*(不是|不对|没有|错了|并不|不感兴趣|no\b|nope)", re.I)


def answer_says(text: str) -> str | None:
    """What a free answer to "是这样吗？" says, if it is not a new wording: "past" (it was so, not now), "no"."""
    t = (text or "").strip()
    if len(t) <= 30 and WAS_SO.search(t):
        return "past"
    if len(t) <= 12 and NOT_SO.search(t):
        return "no"
    return None


def _lasting(kind: str, topic: str | None, text: str) -> bool:
    return kind == "fact" and (any((topic or "").startswith(t) for t in LASTING_TOPICS) or bool(LASTING_WORDS.search(text)))


CATCH_ALL = ("其他", "other")                      # topics that hold many values: nothing supersedes there
SIMILAR_KINDS = ("interest", "dislike", "taste")   # topics named in other words are one
SIMILAR = 0.5                                     # shared two-character pieces, of the shorter name's


def _similar(a: str, b: str) -> bool:
    """Two topic names saying much the same (电商促销与物流广告 / 电商促销与物流通知): most two-character pieces of the
    shorter one are in the other. Not for habits or facts: 某商城 / 某书店 differ in one word that matters."""
    pieces = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}  # noqa: E731
    x, y = pieces("".join(a.lower().split())), pieces("".join(b.lower().split()))
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= SIMILAR


INST_HEADS = ("机构", "账户", "服务", "会员", "学校", "住房", "出行", "其他")   # what luoke-mail files institutions under


def _inst(topic) -> str | None:
    """The institution a fact is about, from its topic ("机构:Apple"; before luoke-mail 0.17.2 "账户:Apple",
    "其他:Amazon.co.jp"): luoke-mail once put the model's category before it, which split one institution across
    topics. None for a topic of one value about the user (职位, 城市), a catch-all with no institution,
    or a person ("同事:某人", "客户:某人") or a matter ("工作:某事") from the user's own mail: several things are
    true of one colleague."""
    if not topic or not isinstance(topic, str):
        return None
    head, sep, tail = topic.replace("：", ":").partition(":")
    name = tail.strip().lower() if sep and head.strip() in INST_HEADS else ""
    return name or None


def _same_fact(topic_a, text_a: str, topic_b, text_b: str) -> bool:
    """One fact, though written twice (luoke-mem#2): the same institution (one note per institution); the
    same value on the same topic, with or without its label; or, where one side is a catch-all topic (其他) or has
    none, values one of which holds the other."""
    ia, ib = _inst(topic_a), _inst(topic_b)
    if ia and ia == ib:
        return True
    va, vb = _value(text_a), _value(text_b)
    if topic_a and topic_a == topic_b and va == vb:
        return True
    loose = lambda t: not t or str(t).split(":")[0] in CATCH_ALL   # noqa: E731
    return (loose(topic_a) or loose(topic_b)) and min(len(va), len(vb)) >= 3 and (va in vb or vb in va)


def _split_off(row: dict) -> bool:
    """A note the user split off from the one it had been folded into: it stands on its own from then on."""
    return '"split": true' in (row.get("data_json") or "")


def _value(text: str) -> str:
    """What a fact says after its label (职位：产品经理 -> 产品经理)."""
    return "".join(text.split("：", 1)[-1].lower().split())


SIDE_FILES = ("", "-wal", "-shm", "-journal")


def _no_links(path: str) -> None:
    """The memory file and what SQLite keeps beside it are never symbolic links: SQLite and chmod follow them, so
    one made in the input method's sandbox would have this library write, or change the mode of, whatever it
    points at (~/.zshenv, say). Checked before opening; the input method's sandbox also forbids making them."""
    for side in SIDE_FILES:
        if os.path.islink(path + side):
            raise RuntimeError(f"记忆文件旁边是一个符号链接，不打开：{path + side}")


REASK_DAYS = 60      # a matter the user answered or waved off is not raised again before this
LATER_DAYS = 7       # one they put off (以后再说), or left unanswered past its time, is asked again after this
REGRADE_BACKUP = ".before-0.18"     # the copy made before the table first grades what is there
BACKUP_DAYS = 14                    # and how long it is kept


def _days(a: str, b: str) -> float:
    """Days from a to b, "YYYY-MM-DD HH:MM:SS" or shorter (tools write happened_at as YYYY-MM, a day, or a minute)."""
    full = "2000-01-01 00:00:00"
    t = lambda s: time.mktime(time.strptime((s[:19] + full[len(s[:19]):]).replace("T", " "), "%Y-%m-%d %H:%M:%S"))
    return (t(b) - t(a)) / 86400


def _drop_backup(path: str) -> None:
    """The copy made before the table first graded the memory: deleted when its time is up or anything is forgotten.
    A tool in a sandbox may not delete it; luoke-mem does, next time."""
    for side in SIDE_FILES:
        p = path + REGRADE_BACKUP + side
        try:
            if os.path.lexists(p) and not os.path.islink(p):
                os.remove(p)
        except OSError:
            pass


def _masked(value):
    """Credentials masked wherever they are in what is written (ADR 0013): strings, and strings in lists and dicts."""
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, list):
        return [_masked(v) for v in value]
    if isinstance(value, dict):
        return {k: _masked(v) for k, v in value.items()}
    return value


def _needs_word(kinds) -> bool:
    """Drawn about health, legal disputes, religion or politics: only the user's word makes it count."""
    return bool(set(kinds or ()) & set(CONFIRM_KINDS))


def _with_confidence(n: dict) -> dict:
    """A note as handed out: its degree, and its structured fields (`data`; a habit's {account, sender, action}).
    `auto` says a tool may carry the habit out by itself: the user confirmed it and it can be undone."""
    n["confidence"] = confidence(n)
    try:
        data = json.loads(n.pop("data_json", None) or "null")
    except ValueError:
        data = None
    n["data"] = data if isinstance(data, dict) else None
    d = n["data"] or {}
    n["auto"] = bool(n["kind"] == "preference" and n["confidence"] == "sure" and d.get("action") in AUTO_ACTIONS
                     and d.get("sender") and d.get("category")       # one kind of a sender's mail, never all of it
                     and (d["action"] != "label" or d.get("label")))
    return n


def _day(at: str) -> str:
    """The day of a record's happened_at, which tools write as YYYY-MM, YYYY-MM-DD or with the time."""
    d = (at or "")[:10]
    return d + "-01" if len(d) == 7 else d


def _evidence(topic: str, text: str, data: dict) -> bool:
    """Whether a record of the user's can bear an understanding out at all -- the one place that says so: not what a
    rule did (it would only prove itself, ADR 0016), not opening business mail (bills, banks, notices: that is
    attending to business, not reading)."""
    if data.get("by", "user") != "user":
        return False
    if topic in ("mail-read", "mail-cleared", "own-label"):
        return not (BUSINESS.search(text or "") or str(data.get("category") or "").lower() in BUSINESS_CATEGORIES)
    return True


def said_no(data: dict, text: str | None) -> bool:
    """A digest-feedback record that is the user saying 没兴趣 (luoke-mail: value "dismissed", "对「…」：没兴趣"), not
    收藏 or taking a word back: only this bears out a dislike, and it bears out nothing else."""
    return data.get("value") == "dismissed" or ("没兴趣" in (text or "") and not (text or "").startswith("撤回"))


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _span(days: set) -> int:
    if len(days) < 2:
        return 0
    t = lambda s: time.mktime(time.strptime(s, "%Y-%m-%d"))  # noqa: E731
    return int(round((t(max(days)) - t(min(days))) / 86400))


def _observe_others(db) -> int:
    """Facts drawn from what others said are not asked about (ADR 0011 amendment): one waiting for
    the user goes to 观察中 instead. What an older library still proposes is moved again whenever luoke-mem opens
    the memory (WholeMemory). Returns how many moved."""
    return db.execute("UPDATE notes SET status='observed' WHERE status='proposed' AND kind='fact' AND origin='inferred'"
                      " AND EXISTS (SELECT 1 FROM cites c JOIN records r ON r.id=c.record_id WHERE c.note_id=notes.id"
                      " AND r.speaker='third_party')").rowcount


def _private(path: str, mode: int) -> None:
    """Only the user may read it. Changed only when it is not so already: in the input method's sandbox the
    folder cannot be changed (the memory still opens; the next tool outside a sandbox puts it right)."""
    try:
        if os.path.islink(path):                 # never the mode of what a link points at
            return
        if stat.S_IMODE(os.lstat(path).st_mode) != mode:
            os.chmod(path, mode)
    except (FileNotFoundError, PermissionError):
        pass


def _kinds(sensitive) -> set:
    kinds = set(sensitive or ())
    unknown = kinds - set(SENSITIVE_KINDS)
    if unknown:
        raise ValueError(f"unknown kind of sensitive context: {', '.join(sorted(unknown))}")
    return kinds


def _join(kinds: set) -> str | None:
    return ",".join(sorted(kinds)) or None


def _split(value: str | None) -> set:
    return set(value.split(",")) if value else set()


def _matches(data_json: str | None, match: dict) -> bool:
    try:
        data = json.loads(data_json) if data_json else None
    except ValueError:
        return False
    return isinstance(data, dict) and all(data.get(k) == v for k, v in match.items())


class Memory:
    """What a tool sees (top-level ADR 0002, 0004): every tool's understanding, but only the records it wrote
    itself; an understanding's evidence from other tools shows only where it came from and how much. Sensitive
    context of a kind the user has not allowed this tool is not there at all."""
    _whole = False

    def __init__(self, path: str | None = None, tool: str = "luoke"):
        self.path = path or default_path()
        _not_the_real_one(self.path)                  # before anything is created or opened
        _no_links(self.path)                        # nor through a symbolic link put beside it
        self.tool = tool
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, 0o700, exist_ok=True)
        if folder == os.path.abspath(luoke_home()):
            _private(folder, 0o700)                 # what a person is like is private
        if not os.path.exists(self.path):           # private from the start: SQLite gives its -wal and -shm
            os.close(os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))   # the same mode as the file
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")  # several luoke tools may read and write at once
        self.db.execute("PRAGMA secure_delete=ON")  # what is forgotten is overwritten, not left in free pages
        self.db.execute("PRAGMA journal_size_limit=0")  # and the log is cut back at every checkpoint, SQLite's too
        try:
            self._upgrade()
        except BaseException:
            self.db.close()
            raise
        for p in (self.path, self.path + "-wal", self.path + "-shm"):
            _private(p, 0o600)

    def _version(self) -> int:
        return self.db.execute("PRAGMA user_version").fetchone()[0]

    def _upgrade(self) -> None:
        """Bring an older file up to this schema. A newer one is used as it is: it only grew. One from a
        thousand this library does not know is refused."""
        if self._version() // 1000 > PROTECTED:
            raise TooNew(f"the memory at {self.path} is newer than this luoke-mem can use "
                               f"(schema {self._version()}): update the tool")
        if self._version() % 1000 >= SCHEMA_VERSION:
            return
        self.db.execute("BEGIN IMMEDIATE")          # another tool may be upgrading the same file right now
        try:
            v = self._version()
            if v % 1000 < SCHEMA_VERSION:
                for stmt in filter(str.strip, SCHEMA.split(";")):     # not executescript: it would commit
                    self.db.execute(stmt)
                for table, col in COLUMNS:          # 0.1 had no topic, 0.7 nothing sensitive
                    if col not in [r[1] for r in self.db.execute(f"PRAGMA table_info({table})")]:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
                if v % 1000 < 5:
                    _observe_others(self.db)
                self.db.execute(f"PRAGMA user_version = {v // 1000 * 1000 + SCHEMA_VERSION}")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _protect(self) -> None:
        """Before 0.23 the first sensitive context written moved the file to the next thousand, so libraries that
        could not keep it from tools would refuse it. Nothing is kept from tools now (ADR 0013): files already
        moved stay so (they open as before), new ones are not moved."""

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):                     # a memory nobody holds any more lets go of its database
        try:
            self.db.close()
        except Exception:  # noqa: BLE001 - interpreter shutdown, or never opened
            pass

    # ---- records: written at once, never changed --------------------------------------------------------
    def record(self, speaker: str, kind: str, text: str, *, topic: str | None = None, data: dict | None = None,
               ref: str | None = None, happened_at: str | None = None, sensitive: list[str] | None = None,
               now: str | None = None) -> int:
        """What happened, at once. `sensitive` names the kinds of sensitive context in it (sensitive_kinds());
        secrets and credentials are not written at all (never_kept(), the tool's check)."""
        return self._insert(self.tool, speaker, kind, text, topic=topic, data=data, ref=ref, happened_at=happened_at,
                            sensitive=sensitive, now=now)

    def _insert(self, tool: str, speaker: str, kind: str, text: str, *, topic: str | None = None, data: dict | None = None,
                ref: str | None = None, happened_at: str | None = None, sensitive=None, now: str | None = None) -> int:
        if speaker not in SPEAKERS or kind not in RECORD_KINDS:
            raise ValueError(f"unknown speaker or kind: {speaker}, {kind}")
        kinds = _kinds(sensitive)
        at = _now(now)
        text, data = mask_secrets(text), _masked(data)     # the library masks, whatever the tool did (ADR 0013)
        with self._lock, self.db:
            cur = self.db.execute(
                "INSERT INTO records (tool, speaker, kind, topic, text, data_json, ref, happened_at, recorded_at, sensitive)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tool, speaker, kind, topic, text, json.dumps(data, ensure_ascii=False) if data is not None else None,
                 ref, happened_at or at, at, _join(kinds)))
            if kinds:
                self._protect()
            return cur.lastrowid

    def redate(self, rid: int, happened_at: str) -> bool:
        """A record of this tool's said the wrong time (own-label before luoke-mail 0.14.1 wrote when it read the
        label, not when it was in use): the tool puts it right. Only the time, only its own records; returns
        whether there was such a record."""
        if not isinstance(happened_at, str) or not happened_at[:4].isdigit():
            raise ValueError("happened_at is YYYY-MM, YYYY-MM-DD or with the time")
        with self._lock, self.db:
            return self.db.execute("UPDATE records SET happened_at=? WHERE id=? AND tool=?",
                                   (happened_at, rid, self.tool)).rowcount == 1

    def _said(self, kind: str, text: str, *, topic: str, ref: str | None = None, sensitive=None, now: str | None = None) -> int:
        """The user's words about an understanding (confirming, rejecting, rewriting, putting off, answering, saying it
        themselves): the memory's, whichever tool they were said in -- named as `via` -- so forgetting that tool keeps
        them (top-level ADR 0009). What a tool records of its own matter stays the tool's (`record`)."""
        via = None if self.tool == KEEPER else {"via": self.tool}
        return self._insert(KEEPER, "user", kind, text, topic=topic, data=via, ref=ref, sensitive=sensitive, now=now)

    # ---- notes: what is understood, with the records it rests on --------------------------------------
    def _cited_kinds(self, cites: list[int]) -> set:
        ids = sorted(set(cites))
        if not ids:
            return set()
        with self._lock:
            rows = self.db.execute(f"SELECT sensitive FROM records WHERE id IN ({','.join('?' * len(ids))})", ids)
            return set().union(*(_split(r[0]) for r in rows))

    def _note(self, kind: str, text: str, origin: str, status: str, cites: list[int], *, why: str | None = None,
              valid_from: str | None = None, valid_to: str | None = None, decided: bool = False, topic: str | None = None,
              sensitive: list[str] | None = None, now: str | None = None, tool: str | None = None,
              data: dict | None = None) -> int:
        """A note is as sensitive as anything it rests on: a tool cannot launder sensitive context by forgetting
        to say so."""
        if kind not in NOTE_KINDS:
            raise ValueError(f"unknown note kind: {kind}")
        if data is not None and not isinstance(data, dict):
            raise ValueError("a note's data is a dict")
        kinds = _kinds(sensitive) | self._cited_kinds(cites)
        at = _now(now)
        text, data = mask_secrets(text), _masked(data)
        with self._lock, self.db:
            cur = self.db.execute(
                "INSERT INTO notes (tool, kind, text, origin, status, valid_from, valid_to, formed_at, decided_at, why, topic,"
                " sensitive, data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tool or self.tool, kind, text.strip(), origin, status, valid_from, valid_to, at, at if decided else None, why,
                 topic, _join(kinds), json.dumps(data, ensure_ascii=False) if data else None))
            nid = cur.lastrowid
            self.db.executemany("INSERT OR IGNORE INTO cites (note_id, record_id) VALUES (?,?)", [(nid, r) for r in cites])
            if kinds:
                self._protect()
            return nid

    def _mark(self, nid: int, kinds: set) -> None:
        """More sensitive context under a note: it takes those kinds too."""
        if not kinds:
            return
        with self._lock, self.db:
            row = self.db.execute("SELECT sensitive FROM notes WHERE id=?", (nid,)).fetchone()
            if row and not kinds <= _split(row[0]):
                self.db.execute("UPDATE notes SET sensitive=? WHERE id=?", (_join(_split(row[0]) | kinds), nid))
                self._protect()

    def state(self, kind: str, text: str, *, record_text: str | None = None, ref: str | None = None,
              valid_from: str | None = None, topic: str | None = None, sensitive: list[str] | None = None,
              now: str | None = None) -> int:
        """The user's own words: a statement, and an active note that cites it. `topic` says what it is about
        (for example "site": the user's own website), so a tool can find it again. Sensitive context the user
        says themselves takes effect too (ADR 0004). Both are the memory's, said through this tool (ADR 0009)."""
        rid = self._said("statement", record_text or f"{LABELS.get(kind, kind)}：{text.strip()}",
                         topic=f"note:{kind}", ref=ref, sensitive=sensitive, now=now)
        return self._note(kind, text, "user", "active", [rid], valid_from=valid_from, decided=True, topic=topic,
                          sensitive=sensitive, now=now, tool=KEEPER)

    def _speakers(self, cites: list[int]) -> set:
        """Who said the cited records; every one of them must be in the memory."""
        ids = sorted(set(cites))
        with self._lock:
            rows = self.db.execute(f"SELECT speaker FROM records WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
        if len(rows) != len(ids):
            raise ValueError("an understanding can only cite records that are in the memory")
        return {r[0] for r in rows}

    def _same_note(self, kind: str, text: str, topic: str | None = None) -> dict | None:
        """The note already saying this, whether or not this tool may read it (never handed out, only its id): the
        same words; for a topic, much the same words (SIMILAR_KINDS); for a fact on the same topic, the same value
        with or without its label (Frank Lou / 姓名：Frank Lou)."""
        with self._lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT id, text, status, topic, decided_at, data_json FROM notes WHERE kind=? AND status IN"
                " ('proposed', 'observed', 'active', 'rejected', 'past') ORDER BY formed_at DESC, id DESC", (kind,))]
        return (next((r for r in rows if _same(r["text"], text)), None)
                or (next((r for r in rows if _similar(r["text"], text)), None) if kind in SIMILAR_KINDS else None)
                or (next((r for r in rows if not _split_off(r) and _same_fact(r["topic"], r["text"], topic, text)
                          # one thing the user said no to about an institution is not everything about it
                          and (r["status"] != "rejected" or _value(r["text"]) == _value(text))), None)
                    if kind == "fact" else None))

    def _reword(self, n: dict, text: str) -> None:
        """The same fact in other words. Not taken a stand on: the fuller wording. Confirmed: its words stay, and
        the new ones wait beside it (`data.newer`) until the memory asks whether it is still so."""
        if _same(n["text"], text):
            return
        if n.get("decided_at"):
            va, vb = _value(n["text"]), _value(text)
            if not (va in vb or vb in va):
                with self._lock:
                    row = self.db.execute("SELECT data_json FROM notes WHERE id=?", (n["id"],)).fetchone()
                try:
                    data = json.loads(row[0]) if row and row[0] else {}
                except ValueError:
                    data = {}
                data = data if isinstance(data, dict) else {}
                data["newer"] = mask_secrets(text.strip())
                with self._lock, self.db:
                    self.db.execute("UPDATE notes SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), n["id"]))
            return
        va, vb = _value(n["text"]), _value(text)
        if (va in vb or vb in va) and len(text.strip()) > len(n["text"]):   # the same, said more fully; not
            with self._lock, self.db:                                       # another thing about the institution
                self.db.execute("UPDATE notes SET text=? WHERE id=? AND origin='inferred' AND decided_at IS NULL",
                                (text.strip(), n["id"]))

    def _rewhy(self, nid: int, why: str | None) -> None:
        """A guess still waiting says what it still waits for, as of now."""
        if why:
            with self._lock, self.db:
                self.db.execute("UPDATE notes SET why=? WHERE id=? AND status='proposed'", (why, nid))

    def propose(self, kind: str, text: str, cites: list[int], *, why: str | None = None, topic: str | None = None,
                sensitive: list[str] | None = None, about: str | None = None, data: dict | None = None,
                now: str | None = None) -> int | None:
        """A guess drawn from records: an open question for the user until confirmed. Nothing is proposed twice,
        and nothing the user already rejected comes back (returns None then). More evidence for a pending guess
        is added to it. A fact resting on what others said is never asked about (ADR 0011 amendment): plain and
        well seen it takes effect (below), otherwise it waits in 观察中 (status 'observed') for the user to look at
        if they wish -- in no card, not in effect.
        A fact drawn from what others said takes effect when the cited records' data say the sender wrote often,
        over months, the user opened some, and lately (EVIDENCE) -- whatever it is about, money included; unless
        it is about health, legal disputes, religion or politics (only the user's word). It is
        never asked about. `about` is kept in the note's `data`, no longer weighed. `data` holds a note's structured fields (a habit's
        {account, sender, action}, 0.18)."""
        if not cites:
            raise ValueError("an inferred note must cite the records it rests on")
        self._speakers(cites)
        n = self._same_note(kind, text, topic)
        if n and n["status"] == "rejected":
            return None
        if n and n["status"] == "past":
            n = self._unpast(n)
        if n and kind == "fact":
            self._reword(n, text)
        if about:
            data = dict(data or {}, about=about)
        plain = (kind == "fact" and not _needs_word(_kinds(sensitive) | self._cited_kinds(cites) | set(sensitive_kinds(text)))
                 and self._well_seen(cites, now))      # one rule for what others say: the evidence
        if n:
            self.add_cites(n["id"], cites)
            self._mark(n["id"], _kinds(sensitive))
            self._set_data(n["id"], data)
            self._rewhy(n["id"], why)
            if plain:
                self._take_effect(n["id"], why, now)
            return n["id"]
        return self._note(kind, text, "inferred", "active" if plain else "proposed", cites,
                          why=why, topic=topic, sensitive=sensitive, now=now, data=data)

    def _unpast(self, n: dict) -> dict:
        """Seen again. What went into the past only for being old comes back as it was: sure if the user had
        confirmed it (decided_at kept), otherwise a guess to be graded with its new evidence. What the user themselves
        said was over (以前是，现在不是) becomes a guess: new evidence asks again, it does not overrule them; their
        words stay among its evidence, and so does when they said it (decided_at)."""
        with self._lock:
            latest = self.db.execute(              # the user's latest word on it decides, not any one of them
                "SELECT r.text FROM cites c JOIN records r ON r.id=c.record_id WHERE c.note_id=? AND r.topic='note-decision'"
                " ORDER BY r.recorded_at DESC, r.id DESC LIMIT 1", (n["id"],)).fetchone()
            said_over = bool(latest and latest[0].startswith("以前是，现在不是："))
            row = self.db.execute("SELECT decided_at FROM notes WHERE id=?", (n["id"],)).fetchone()
        status = "active" if row and row[0] and not said_over else "proposed"
        with self._lock, self.db:
            self.db.execute("UPDATE notes SET status=? WHERE id=? AND status='past'", (status, n["id"]))
        return dict(n, status=status, decided_at=row[0] if row else None)

    def _set_data(self, nid: int, data: dict | None) -> None:
        if data:
            with self._lock, self.db:
                self.db.execute("UPDATE notes SET data_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), nid))

    def _likely(self, kind: str, cites: list[int], text: str = "") -> bool:
        """Whether what the user's records say bears this understanding out (GRADES): one topic among the cited
        records that can tell this kind, seen often enough, on enough days, far enough apart. Opening business mail
        (BUSINESS) is not reading, and an interest named after a sender (_senderish) is not a topic."""
        rules = GRADES.get(kind, {})
        if rules is None:
            return True
        if kind == "interest" and _senderish(text):
            return False
        ids = sorted(set(cites))
        if not ids:
            return False
        with self._lock:
            rows = self.db.execute(f"SELECT topic, happened_at, data_json, text FROM records WHERE speaker='user' AND id IN"
                                   f" ({','.join('?' * len(ids))})", ids).fetchall()
        by: dict = {}
        for topic, happened, data_json, words in rows:
            try:
                data = json.loads(data_json) if data_json else {}
            except ValueError:
                data = {}
            data = data if isinstance(data, dict) else {}
            if topic == "digest-feedback" and said_no(data, words) != (kind == "dislike"):
                continue                         # 没兴趣 is a dislike's evidence, and only a dislike's
            if topic in rules and _evidence(topic, words, data):
                by.setdefault(topic, []).append((happened, data))
        for topic, rs in by.items():
            times, days, span = self._bar(kind, topic, rules[topic])
            ds = {_day(h) for h, _ in rs}
            also = ALSO.get(topic, {})
            if (len(rs) >= times and len(ds) >= days and _span(ds) >= span
                    and sum(_int(d.get("opened")) for _, d in rs) >= also.get("opened", 0)
                    and len({str(d.get("sender") or "") for _, d in rs} - {""}) >= also.get("senders", 0)):
                return True
        return False

    def _bar(self, kind: str, topic: str, rule: tuple) -> tuple:
        """The bar for this user ("按反馈逼近"): what they said of understanding of this kind drawn
        from these records. Mostly wrong (two or more, more than right): higher, so more is asked; always right
        (three or more, none wrong): lower."""
        with self._lock:
            row = self.db.execute(
                "SELECT SUM(n.status='rejected'), SUM(n.status<>'rejected') FROM notes n WHERE n.kind=? AND n.origin='inferred'"
                " AND n.decided_at IS NOT NULL AND EXISTS (SELECT 1 FROM cites c JOIN records r ON r.id=c.record_id"
                " WHERE c.note_id=n.id AND r.topic=?)", (kind, topic)).fetchone()
        no, yes = int(row[0] or 0), int(row[1] or 0)
        times, days, span = rule
        if no >= 2 and no > yes:
            return times + 1, days + 1, max(span * 2, 7)
        if yes >= 3 and not no:
            days = max(1, days - 1)
            return max(1, times - 1), days, span // 2 if days > 1 else 0
        return rule

    def _well_seen(self, cites: list[int], now: str | None) -> bool:
        """Enough evidence for a plain fact (ADR 0011): some cited record says the sender wrote often, over months,
        the user opened some, and lately."""
        at = _now(now)
        y, m = int(at[:4]), int(at[5:7]) - EVIDENCE["within_months"]
        while m < 1:
            y, m = y - 1, m + 12
        since = f"{y:04d}-{m:02d}"
        with self._lock:
            rows = self.db.execute(f"SELECT data_json FROM records WHERE id IN ({','.join('?' * len(cites))})", cites).fetchall()
        for (data_json,) in rows:
            try:
                d = json.loads(data_json) if data_json else {}
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            try:
                if (int(d.get("count") or 0) >= EVIDENCE["count"] and int(d.get("months") or 0) >= EVIDENCE["months"]
                        and int(d.get("opened") or 0) >= EVIDENCE["opened"] and str(d.get("last") or "") >= since):
                    return True
            except (TypeError, ValueError):
                continue
        return False

    def _take_effect(self, nid: int, why: str | None, now: str | None) -> None:
        """A waiting guess takes effect without the user's word: it counts as taken down now (formed_at), so it is
        among what was recently taken down; decided_at stays empty -- the user did not decide."""
        with self._lock:
            row = self.db.execute("SELECT sensitive FROM notes WHERE id=?", (nid,)).fetchone()
        if not row or _needs_word(_split(row[0])):
            return
        with self._lock, self.db:
            self.db.execute("UPDATE notes SET status='active', why=COALESCE(?, why), formed_at=? WHERE id=? AND"
                            " status IN ('proposed', 'observed') AND origin='inferred' AND decided_at IS NULL",
                            (why, _now(now), nid))         # not over the user's word (以前是，现在不是)

    def conclude(self, kind: str, text: str, cites: list[int], *, topic: str | None = None, why: str | None = None,
                 sensitive: list[str] | None = None, data: dict | None = None, now: str | None = None) -> int | None:
        """An understanding drawn from what the user said or did themselves (never third parties). It takes effect
        -- likely -- when what it rests on bears it out by the table of what records can tell (GRADES, ADR 0012
        amendment: which records can tell this kind, how often, how far apart); otherwise it is a guess, whatever
        the tool meant. The user can still reject or change it. On the same topic a new understanding in effect
        supersedes the old one (a new job title replaces the old). Nothing the user rejected comes back; the same
        understanding again gains evidence, and a guess takes effect once its evidence bears it out. Returns the
        note, or None if rejected. Sensitive context (marked here or in what it rests on) only counts when the
        user said or confirmed it (ADR 0004), so it waits for them like a guess."""
        if not cites:
            raise ValueError("an inferred note must cite the records it rests on")
        if self._speakers(cites) != {"user"}:
            raise ValueError("only what the user said can take effect without their confirmation")
        if _needs_word(_kinds(sensitive) | self._cited_kinds(cites) | set(sensitive_kinds(text))):
            return self.propose(kind, text, cites, why=why, topic=topic, sensitive=sensitive, data=data, now=now)
        n = self._same_note(kind, text, topic)
        if n:
            if n["status"] == "rejected":
                return None
            if n["status"] == "past":
                n = self._unpast(n)
            if kind == "fact":
                self._reword(n, text)
            self.add_cites(n["id"], cites)
            self._set_data(n["id"], data)
            if n["status"] in ("proposed", "observed") and self._likely(kind, self._cites_of(n["id"]), text):
                self._take_effect(n["id"], why, now)
                self._supersede(n["id"])
            return n["id"]
        likely = self._likely(kind, cites, text)
        new = self._note(kind, text, "inferred", "active" if likely else "proposed", cites, why=why, topic=topic,
                         now=now, data=data)
        if likely:
            self._supersede(new)
        return new

    def _fresh(self, nid: int) -> str:
        """The day of the newest evidence a note rests on (YYYY-MM-DD; '' with none): when it was last seen so."""
        with self._lock:
            rows = self.db.execute("SELECT r.happened_at, r.data_json FROM cites c JOIN records r ON r.id=c.record_id"
                                   " WHERE c.note_id=?", (nid,)).fetchall()
        days = []
        for happened, data_json in rows:
            days.append(_day(happened))
            try:
                last = (json.loads(data_json) or {}).get("last") if data_json else None
            except (ValueError, AttributeError):
                last = None
            if isinstance(last, str) and last[:4].isdigit():
                days.append(_day(last))
        return max(days) if days else ""

    def _past_days(self, kind: str) -> int:
        """How long without new evidence before what changes is 以前的事: PAST_DAYS, twice that for a kind whose
        past notes the user said twice or more are still so (they came back by the user's word)."""
        with self._lock:
            row = self.db.execute("SELECT value FROM learning WHERE name=?", (f"past.revived.{kind}",)).fetchone()
        return PAST_DAYS * (2 if row and int(row[0] or 0) >= 2 else 1)

    def _cites_of(self, nid: int) -> list[int]:
        with self._lock:
            return [r[0] for r in self.db.execute("SELECT record_id FROM cites WHERE note_id=?", (nid,))]

    def _supersede(self, nid: int) -> None:
        """A note in effect replaces what was drawn before on its topic -- not what the user confirmed (theirs to
        change), and not on a catch-all topic (其他: many facts, not one value)."""
        with self._lock, self.db:
            row = self.db.execute("SELECT kind, topic, status FROM notes WHERE id=?", (nid,)).fetchone()
            if row and row["topic"] and row["topic"] not in CATCH_ALL and row["status"] == "active":
                self.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE kind=? AND topic=? AND id<>?"
                                " AND origin='inferred' AND decided_at IS NULL AND status IN ('active', 'proposed', 'observed')",
                                (nid, row["kind"], row["topic"], nid))

    def add_cites(self, nid: int, record_ids: list[int]) -> None:
        if record_ids:
            self._speakers(record_ids)
        with self._lock, self.db:
            self.db.executemany("INSERT OR IGNORE INTO cites (note_id, record_id) VALUES (?,?)", [(nid, r) for r in record_ids])
        self._mark(nid, self._cited_kinds(record_ids))

    def _decide(self, nid: int, status: str, verb: str, now: str | None) -> dict:
        n = self.note(nid)
        if not n or n["status"] not in ("proposed", "observed", "active", "past"):
            raise ValueError("this note can no longer be changed")
        if n["status"] == "past" and status == "active":      # still so, the user says: this kind waits longer
            with self._lock, self.db:
                self.db.execute("INSERT INTO learning (name, value) VALUES (?, '1') ON CONFLICT(name) DO UPDATE SET"
                                " value=CAST(value AS INTEGER)+1", (f"past.revived.{n['kind']}",))
        rid = self._said("statement", f"{verb}：{n['text']}", topic="note-decision", ref=str(nid),
                         sensitive=_split(n["sensitive"]), now=now)
        with self._lock, self.db:
            self.db.execute("UPDATE notes SET status=?, decided_at=? WHERE id=?", (status, _now(now), nid))
            self.db.execute("INSERT OR IGNORE INTO cites (note_id, record_id) VALUES (?,?)", (nid, rid))
        self._settle_asks(nid, rid, now)
        return n

    def _settle_asks(self, nid: int, rid: int | None, now: str | None, status: str = "answered") -> None:
        """The user said about a note, here or on the page: what the memory meant to ask about it is answered."""
        with self._lock, self.db:
            self.db.execute("UPDATE asks SET status=?, decided_at=?, answer_record=COALESCE(?, answer_record) WHERE"
                            " about_note=? AND status IN ('open', 'later')", (status, _now(now), rid, nid))

    def confirm(self, nid: int, *, now: str | None = None) -> None:
        self._decide(nid, "active", "确认", now)

    def was(self, nid: int, *, now: str | None = None) -> None:
        """以前是，现在不是: the user says it was so and is no longer -- kept as 以前的事, not used, not asked."""
        self._decide(nid, "past", "以前是，现在不是", now)

    def reject(self, nid: int, *, now: str | None = None) -> None:
        """Kept, marked rejected, so the same guess is not raised again."""
        self._decide(nid, "rejected", "否定", now)

    def later(self, nid: int, *, now: str | None = None) -> int:
        """The user put it off (以后再说): a receipt, never a decision -- putting off is not saying no (principle 2).
        The note stays as it is; when to ask again is the asker's to decide."""
        n = self.note(nid)
        if not n or n["status"] not in ("proposed", "observed", "active", "past"):
            raise ValueError("this note can no longer be changed")
        rid = self._said("receipt", f"以后再说：{n['text']}", topic="note-later", ref=str(nid),
                         sensitive=_split(n["sensitive"]), now=now)
        with self._lock, self.db:                # what was asked about it waits; rumination asks again later
            self.db.execute("UPDATE asks SET status='later', decided_at=? WHERE about_note=? AND status='open'",
                            (_now(now), nid))
        return rid

    def revise(self, nid: int, text: str, *, kind: str | None = None, sensitive: list[str] | None = None,
               now: str | None = None) -> int:
        """The user rewrites a note: the new one supersedes the old, which stays (principle 3). It is as
        sensitive as the old one, and as what the new words bring (`sensitive`, from sensitive_kinds())."""
        said = answer_says(text)
        if said == "past":                      # "那是很久以前的了": not new words, it is over (10-19)
            self.was(nid, now=now)
            return nid
        if said == "no":
            self.reject(nid, now=now)
            return nid
        old = self.note(nid)
        if not old or old["status"] not in ("proposed", "observed", "active", "past"):
            raise ValueError("this note can no longer be changed")
        kind = kind or old["kind"]
        kinds = _split(old["sensitive"]) | _kinds(sensitive)   # still about the same, and as sensitive
        rid = self._said("statement", f"改为：{text.strip()}", topic="note-decision",     # not the old words: they
                         ref=str(nid), sensitive=kinds, now=now)    # may be forgotten with their tool (ref)
        new = self._note(kind, text, "user", "active", [rid], decided=True, topic=old["topic"], sensitive=kinds, now=now,
                         tool=KEEPER)                 # the user's own words: the memory's (ADR 0009)
        with self._lock, self.db:
            self.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (new, nid))
        self._settle_asks(nid, rid, now)
        return new

    # ---- forgetting: the one place where things are really deleted -------------------------------------
    def forget(self, nid: int) -> None:
        """Delete a note, the versions it superseded, and the records nothing else cites."""
        with self._lock, self.db:
            self._forget(nid)
        self._wipe()

    def _wipe(self) -> None:
        """Forgotten is gone from the file too (ARCHITECTURE §4): deleted pages are overwritten (secure_delete), and
        the write-ahead log, which may still hold earlier versions of them, is emptied -- once no other tool is
        reading; if one is, at the next forgetting or when SQLite checkpoints on its own."""
        try:
            with self._lock:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        except sqlite3.Error:
            pass
        _drop_backup(self.path)                  # nor in the copy made before grading (0.18): forgetting wins


    def _forget(self, nid: int) -> None:
        chain, todo = set(), [nid]
        while todo:
            n = todo.pop()
            if n in chain:
                continue
            chain.add(n)
            todo += [r[0] for r in self.db.execute("SELECT id FROM notes WHERE superseded_by=?", (n,))]
        marks = ",".join("?" * len(chain))
        refs = [str(n) for n in chain]           # receipts about it, and questions about it with their answers
        self.db.execute(f"DELETE FROM records WHERE topic='note-later' AND ref IN ({marks})", refs)
        for aid, answer in self.db.execute(f"SELECT id, answer_record FROM asks WHERE about_note IN ({marks})",
                                           list(chain)).fetchall():
            self.db.execute("DELETE FROM records WHERE (topic IN ('ask-answer', 'ask-dismissed') AND ref=?) OR id=?",
                            (str(aid), answer))
            self.db.execute("DELETE FROM asks WHERE id=?", (aid,))
        recs = [r[0] for r in self.db.execute(f"SELECT DISTINCT record_id FROM cites WHERE note_id IN ({marks})", list(chain))]
        self.db.execute(f"DELETE FROM cites WHERE note_id IN ({marks})", list(chain))
        self.db.execute(f"DELETE FROM notes WHERE id IN ({marks})", list(chain))
        gone = [r for r in recs if not self.db.execute("SELECT 1 FROM cites WHERE record_id=?", (r,)).fetchone()]
        for r in gone:
            self.db.execute("DELETE FROM records WHERE id=?", (r,))
        self._unref(gone)

    def _drop(self, ids: list[int]) -> None:
        """Delete records. An understanding left citing nothing rested only on them, so it goes too."""
        touched: set = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            touched |= {r[0] for r in self.db.execute(f"SELECT DISTINCT note_id FROM cites WHERE record_id IN ({marks})", chunk)}
            self.db.execute(f"DELETE FROM cites WHERE record_id IN ({marks})", chunk)
            self.db.execute(f"DELETE FROM records WHERE id IN ({marks})", chunk)
        for nid in sorted(touched):
            if (self.db.execute("SELECT 1 FROM notes WHERE id=?", (nid,)).fetchone()
                    and not self.db.execute("SELECT 1 FROM cites WHERE note_id=?", (nid,)).fetchone()):
                self._forget(nid)
        self._unref(ids)

    def _unref(self, ids: list[int]) -> None:
        """Records are gone: what still pointed at them lets go. Ids come back (no AUTOINCREMENT), so a pointer
        left behind would one day point at someone else's record."""
        for i in range(0, len(ids), 500):    # an answer forgotten: the question keeps that it was answered
            chunk = ids[i:i + 500]
            self.db.execute(f"UPDATE asks SET answer_record=NULL WHERE answer_record IN ({','.join('?' * len(chunk))})", chunk)
        gone = set(ids)                      # what luoke-mem saw but had not yet learnt rests on records too
        for cid, rids in self.db.execute("SELECT id, record_ids FROM candidates").fetchall():
            left = [r for r in json.loads(rids) if r not in gone]
            if not left:
                self.db.execute("DELETE FROM candidates WHERE id=?", (cid,))
            elif len(left) != len(json.loads(rids)):
                self.db.execute("UPDATE candidates SET record_ids=? WHERE id=?", (json.dumps(left), cid))

    def forget_records(self, *, topics: list[str] | None = None, since: str | None = None, until: str | None = None,
                       match: dict | None = None, keep_cited: bool = False) -> int:
        """Delete this tool's records: of these topics, that happened in [since, until), whose data has these
        values (for example {"app": "com.tencent.xinWeChat"}). Understanding resting only on them goes too.
        With keep_cited the records an understanding cites stay as its evidence: what a retention period the
        user set uses, so raw records age out and what was learnt from them stays traceable. Returns how many
        records were deleted. Only this tool's own records."""
        q, args = "SELECT id, data_json FROM records WHERE tool=?", [self.tool]
        if topics:
            q += f" AND topic IN ({','.join('?' * len(topics))})"
            args += topics
        if since:
            q += " AND happened_at>=?"
            args.append(since)
        if until:
            q += " AND happened_at<?"
            args.append(until)
        if keep_cited:
            q += " AND id NOT IN (SELECT record_id FROM cites)"
        with self._lock, self.db:
            ids = [r[0] for r in self.db.execute(q, args) if not match or _matches(r[1], match)]
            self._drop(ids)
        if ids:
            self._wipe()
        return len(ids)

    def _sizes(self) -> tuple:
        return tuple(self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("records", "notes"))

    def forget_tool(self, tool: str | None = None, *, dry_run: bool = False) -> dict:
        """Forget everything a tool wrote: its records, its understanding with what the user said about it (by
        whichever tool they used: decisions, 以后再说, answers to the memory's questions about it), other tools'
        understanding that rested only on its records, and what the user allowed it to read. What the user said
        through it is the memory's and stays (top-level ADR 0009), and so does what the user confirmed, rejected or
        wrote themselves: such notes of the tool move to luoke-mem, keeping the user's words as their evidence
        (ADR 0009 amendment). Only guesses the user never took a stand on go with the tool. Another tool's older version it superseded stays, as history pointing nowhere.
        Returns how many records and notes went; with dry_run nothing is deleted and the same numbers say what
        would go. A tool forgets what it wrote itself; another tool's share is forgotten only from luoke-mem's
        own interface (WholeMemory)."""
        tool = tool or self.tool
        if tool == KEEPER:                       # the user's own words and what the memory drew
            raise ValueError("螺壳记忆自己的不按来源忘掉：在每一条上删，或者清空整份记忆")
        if tool != self.tool and not self._whole:
            raise ValueError("a tool forgets only what it wrote itself; another tool's share is forgotten from "
                             "luoke-mem's own page (top-level ADR 0002)")
        with self._lock:
            try:
                before = self._sizes()
                # what the user said, and what they confirmed, rejected or wrote themselves, is theirs and stays,
                # with the memory (ADR 0009 and its amendment): the user's words an older library
                # recorded under the tool, and the tool's notes the user took a stand on
                for rid, data_json in self.db.execute(
                        "SELECT id, data_json FROM records WHERE tool=? AND speaker='user' AND (topic IN ('note-decision',"
                        " 'note-later', 'ask-answer', 'ask-dismissed') OR topic LIKE 'note:%')", (tool,)).fetchall():
                    data = json.loads(data_json) if data_json else {}
                    data = dict(data, via=tool) if isinstance(data, dict) else {"via": tool}
                    self.db.execute("UPDATE records SET tool=?, data_json=? WHERE id=?",
                                    (KEEPER, json.dumps(data, ensure_ascii=False), rid))
                self.db.execute("UPDATE notes SET tool=? WHERE tool=? AND status IN ('active', 'rejected')"
                                " AND (decided_at IS NOT NULL OR origin='user')", (KEEPER, tool))
                decided = [r[0] for r in self.db.execute(       # 确认 / 否定 made about its notes, by whichever tool
                    "SELECT DISTINCT records.id FROM records JOIN cites ON cites.record_id=records.id"
                    " JOIN notes ON notes.id=cites.note_id WHERE notes.tool=? AND records.topic='note-decision'", (tool,))]
                # what else was said about its notes goes with them, as when one note is forgotten: 以后再说, and the
                # memory's questions about them with their answers -- the user's words are the memory's (ADR 0009),
                # so no longer taken along as the tool's own records, and they repeat the forgotten notes' words
                ours = "SELECT id FROM notes WHERE tool=?"
                decided += [r[0] for r in self.db.execute(
                    f"SELECT id FROM records WHERE topic='note-later' AND ref IN (SELECT CAST(id AS TEXT) FROM notes WHERE tool=?)",
                    (tool,))]
                for aid, answer in self.db.execute(f"SELECT id, answer_record FROM asks WHERE about_note IN ({ours})",
                                                   (tool,)).fetchall():
                    decided += [r[0] for r in self.db.execute(
                        "SELECT id FROM records WHERE (topic IN ('ask-answer', 'ask-dismissed') AND ref=?) OR id=?", (str(aid), answer))]
                    self.db.execute("DELETE FROM asks WHERE id=?", (aid,))
                self.db.execute("DELETE FROM cites WHERE note_id IN (SELECT id FROM notes WHERE tool=?)", (tool,))
                self.db.execute("UPDATE notes SET superseded_by=NULL WHERE tool<>? AND superseded_by IN"   # ids come
                                " (SELECT id FROM notes WHERE tool=?)", (tool, tool))                       # back
                self.db.execute("DELETE FROM notes WHERE tool=?", (tool,))
                decided = [d for d in decided if not self.db.execute("SELECT 1 FROM cites WHERE record_id=?", (d,)).fetchone()]
                self._drop([r[0] for r in self.db.execute("SELECT id FROM records WHERE tool=?", (tool,))] + decided)
                self.db.execute("DELETE FROM grants WHERE tool=?", (tool,))
                after = self._sizes()
            except BaseException:
                self.db.rollback()
                raise
            if dry_run:
                self.db.rollback()
            else:
                self.db.commit()
        if not dry_run:
            self._wipe()
        return {"records": before[0] - after[0], "notes": before[1] - after[1]}

    # ---- sensitive context: read only with the user's leave (top-level ADR 0004) --------------------------
    def allowed(self) -> list[str]:
        """Every kind (ADR 0013; before 0.23 a tool read a kind of sensitive context only with the user's leave)."""
        return sorted(SENSITIVE_KINDS)

    def ask(self, kind: str, why: str | None = None, *, now: str | None = None) -> str:
        """Before 0.23 a tool asked to read a kind of sensitive context; every tool reads it now: "granted"."""
        _kinds([kind])
        return "granted"

    def _visible(self, col: str = "sensitive") -> tuple:
        """Nothing is kept out (ADR 0013: the machine is the boundary; before 0.23 sensitive context needed the
        user's leave for each tool). Kept so the readers stay as they were written."""
        return "", []

    def withheld(self, status: list[str] | None = None) -> int:
        """Nothing is withheld any more (ADR 0013): always 0, for tools written before 0.23."""
        return 0

    # ---- what the memory would like to ask the user (top-level ADR 0006) --------------------------------
    # luoke-mem thinks of questions and suggestions; luoke-agent, the one voice that speaks to the user, asks
    # them and hands the answers back. The memory never goes to the user itself.
    @staticmethod
    def _ask_row(row: sqlite3.Row) -> dict:
        a = dict(row)
        a["answer"] = json.loads(a["answer"])
        a["action"] = json.loads(a["action"]) if a.get("action") else None
        a["rank"] = int(a.get("rank") or 0)
        return a

    def asks(self, status: list[str] | None = None, *, tool: str | None = None, now: str | None = None) -> list[dict]:
        """Questions and suggestions -- the agent's list (ADR 0012) -- most worth it first (rank), then newest; open
        ones past their time are left out. Sensitive ones only for a tool allowed that kind. `tool` keeps those
        that concern it, for its own page while luoke-agent is not there (ADR 0012 amendment 6): about a note it
        wrote or resting on its records, or a suggestion it would carry out (`action`)."""
        status = status or ["open"]
        q = f"SELECT * FROM asks WHERE status IN ({','.join('?' * len(status))})"
        args: list = list(status)
        if "open" in status:
            q += " AND (status<>'open' OR expires_at IS NULL OR expires_at>?)"
            args.append(_now(now))
        if tool:
            q += (" AND (about_note IN (SELECT n.id FROM notes n WHERE n.tool=? OR EXISTS (SELECT 1 FROM cites c JOIN records r"
                  " ON r.id=c.record_id WHERE c.note_id=n.id AND r.tool=?)) OR action LIKE ?)")
            args += [tool, tool, '%"tool": ' + json.dumps(tool) + '%']
        hide, more = self._visible()
        with self._lock:
            return [self._ask_row(r) for r in self.db.execute(
                q + hide + " ORDER BY CAST(COALESCE(rank, '0') AS INTEGER) DESC, created_at DESC, id DESC", args + more)]

    def _open_ask(self, aid: int) -> dict:
        hide, more = self._visible()
        with self._lock:
            row = self.db.execute("SELECT * FROM asks WHERE id=?" + hide, [aid] + more).fetchone()
        if not row or row["status"] != "open":
            raise ValueError("this question is no longer open")
        return self._ask_row(row)

    def answer(self, aid: int, reply: str, *, now: str | None = None) -> int:
        """The user's answer to a question of the memory's own: their own words, kept as a statement under the question
        (topic ask-answer); the question is closed. Nothing is drawn from it by itself yet -- what it says is there for
        the page and for whoever reads the memory."""
        a = self._open_ask(aid)
        reply = " ".join(str(reply).split())
        if not reply:
            raise ValueError("an answer says something")
        rid = self._said("statement", f"问：{a['text']}\n答：{reply}", topic="ask-answer", ref=str(aid),
                         sensitive=_split(a["sensitive"]), now=now)
        with self._lock, self.db:
            self.db.execute("UPDATE asks SET status='answered', decided_at=?, answer_record=? WHERE id=?", (_now(now), rid, aid))
        return rid

    def reply(self, aid: int, choice: str, text: str | None = None, *, now: str | None = None) -> None:
        """What the user said to something on the agent's list, in one call (ADR 0012); the memory settles it.
        A question about a note: "yes" (sure), "no" (wrong, not raised again), "past" (it was so, not now: 以前的事),
        "revise" with `text` (the user's own
        words, sure), "later" (put off; asked again some other time). A question of its own: "answer" with `text`.
        A suggestion: "useful", "useless" (not again for a while), "never" (not again); one about a note (a habit:
        "以后都这样处理？") confirms it when useful."""
        a = self._open_ask(aid)
        nid = a.get("about_note")
        if a["kind"] == "question" and nid and choice in ("yes", "no", "revise", "later", "past"):
            n = self.note(nid)
            if not n or n["status"] not in ("proposed", "observed", "active", "past"):
                self._settle_asks(nid, None, now)        # decided already, or forgotten
                return
            if choice == "yes":
                self.confirm(nid, now=now)
            elif choice == "no":
                self.reject(nid, now=now)
            elif choice == "past":
                self.was(nid, now=now)
            elif choice == "revise":
                if not (text or "").strip() or never_kept(text):
                    raise ValueError("记忆中不保存密码、验证码和证件号码，也不保存空的话")
                self.revise(nid, text, sensitive=sensitive_kinds(text), now=now)
            else:
                self.later(nid, now=now)
        elif a["kind"] == "question" and choice == "answer":
            self.answer(aid, text or "", now=now)
        elif a["kind"] == "question" and choice == "later":   # a question of the memory's own, put off
            with self._lock, self.db:
                self.db.execute("UPDATE asks SET status='later', decided_at=? WHERE id=?", (_now(now), aid))
        elif a["kind"] == "suggestion" and choice == "useful":
            self.answer(aid, "有用", now=now)
            n = self.note(nid) if nid else None
            if n and n["status"] in ("proposed", "observed", "active") and not n["decided_at"]:
                self.confirm(nid, now=now)               # "以后都这样？" -- yes: the habit is the user's now
        elif a["kind"] == "suggestion" and choice in ("useless", "never"):
            self.dismiss(aid, now=now)
            if choice == "never":
                with self._lock, self.db:
                    self.db.execute("UPDATE asks SET status='never' WHERE id=?", (aid,))
        else:
            raise ValueError(f"not an answer to this: {choice}")

    def dismiss(self, aid: int, *, now: str | None = None) -> None:
        """The user does not want to be asked this: a receipt, and the question is closed."""
        a = self._open_ask(aid)
        self._said("receipt", f"不用问了：{a['text']}", topic="ask-dismissed", ref=str(aid),
                   sensitive=_split(a["sensitive"]), now=now)
        with self._lock, self.db:
            self.db.execute("UPDATE asks SET status='dismissed', decided_at=? WHERE id=?", (_now(now), aid))

    # ---- recall: read only (principle 10) ----------------------------------------------------------------
    @staticmethod
    def _record(row: sqlite3.Row) -> dict:
        r = dict(row)
        r["data"] = json.loads(r.pop("data_json")) if r["data_json"] else None
        return r

    def notes(self, kinds: list[str] | None = None, status: list[str] | None = None, tool: str | None = None,
              confidence: list[str] | None = None) -> list[dict]:
        """Understanding, newest first. By default what is in effect -- likely and sure (ADR 0012): what a tool
        uses. `confidence` (guess / likely / sure) asks for those degrees instead of `status`."""
        q, args = ("SELECT *, (SELECT MAX(r.happened_at) FROM cites c JOIN records r ON r.id=c.record_id WHERE"
                   " c.note_id=notes.id) AS fresh FROM notes WHERE 1=1"), []
        if kinds:
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        if confidence:
            q += " AND (" + " OR ".join(f"({CONFIDENCE[c]})" for c in confidence) + ")"
            status = []
        status = status if status is not None else ["active"]
        if status:
            q += f" AND status IN ({','.join('?' * len(status))})"
            args += status
        if tool:
            q += " AND tool=?"
            args.append(tool)
        hide, more = self._visible()
        with self._lock:
            return [_with_confidence(dict(r)) for r in self.db.execute(q + hide + " ORDER BY formed_at DESC, id DESC", args + more)]

    def recent(self, since: str | None = None, limit: int = 50) -> list[dict]:
        """What was taken down without asking, newest first (ADR 0011): understanding that took effect without the
        user's word -- from their own words, or plain facts from others -- since `since` (formed_at), as notes()
        gives it, with `source`: where it comes from, in words for the user ("根据你收到的邮件"). Sensitive
        context this tool may not read is not there. What the user strikes, they reject (`reject`)."""
        q, args = ("SELECT * FROM notes WHERE status='active' AND decided_at IS NULL AND origin='inferred'"
                   " AND kind<>'taste'"), []                # the digest's, not what was taken down about the user
        if since:
            q += " AND formed_at>=?"
            args.append(since)
        hide, more = self._visible()
        with self._lock:
            rows = [dict(r) for r in self.db.execute(q + hide + " ORDER BY formed_at DESC, id DESC LIMIT ?",
                                                     args + more + [int(limit)])]
            for n in rows:
                said = self.db.execute("SELECT DISTINCT r.tool, r.speaker FROM cites c JOIN records r ON r.id=c.record_id"
                                       " WHERE c.note_id=? ORDER BY r.tool, r.speaker", (n["id"],)).fetchall()
                phrases = list(dict.fromkeys(SOURCE_PHRASES.get((t, s)) or f"根据{TOOL_NAMES.get(t, t)}记下的" for t, s in said))
                n["source"] = "、".join(phrases)
                _with_confidence(n)
        return rows

    def note(self, nid: int) -> dict | None:
        """A note with all its evidence, whichever tool wrote it (ADR 0013), newest first: `cites`. `cites_elsewhere`
        is kept, empty, for tools written before 0.23 (they read only their own records then)."""
        hide, more = self._visible()
        with self._lock:
            row = self.db.execute("SELECT * FROM notes WHERE id=?" + hide, [nid] + more).fetchone()
            if not row:
                return None
            n = dict(row)
            hide, more = self._visible("records.sensitive")
            cites = [self._record(r) for r in self.db.execute(
                "SELECT records.* FROM records JOIN cites ON cites.record_id=records.id WHERE cites.note_id=?" + hide +
                " ORDER BY records.happened_at DESC, records.id DESC", [nid] + more)]
            n["cites"] = cites
            n["cites_elsewhere"] = []
            return _with_confidence(n)

    def records(self, *, tool: str | None = "", topic: str | None = None, kind: str | None = None,
                speaker: str | None = None, ref: str | None = None, since: str | None = None,
                limit: int | None = None) -> list[dict]:
        """Records, newest first: this tool's by default, another's with `tool`, every tool's with tool=None
        (ADR 0013: any tool reads them all)."""
        return self._records(self.tool if tool == "" else tool, topic=topic, kind=kind, speaker=speaker, ref=ref,
                             since=since, limit=limit)

    def _records(self, tool: str | None, *, topic: str | None, kind: str | None, speaker: str | None,
                 ref: str | None, since: str | None, limit: int | None, until: str | None = None,
                 offset: int = 0) -> list[dict]:
        q, args = "SELECT * FROM records WHERE 1=1", []
        for col, val in (("tool", tool), ("topic", topic), ("kind", kind), ("speaker", speaker), ("ref", ref)):
            if val is not None:
                q += f" AND {col}=?"
                args.append(val)
        if since:
            q += " AND recorded_at>=?"
            args.append(since)
        if until:
            q += " AND recorded_at<?"
            args.append(until)
        hide, more = self._visible()
        q, args = q + hide, args + more
        q += " ORDER BY recorded_at DESC, id DESC" + (" LIMIT ? OFFSET ?" if limit else "")
        if limit:
            args += [limit, offset]
        with self._lock:
            return [self._record(r) for r in self.db.execute(q, args)]

    def context(self, kinds: list[str], limit: int = 600, labels: dict | None = None) -> str:
        """What is already written down, as a short block for a prompt (principle 12: no summarising here).
        Newest first within each kind; stops before `limit` characters."""
        labels = {**LABELS, **(labels or {})}
        out, used = [], 0
        for kind in kinds:
            lines = [f"- {n['text']}" for n in self.notes(kinds=[kind])]
            if not lines:
                continue
            head = f"{labels.get(kind, kind)}："
            if used + len(head) + len(lines[0]) > limit:
                break
            out.append(head)
            used += len(head)
            for line in lines:
                if used + len(line) > limit:
                    break
                out.append(line)
                used += len(line)
        return "\n".join(out)

    def counts(self) -> dict:
        hide, more = self._visible()
        with self._lock:
            out: dict = {}
            for kind, status, n in self.db.execute(
                    "SELECT kind, status, COUNT(*) FROM notes WHERE 1=1" + hide + " GROUP BY kind, status", more):
                out.setdefault(kind, {})[status] = n
            return out


class WholeMemory(Memory):
    """The whole memory, for luoke-mem's own interface only (top-level ADR 0002): every tool's records, every
    note's evidence whoever wrote it, forgetting by source and clearing everything. What the user writes here
    is the tool "luoke-mem". The rule that a tool reads only its own records lives in the library, not in a
    security boundary, so no tool uses this class."""
    _whole = True

    def __init__(self, path: str | None = None, *, regrade: bool = True):
        super().__init__(path, tool=KEEPER)
        if regrade:
            self._switch()

    # ---- the table applied to what is there (ADR 0012 amendment, 0.18) ----------------------------------------
    def _learning(self, name: str) -> str | None:
        with self._lock:
            row = self.db.execute("SELECT value FROM learning WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def _switch(self, now: str | None = None) -> None:
        """The first time: the whole memory is copied aside (REGRADE_BACKUP, deleted after BACKUP_DAYS or when the
        memory is cleared), then what is in effect is graded by the table; after that, at every opening, so what an
        older library in a tool still concludes is graded too. Without a copy, nothing is changed."""
        at = _now(now)
        done = self._learning("regrade.0.18")
        if not done:
            backup = self.path + REGRADE_BACKUP
            try:
                if not os.path.exists(backup):
                    self._backup(backup)
            except (OSError, sqlite3.Error):
                return
            ids = self.regrade(now=at)
            self.merge_similar()
            restored = self.restore_catch_all(now=at)
            answered = self.repair_answers(now=at)
            with self._lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('repair.answers', ?)",
                                (json.dumps({"at": at, "notes": answered}),))
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('repair.catch-all', ?)",
                                (json.dumps({"at": at, "restored": restored}),))
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('regrade.0.18', ?)",
                                (json.dumps({"at": at, "backup": os.path.basename(backup), "ids": ids}),))
            return
        self.regrade(now=at)
        self.merge_similar()
        if not self._learning("repair.answers"):
            with self._lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('repair.answers', ?)",
                                (json.dumps({"at": at, "notes": self.repair_answers(now=at)}),))
        if not self._learning("repair.people-0.25.4"):
            n = self.unfold_people(now=at)
            with self._lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('repair.people-0.25.4', ?)",
                                (json.dumps({"at": at, "restored": n}),))
        if not self._learning("repair.catch-all"):
            n = self.restore_catch_all(now=at)
            with self._lock, self.db:
                self.db.execute("INSERT OR REPLACE INTO learning (name, value) VALUES ('repair.catch-all', ?)",
                                (json.dumps({"at": at, "restored": n}),))
        try:
            first = json.loads(done).get("at") or at
        except (ValueError, AttributeError):
            first = at
        if _days(first[:19], at) > BACKUP_DAYS:
            _drop_backup(self.path)

    def _backup(self, dest: str) -> None:
        fd = os.open(dest, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        out = sqlite3.connect(dest)
        try:
            with self._lock:
                self.db.backup(out)
        except BaseException:
            out.close()
            os.remove(dest)
            raise
        out.close()

    def repair_answers(self, *, now: str | None = None) -> list[int]:
        """Before 0.20 "改成…" kept whatever the user wrote as a new understanding, even "那是非常久之前的了": such a
        note goes, pointing at the one it replaced, and that one is 以前的事 (or wrong), as the user meant. Once."""
        with self._lock:
            rows = self.db.execute("SELECT n.id, n.text, o.id FROM notes n JOIN notes o ON o.superseded_by=n.id WHERE"
                                   " n.origin='user' AND n.status='active'").fetchall()
        out = []
        for nid, text, old in rows:
            said = answer_says(text)
            if not said:
                continue
            with self._lock, self.db:
                self.db.execute("UPDATE notes SET status=?, superseded_by=NULL, decided_at=? WHERE id=?",
                                ("past" if said == "past" else "rejected", _now(now), old))
                self.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (old, nid))
            out.append(old)
        return out

    def restore_catch_all(self, *, now: str | None = None) -> list[int]:
        """Before 0.19.1 a fact on a catch-all topic (其他) took the place of the one before it, as if the topic held
        one value: those come back, graded by the table (a guess, 观察中, or likely). Once, when the whole memory
        opens. What the user rewrote stays superseded. Returns the notes restored."""
        marks = ",".join("?" * len(CATCH_ALL))
        with self._lock:
            rows = self.db.execute(
                f"SELECT n.id, n.kind FROM notes n JOIN notes m ON m.id=n.superseded_by WHERE n.status='superseded'"
                f" AND n.origin='inferred' AND n.decided_at IS NULL AND n.topic IN ({marks}) AND m.topic=n.topic"
                f" AND m.origin='inferred'", CATCH_ALL).fetchall()
        out = []
        for nid, kind in rows:
            cites = self._cites_of(nid)
            speakers = self._speakers(cites) if cites else set()
            status = "active" if "third_party" not in speakers and self._likely(kind, cites) else "proposed"
            with self._lock, self.db:
                self.db.execute("UPDATE notes SET status=?, superseded_by=NULL WHERE id=?", (status, nid))
            out.append(nid)
        return out

    def unfold_people(self, *, now: str | None = None) -> list[int]:
        """0.25.0-0.25.3 took any "X:Y" topic for an institution, so two things about one colleague ("同事:某人") or
        a matter and an institution of one name ("工作:X", "机构:X") were folded into one: those come apart again,
        graded by the table. Once, when the whole memory opens. Evidence the first took over stays with it (one mail
        may well bear out both). Returns the notes restored."""
        with self._lock:
            rows = self.db.execute(
                "SELECT n.id, n.kind, n.text, n.topic, m.topic, m.text, n.data_json FROM notes n JOIN notes m ON m.id=n.superseded_by"
                " WHERE n.kind='fact' AND m.kind='fact' AND n.status='superseded' AND n.origin='inferred'"
                " AND n.decided_at IS NULL").fetchall()
        out = []
        for nid, kind, text, topic, into, into_text, data_json in rows:
            if _split_off({"data_json": data_json}) or _same_fact(into, into_text, topic, text):
                continue
            a, b = (str(t or "").replace("：", ":") for t in (topic, into))
            if not (":" in a and ":" in b and a.partition(":")[2].strip().lower() == b.partition(":")[2].strip().lower()):
                continue                            # not folded by the institution rule: superseded for its own reasons
            cites = self._cites_of(nid)
            speakers = self._speakers(cites) if cites else set()
            if "third_party" in speakers:
                status = "active" if not _needs_word(sensitive_kinds(text)) and self._well_seen(cites, now) else "observed"
            else:
                status = "active" if self._likely(kind, cites, text) else "proposed"
            with self._lock, self.db:
                self.db.execute("UPDATE notes SET status=?, superseded_by=NULL WHERE id=?", (status, nid))
            out.append(nid)
        return out

    def split(self, nid: int, *, now: str | None = None) -> None:
        """The user parts a note folded into another (two accounts at one bank): it stands on its own again, a guess,
        and is never folded in again."""
        n = self.note(nid)
        if not n or n["status"] != "superseded" or not n.get("superseded_by") or n.get("decided_at") or n["origin"] != "inferred":
            raise ValueError("这一条不是并进别的那条里的")
        data = dict(n.get("data") or {}, split=True)
        with self._lock, self.db:
            self.db.execute("UPDATE notes SET status='proposed', superseded_by=NULL, data_json=? WHERE id=?",
                            (json.dumps(data, ensure_ascii=False), nid))

    def merged(self, nid: int) -> list[dict]:
        """What was folded into this note (one fact written several times), for the page to show and part again."""
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id, text, topic, formed_at FROM notes WHERE superseded_by=? AND status='superseded' AND"
                " origin='inferred' AND decided_at IS NULL ORDER BY formed_at, id", (nid,))]

    def merge_similar(self) -> int:
        """Topics named in other words are one (SIMILAR_KINDS): the later one's evidence goes to the first, which it
        then points at (superseded). What the user confirmed is kept first; what they took a stand on is never folded
        into another. Returns how many were folded."""
        folded = 0
        for kind in SIMILAR_KINDS:
            with self._lock:
                rows = [dict(r) for r in self.db.execute(
                    "SELECT id, text, decided_at FROM notes WHERE kind=? AND status IN ('active', 'proposed', 'observed')"
                    " ORDER BY decided_at IS NULL, status<>'active', formed_at, id", (kind,))]
            kept: list = []
            for n in rows:
                into = next((k for k in kept if _similar(k["text"], n["text"])), None)
                if not into or n["decided_at"]:
                    kept.append(n)
                    continue
                with self._lock, self.db:
                    self.db.execute("INSERT OR IGNORE INTO cites (note_id, record_id) SELECT ?, record_id FROM cites WHERE note_id=?",
                                    (into["id"], n["id"]))
                    self.db.execute("UPDATE notes SET status='superseded', superseded_by=? WHERE id=?", (into["id"], n["id"]))
                    self.db.execute("UPDATE asks SET status='withdrawn', decided_at=? WHERE about_note=? AND status='open'",
                                    (_now(None), n["id"]))
                folded += 1
        return folded

    def regrade(self, *, now: str | None = None) -> list[int]:
        """What is in effect without the user's word, graded again by the table (GRADES): what the user's records no
        longer bear out goes back to a guess; a fact from others that is no longer plain or well seen goes back to
        观察中. What the user confirmed, rejected or said stays as it is. Only ever down: up is conclude's, with new
        evidence. Returns the notes that went down."""
        with self._lock:
            rows = self.db.execute("SELECT id, kind, text, data_json FROM notes WHERE status='active' AND origin='inferred'"
                                   " AND decided_at IS NULL").fetchall()
        down: dict = {"proposed": [], "observed": []}
        for nid, kind, text, data_json in rows:
            cites = self._cites_of(nid)
            speakers = self._speakers(cites) if cites else set()
            if "third_party" in speakers:
                try:
                    about = (json.loads(data_json) or {}).get("about") if data_json else None
                except (ValueError, AttributeError):
                    about = None
                plain = kind == "fact" and not _needs_word(sensitive_kinds(text)) and self._well_seen(cites, now)
                if not plain:
                    down["proposed"].append(nid)
            elif not self._likely(kind, cites, text):
                down["proposed"].append(nid)
        with self._lock:                         # interests drawn from mail: the best MAX_ABOUT, the rest wait
            mail = [r[0] for r in self.db.execute(
                "SELECT n.id FROM notes n WHERE n.kind='interest' AND n.status='active' AND n.origin='inferred'"
                " AND n.decided_at IS NULL AND EXISTS (SELECT 1 FROM cites c JOIN records r ON r.id=c.record_id"
                " WHERE c.note_id=n.id AND r.tool='luoke-mail') ORDER BY (SELECT COUNT(*) FROM cites c WHERE"
                " c.note_id=n.id) DESC, n.formed_at DESC").fetchall() if r[0] not in down["proposed"]]
        down["proposed"] += mail[MAX_ABOUT:]
        with self._lock, self.db:
            for status, ids in down.items():
                if ids:
                    self.db.execute(f"UPDATE notes SET status=? WHERE id IN ({','.join('?' * len(ids))})", [status] + ids)
        self.age(now=now)
        return sorted(down["proposed"] + down["observed"])

    def age(self, *, now: str | None = None) -> list[int]:
        """What changes (work, city, interests, accounts, habits; not a name or a school one went to), with nothing
        newer than `_past_days` behind it, is 以前的事: kept, with when it was last so, not used as now and not
        asked about as now. Whatever the user said: their word of long ago is old too. Returns those that went."""
        at = _now(now)
        with self._lock:
            rows = self.db.execute("SELECT id, kind, topic, text FROM notes WHERE status IN ('active', 'proposed', 'observed')"
                                   ).fetchall()      # a guess of long ago too
        gone = []
        for nid, kind, topic, text in rows:
            if _lasting(kind, topic, text):
                continue
            fresh = self._fresh(nid)
            if fresh and _days(fresh, at) > self._past_days(kind):
                gone.append(nid)
        if gone:
            with self._lock, self.db:
                self.db.execute(f"UPDATE notes SET status='past' WHERE id IN ({','.join('?' * len(gone))})", gone)
                self.db.execute(f"UPDATE asks SET status='withdrawn', decided_at=? WHERE status='open' AND about_note IN"
                                f" ({','.join('?' * len(gone))})", [at] + gone)
        return gone

    def records(self, *, tool: str | None = None, topic: str | None = None, kind: str | None = None,
                speaker: str | None = None, ref: str | None = None, since: str | None = None,
                until: str | None = None, limit: int | None = None, offset: int = 0) -> list[dict]:
        """Any tool's records; `since` / `until` bound when the memory learnt of them ([since, until))."""
        return self._records(tool, topic=topic, kind=kind, speaker=speaker, ref=ref, since=since, until=until,
                             limit=limit, offset=offset)

    def daily(self, since: str) -> list[dict]:
        """How many records each tool wrote each day from `since` on: [{"day", "tool", "count"}], by when the
        memory learnt of them. What the page shows so the user sees collecting happen, before any
        understanding is drawn from it."""
        with self._lock:
            return [{"day": d, "tool": t, "count": n} for d, t, n in self.db.execute(
                "SELECT substr(recorded_at, 1, 10) AS day, tool, COUNT(*) FROM records WHERE recorded_at>=?"
                " GROUP BY day, tool ORDER BY day, tool", (since,))]

    def ask(self, kind: str, text: str, why: str, *, urgency: str = "normal", answer: dict | None = None,
            about_note: int | None = None, about_kind: str | None = None, expires_at: str | None = None,
            sensitive: list[str] | None = None, evidence: str | None = None, rank: int = 0, action: dict | None = None,
            key: str | None = None, now: str | None = None) -> int | None:
        """Something the memory would like to ask or suggest. Stored, never sent: luoke-agent picks when to ask.
        The same open question is not asked twice. `answer` says how it is answered: {"type": "yes_no"},
        {"type": "choice", "options": [...]} or {"type": "text"}. It is as sensitive as the note it is about.
        `key` names the matter (ADR 0012): none is made while one with the same key is open or put off, after
        the user said never, or within REASK_DAYS of their last answer -- returns None then. `evidence` is what it
        rests on, in words; `rank` how much it is worth; `action` what a tool would do ({tool, capability, params}),
        for luoke-agent to carry out with the user's say."""
        if kind not in ASK_KINDS or urgency not in URGENCY:
            raise ValueError(f"unknown kind or urgency: {kind}, {urgency}")
        answer = answer or {"type": "yes_no" if kind == "suggestion" else "text"}
        if answer.get("type") not in ("yes_no", "choice", "text") or (answer["type"] == "choice" and not answer.get("options")):
            raise ValueError("an answer is yes_no, choice with options, or text")
        kinds = _kinds(sensitive)
        if about_note is not None:
            n = self.note(about_note)
            if not n:
                raise ValueError("no such note")
            kinds |= _split(n["sensitive"])
        at = _now(now)
        with self._lock, self.db:                # left unanswered past its time: no longer holds its place
            self.db.execute("UPDATE asks SET status='withdrawn', decided_at=? WHERE status='open' AND expires_at IS NOT NULL"
                            " AND expires_at<=?", (at, at))
            for aid, t in self.db.execute("SELECT id, text FROM asks WHERE status='open'").fetchall():
                if _same(t, text):
                    return aid
            if key:
                for status, decided in self.db.execute("SELECT status, decided_at FROM asks WHERE key=?", (key,)).fetchall():
                    if status in ("open", "never") or (decided and _days(decided[:19], at) < (
                            LATER_DAYS if status in ("later", "withdrawn") else REASK_DAYS)):
                        return None
            cur = self.db.execute(
                "INSERT INTO asks (tool, kind, text, why, about_note, about_kind, urgency, answer, status, created_at,"
                " expires_at, sensitive, evidence, rank, action, key) VALUES (?,?,?,?,?,?,?,?,'open',?,?,?,?,?,?,?)",
                (self.tool, kind, " ".join(text.split()), why, about_note, about_kind, urgency,
                 json.dumps(answer, ensure_ascii=False), at, expires_at, _join(kinds), evidence, str(int(rank)),
                 json.dumps(action, ensure_ascii=False) if action else None, key))
            if kinds:
                self._protect()
            return cur.lastrowid

    def withdraw(self, aid: int, *, now: str | None = None) -> None:
        """No longer worth asking (the memory learnt it otherwise, or it is out of date)."""
        with self._lock, self.db:
            self.db.execute("UPDATE asks SET status='withdrawn', decided_at=? WHERE id=? AND status='open'", (_now(now), aid))

    def forget_all(self) -> None:
        """Clear the whole memory: every tool's records and notes, and what luoke-mem was learning from them."""
        with self._lock, self.db:
            for t in ("cites", "notes", "records", "candidates", "learning", "asks"):
                self.db.execute(f"DELETE FROM {t}")
            self.db.execute("INSERT INTO learning (name, value) VALUES ('regrade.0.18', '{}')")   # nothing to grade
        self._wipe()                              # the copy made before grading goes too

    def grants(self) -> list[dict]:
        """What each tool asked to read and what the user said: [{"tool", "category", "status", "why", "at"}]."""
        with self._lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM grants ORDER BY status='asked' DESC, tool, category")]

    def grant(self, tool: str, kind: str, *, now: str | None = None) -> None:
        self._answer(tool, kind, "granted", now)

    def deny(self, tool: str, kind: str, *, now: str | None = None) -> None:
        """No, or no longer: the tool stops seeing that kind at once, and does not ask again."""
        self._answer(tool, kind, "denied", now)

    def _answer(self, tool: str, kind: str, status: str, now: str | None) -> None:
        _kinds([kind])
        with self._lock, self.db:
            row = self.db.execute("SELECT why FROM grants WHERE tool=? AND category=?", (tool, kind)).fetchone()
            self.db.execute("INSERT OR REPLACE INTO grants (tool, category, status, why, at) VALUES (?,?,?,?,?)",
                            (tool, kind, status, row[0] if row else None, _now(now)))

    def sources(self) -> list[dict]:
        """Which tools wrote into the memory, and how much: [{"tool", "records", "notes"}]."""
        with self._lock:
            out: dict = {}
            for table, key in (("records", "records"), ("notes", "notes")):
                for tool, n in self.db.execute(f"SELECT tool, COUNT(*) FROM {table} GROUP BY tool"):
                    out.setdefault(tool, {"tool": tool, "records": 0, "notes": 0})[key] = n
            return sorted(out.values(), key=lambda s: s["tool"])
