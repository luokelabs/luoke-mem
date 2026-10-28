# 参与螺壳记忆

螺壳现在处于封闭测试。欢迎开 issue 报问题、提想法；要改代码，先开 issue 说明想改什么、为什么，商定了再提 PR。安全问题不要开公开 issue，见 [SECURITY.md](SECURITY.md)。

## 基本要求

- **只用标准库**，要能在 macOS 自带的 Python 3.9 上跑；新文件开头写 `from __future__ import annotations`。
- **测试**：在 3.9 和较新的 Python 上各跑一遍，较新的把资源警告当错误：

  ```bash
  python3 -m unittest discover -s tests
  python3.14 -W error::ResourceWarning -m unittest discover -s tests
  ```

- **不碰真实的记忆**：实验和测试一律用临时路径（`Memory(path)`、`--path`、`LUOKE_HOME`），不要拿 `~/.luoke/mem.db` 试；issue 和 PR 里也不要贴真实的记忆、邮件或打的字。
- **接口只加不改**：几个应用依赖这个库，schema 只加列、不改不删；改了对外接口，在 [DESIGN.md](DESIGN.md) 里写明。
- **评测集是编造的**：`bench/` 里的打字和邮箱都是虚构的人，加例子也只用编造的内容。
- 文档用简体中文，代码标识、路径、API 名保留英文；提交信息用英文。
- **README 有两份**：`README.md`（中文）和 `README.en.md`（英文全文），改一份就同一次提交里改另一份。

## 行为准则

友善、就事论事，尊重每一位参与者。
