import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolate  # noqa: E402,F401  -- first: a temporary home; the real memory and hub are refused

from luoke_mem import find_secrets, mask, mask_secrets, never_kept, sensitive, sensitive_kinds  # noqa: E402


class SensitiveTests(unittest.TestCase):
    """Top-level ADR 0013: only credentials are masked, only that string; sensitive context is marked, not hidden."""

    def test_credentials_are_masked_where_they_are(self):
        for text, masked in (("验证码 482913，5 分钟内有效", "验证码 〔验证码〕，5 分钟内有效"),
                             ("482913 是你的验证码", "〔验证码〕 是你的验证码"),
                             ("Your verification code: 774201", "Your verification code: 〔验证码〕"),
                             ("我的密码是 Lk-2026-qwe，别外传", "我的密码是 〔密码〕，别外传"),
                             ("护照号 E12345678", "护照号 〔证件号〕"),
                             ("卡号 4111 1111 1111 1111 已绑定", "卡号 〔卡号〕 已绑定"),
                             ("身份证 110101199003070017", "身份证 〔证件号〕"),
                             ("key: sk-abc123def456ghi789jkl0", "key: 〔密钥〕"),
                             ("AKIAABCDEFGHIJKLMNOP 是那个", "〔密钥〕 是那个")):
            self.assertEqual(mask_secrets(text), masked, text)
            self.assertTrue(never_kept(text), text)
        self.assertIs(mask, mask_secrets)
        self.assertEqual([k for _, _, k in find_secrets("密码是 abc123；验证码 482913")], ["password", "code"])

    def test_as_luoke_search_found_luoke_mem_4(self):
        """luoke-mem#4: 护照号码 as well as 护照号; full-width digits; a long run of blanks is not slow."""
        import time
        self.assertEqual(mask_secrets("护照号码：E12345678"), "护照号码：〔证件号〕")
        self.assertEqual(mask_secrets("身份证 １１０１０１１９９００３０７００１７"), "身份证 〔证件号〕")
        self.assertEqual(mask_secrets("卡号 ４１１１ １１１１ １１１１ １１１１ 已绑定"), "卡号 〔卡号〕 已绑定")
        self.assertEqual(mask_secrets("验证码　４８２９１３"), "验证码　〔验证码〕")
        for word in ("密码已重置", "验证码已发送"):
            self.assertEqual(mask_secrets(word), word)
        t0 = time.time()
        for blank in (" " * 8000, "\u3000" * 8000):
            for kw in ("验证码", "密码"):
                mask_secrets(kw + blank + "x")
        self.assertLess(time.time() - t0, 1.0)

    def test_what_is_not_a_credential_stays(self):
        for text in ("手机 138 1234 5678", "订单号 20391837", "月薪 3 万元", "我的账号是 xiaolin", "银行卡尾号 7890",
                     "commit 5fee45b", "https://github.com/luoke/luoke-mem", "打开 GPT-4o-mini 试试", "token 太多了",
                     "4111 1111 1111 1112", "2026-10-19 10:00:00", "职位：产品负责人", "", None):
            self.assertEqual(mask_secrets(text), text or "", text)
            self.assertFalse(never_kept(text), text)

    def test_sensitive_context_is_marked_by_kind_and_money_is_not_one(self):
        for text, kinds in (("最近在医院复查", ["health"]), ("他在打官司，下周起诉", ["legal"]), ("信仰什么宗教", ["belief"]),
                            ("体检报告和贷款", ["health"]), ("月薪 3 万元", []), ("$20 一个月", [])):
            self.assertEqual(sensitive_kinds(text), kinds, text)
        for text in ("这个毛病改掉了", "电脑中了病毒"):
            self.assertFalse(sensitive(text), text)


if __name__ == "__main__":
    unittest.main()
