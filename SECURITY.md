# 安全问题

如果你发现螺壳记忆的安全问题（会让记忆里的内容离开本机、被别的账户或网页读到、绕过记忆页面的令牌校验、凭证没被遮掉等），请**不要在公开 issue 里写细节**，用 GitHub 的私密漏洞报告：

1. 打开本仓库的 **Security** 页；
2. 点 **Report a vulnerability**，写清楚版本、怎么复现、可能的影响。

我们会尽快回复，修好以后在发布说明里注明。

请不要在报告里附上真实的记忆内容、邮件或打的字；需要样本时用编造的数据，或者用一份临时的记忆文件（`Memory("/tmp/x.db")`、`python3 -m luoke_mem --path /tmp/x.db …`）复现。

---

If you find a security issue in Luoke Memory, please report it privately via **Security → Report a vulnerability** on this repository. Please don't include real memory contents, mail or typing in reports; reproduce with a temporary memory file and made-up data.
