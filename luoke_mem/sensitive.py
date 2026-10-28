"""Credentials, and what an understanding needs the user's word for (top-level ADR 0013).

The machine is the boundary: everything a tool sees may go into the memory, so the memory can understand the user.
Two things are left, neither of them about privacy:

- credentials -- passwords, one-time codes, keys and tokens, full card and ID numbers -- are of no use for
  understanding anyone and do the most harm if they leak: only that string is masked, where it is
  (`mask_secrets(text)`, `find_secrets(text)`), the rest is kept. A phone number, an account name, an amount of
  money are not credentials. The library masks whatever is written (records and notes), whatever the tool did;
- sensitive context: health, legal disputes, religion and politics (`sensitive_kinds(text)`) is marked, never
  hidden; an understanding drawn about it takes effect only when the user says or confirms it (a wrong guess there
  hurts). Money is not among them any more ("finance" stays a known mark for what older libraries wrote).

`never_kept(text)` and `sensitive(text)` are kept for tools written before: never_kept says there is a credential
in the text (mask it rather than drop it); sensitive says there is a credential or a sensitive mark."""
from __future__ import annotations

import re

SENSITIVE_KINDS = {"health": "健康", "finance": "财务", "legal": "法律纠纷", "belief": "宗教和政治"}
CONFIRM_KINDS = ("health", "legal", "belief")   # drawn about these, only the user's word makes it count
SECRET_NAMES = {"password": "密码", "code": "验证码", "key": "密钥", "card": "卡号", "id": "证件号"}

_KINDS = {
    "health": re.compile(r"(?<!毛)病(?!毒)|诊断|医院|治疗|药|健康|(?<!螺壳)体检|怀孕|心理|抑郁|复诊|手术"),   # not 毛病,
                                                                                    # 病毒, 螺壳体检 (luoke-mem#7)
    "legal": re.compile(r"诉讼|起诉|律师函|官司|离婚|仲裁"),
    "belief": re.compile(r"宗教|信仰|政治|党员"),
}

# a value said to be a password or a code, right after the word (密码是 abc123, 验证码：482913, code 482913)
_SAID = [
    # "\s*(?:[是为:：=]\s*)?" and not "\s*(?:是|…|\s)\s*": the latter backtracks quadratically over a long run of blanks
    # (luoke-mem#4: 8000 spaces took 14 s); Python 3.9 has no possessive quantifiers
    ("password", re.compile(r"(?:密码|口令|password|passwd|passcode|pwd)\s*(?:[是为:：=]\s*)?(?<=[\s是为:：=])([^\s，。,;；、）)]{3,64})", re.I)),
    ("code", re.compile(r"(?:验证码|校验码|动态码|确认码|安全码|one[- ]time code|verification code|security code|\bOTP\b|\bcode\b)"
                        r"\s*(?:[是为:：=]\s*)?([0-9]{4,8}|[A-Z0-9]{6})\b", re.I)),
    ("code", re.compile(r"\b([0-9]{4,8})\s*(?:是|为|is)\s*(?:你的|您的|your)?\s*(?:验证码|校验码|动态码|verification code|code)", re.I)),
    ("id", re.compile(r"(?:护照(?:号码?)?|passport(?:\s*(?:no\.?|number))?)\s*(?:[是为:：]\s*)?([A-Z]{1,2}[0-9]{6,9})\b", re.I)),
]
# a string that is a secret wherever it stands
_ALONE = [
    ("key", re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghs|ghu|glpat|xox[abp])[-_][A-Za-z0-9_-]{16,}")),
    ("key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("key", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),          # a JWT
    ("key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("key", re.compile(r"(?<![A-Za-z0-9/+_-])(?=[A-Za-z0-9_-]*[0-9])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])"
                       r"[A-Za-z0-9_-]{32,}(?![A-Za-z0-9/+_-])")),                                    # long, mixed, random
    ("id", re.compile(r"(?<![0-9])[1-9][0-9]{5}(?:19|20)[0-9]{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12][0-9]|3[01])[0-9]{3}[0-9Xx](?![0-9])")),
    ("card", re.compile(r"(?<![0-9])(?:[0-9][ -]?){12,18}[0-9](?![0-9])")),                            # checked by Luhn below
]


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# full-width letters, digits and signs (U+FF01-FF5E) and the ideographic space, folded one for one to their ASCII
# selves before looking: "１１０１…" is an ID number too (luoke-mem#4); positions stay those of the original
_FOLD = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FOLD[0x3000] = 0x20


def find_secrets(text: str | None) -> list[tuple]:
    """The credentials in the text: [(start, end, kind)], kind one of SECRET_NAMES, in order, not overlapping."""
    text = (text or "").translate(_FOLD)
    found = []
    for kind, rx in _SAID:
        for m in rx.finditer(text):
            found.append((m.start(1), m.end(1), kind))
    for kind, rx in _ALONE:
        for m in rx.finditer(text):
            if kind == "card":
                digits = re.sub(r"[ -]", "", m.group(0))
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            found.append((m.start(), m.end(), kind))
    out = []
    for start, end, kind in sorted(found, key=lambda f: (f[0], -f[1])):
        if out and start < out[-1][1]:
            continue
        out.append((start, end, kind))
    return out


def mask_secrets(text: str | None) -> str:
    """The text with each credential replaced by what it was (〔验证码〕), everything else as it is."""
    text = text or ""
    out, last = [], 0
    for start, end, kind in find_secrets(text):
        out.append(text[last:start])
        out.append(f"〔{SECRET_NAMES[kind]}〕")
        last = end
    return "".join(out) + text[last:]


mask = mask_secrets


def never_kept(text: str | None) -> bool:
    """There is a credential in the text (before 0.23 such text was dropped; now mask_secrets() it)."""
    return bool(find_secrets(text))


def sensitive_kinds(text: str | None) -> list[str]:
    """The kinds of sensitive context in the text, to mark it with: [] when there is none. Money is not one."""
    return [k for k, rx in _KINDS.items() if rx.search(text or "")]


def sensitive(text: str | None) -> bool:
    """A credential or a sensitive mark: for tools written before 0.23."""
    return never_kept(text) or bool(sensitive_kinds(text))
