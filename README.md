简体中文 | [English](README.en.md)

# 螺壳记忆 luoke-mem

螺壳各个应用共用的一份记忆，就在你的 Mac 上：记下你在螺壳里说过的话、做过的事，慢慢形成对你的理解。每一条都看得到依据，不对就说不对，不想要就删掉。

**English.** Luoke Memory is the on-device memory shared by the Luoke apps on your Mac. It keeps what you said and did in them, and the understanding drawn from that: facts about you, topics you follow, what you are not interested in, how you handle mail, the phrases you use. Every item cites its evidence, carries a confidence level, and can be corrected or deleted. One SQLite file, Python standard library only, nothing leaves the machine.

## 在螺壳里的位置

[螺壳](https://github.com/luokelabs/luoke)是螺壳实验室（Luoke Labs）做的一组本地 AI 工具。分工是：推理归螺壳（luoke-core），记忆归螺壳记忆，业务归各个应用。各应用把"发生了什么"写成记录，结论都由记忆来下：关于你、关注的话题、不感兴趣、处理习惯、常用说法，每条带依据和把握程度。

- [螺壳邮件助手](https://github.com/luokelabs/luoke/blob/main/projects/luoke-mail.md)：分拣邮件时参考「关于你」；订阅精选按关注的话题挑，你点的「有用 / 没兴趣」也记在这里。
- [螺壳输入法](https://github.com/luokelabs/luoke/blob/main/projects/luoke-ime.md)：记下上屏的文字，归纳你常用的说法，用来挑候选和联想。
- 螺壳本身：把记忆打包在里面，空闲时调度归纳，菜单「记忆」打开记忆的页面。

对用户来说，螺壳记忆不是一个单独的应用：入口在螺壳里，打开就是一个本机网页，能看全部理解和依据、确认、否定、修改、按来源忘掉、清空。

系统文档里的介绍：[记忆](https://luokelabs.github.io/luoke-atlas/#mem)、[接口](https://luokelabs.github.io/luoke-atlas/#ref-mem)；本组件的注册页：[projects/luoke-mem.md](https://github.com/luokelabs/luoke/blob/main/projects/luoke-mem.md)。

## 现状

封闭测试中。beta1 用的是 0.25.8（`beta` 分支）；`main` 上是 0.26.2。

记忆随螺壳安装，要求和螺壳一样：Apple 芯片的 Mac、macOS 26.2 以上、内存 16 GB 起（见顶层 [README](https://github.com/luokelabs/luoke#下载和反馈)）。记忆本身不需要模型；从打字和邮件里归纳理解要用螺壳的模型，没有螺壳时只是不归纳，读写照常。

## 怎么用

到 [luoke.ai](https://luoke.ai) 下载螺壳，在「应用」里开启邮件助手或输入法，记忆就跟着用起来了。从螺壳菜单栏的「记忆」打开记忆的页面。

## 从源码运行和开发

只用 Python 标准库，Python 3.9 起（macOS 自带的就行），不用装依赖。

```bash
git clone https://github.com/luokelabs/luoke-mem.git
cd luoke-mem
python3 -m unittest discover -s tests
```

测试把 `LUOKE_HOME` 固定在临时目录，不会碰到你本机已有的记忆。

作为库用：

```python
from luoke_mem import Memory

mem = Memory("/tmp/demo-mem.db", tool="luoke-mail")      # 不给路径就是 ~/.luoke/mem.db
mem.state("interest", "本地大模型")                       # 用户亲口说的
rid = mem.record("user", "activity", "打开过：本地大模型周报", topic="mail-read")
nid = mem.propose("interest", "AI 编程工具", cites=[rid])   # 推测，待确认
mem.confirm(nid)
print(mem.context(["fact", "interest", "dislike"]))       # 给模型的一段文字
```

命令行：

```bash
python3 -m luoke_mem --path /tmp/demo-mem.db ui --open                  # 打开记忆的页面
python3 -m luoke_mem --path /tmp/demo-mem.db sources                    # 各应用写了多少
python3 -m luoke_mem --path /tmp/demo-mem.db forget-tool luoke-mail --dry-run
echo '{"speaker":"user","kind":"statement","text":"…","topic":"demo"}' | python3 -m luoke_mem --path /tmp/demo-mem.db record --tool demo
```

`record` 给不是 Python 写的应用用：标准输入一行一条 JSON，标准输出一行对一行回 `{"id"}` 或 `{"error"}`。

评测：`bench/run_typing.py` 和 `bench/run_topics.py` 拿编造的打字和邮箱，经螺壳跑归纳任务打分，要指定一个螺壳实例的 socket（`--hub`）。

## 数据和隐私

- **只在本机**：一个 SQLite 文件 `~/.luoke/mem.db`（目录 700、文件 600，只有你的账户能读；位置可以用 `LUOKE_HOME` 换）。
- **不联网**：记忆只连本机——空闲时归纳要用模型，连螺壳的本机 socket；记忆的页面只监听 127.0.0.1，地址里带令牌，页面不加载任何外部内容。
- **每条有依据**：每条理解都引用它依据的记录，能看到是从哪句话、哪封邮件得出的；三档把握程度（猜测、比较确定、确定），比较确定以上各应用才用。
- **能改能删**：修改保留旧版本；删除连同依据一起删，只靠被删记录得出的理解也一起删；从文件里也抹掉（`secure_delete`）。卸载一个应用时，它写进记忆的要不要一起忘掉由你选。
- **凭证只遮那一串**：密码、验证码、密钥、完整卡号和证件号写进来之前换成占位。健康、法律纠纷、宗教政治标上类别，从中推出来的只认你自己说的或确认过的。

设计和原则见 [DESIGN.md](DESIGN.md)。

## 文档

- [DESIGN.md](DESIGN.md)：原则、数据、接口
- [CHANGELOG.md](CHANGELOG.md)：每一版改了什么
- [bench/](bench/)：归纳任务的评测集和跑法
- [docs/community/luoke-mem.md](docs/community/luoke-mem.md)：给官网用的事实和数字
- 涉及多个组件的决定：顶层 [docs/adr/](https://github.com/luokelabs/luoke/tree/main/docs/adr)

## 许可、安全、贡献

- 许可：[MIT](LICENSE)，© 2026 螺壳实验室 Luoke Labs
- 安全问题：见 [SECURITY.md](SECURITY.md)
- 参与：见 [CONTRIBUTING.md](CONTRIBUTING.md)
