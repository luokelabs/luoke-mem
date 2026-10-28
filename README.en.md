[简体中文](README.md) | English

# Luoke Memory (luoke-mem)

One memory shared by all the Luoke apps, right on your Mac. It keeps what you said and did in Luoke, and slowly forms an understanding of you. Every item shows what it rests on: if it's wrong, say so; if you don't want it, delete it.

## Where it sits in Luoke

[Luoke](https://github.com/luokelabs/luoke) is a family of local AI tools made by Luoke Labs. The split: inference belongs to Luoke itself (luoke-core), memory belongs to Luoke Memory, and each app does its own job. Apps write down *what happened* as records; only the memory draws conclusions — facts about you, topics you follow, what you're not interested in, how you handle mail, the phrases you use — each with its evidence and a confidence level.

- [Luoke Mail](https://github.com/luokelabs/luoke/blob/main/projects/luoke-mail.md): uses "about you" when sorting mail; picks the digest by the topics you follow, and your "useful / not interested" answers are kept here too.
- [Luoke Input Method](https://github.com/luokelabs/luoke/blob/main/projects/luoke-ime.md): keeps the text you commit, learns the phrases you use, and uses them to rank candidates and suggest what comes next.
- Luoke itself: packs the memory inside, runs the learning when the Mac is idle, and opens the memory page from its "Memory" menu.

To the user, Luoke Memory is not a separate app: it opens from inside Luoke as a local web page, where you can see every understanding and its evidence, confirm, reject, revise, forget by source, or clear everything.

In the system documentation: [Memory](https://luokelabs.github.io/luoke-atlas/#mem), [interface](https://luokelabs.github.io/luoke-atlas/#ref-mem); this component's registry page: [projects/luoke-mem.md](https://github.com/luokelabs/luoke/blob/main/projects/luoke-mem.md) (in Chinese).

## Status

In closed testing. beta1 uses 0.25.8 (the `beta` branch); `main` is at 0.26.2.

The memory is installed with Luoke and needs what Luoke needs: a Mac with Apple silicon, macOS 26.2 or later, 16 GB of memory or more (see the top-level [README](https://github.com/luokelabs/luoke/blob/main/README.en.md#download-and-feedback)). The memory itself needs no model; learning from typing and mail uses Luoke's models. Without Luoke it simply doesn't learn — reading and writing work as usual.

## How to use it

Download Luoke from [luoke.ai](https://luoke.ai) and turn on Luoke Mail or the input method under "Apps"; the memory comes with them. Open the memory page from "Memory" in Luoke's menu bar.

## Running from source and developing

Python standard library only, Python 3.9 or later (the one that comes with macOS will do), nothing to install.

```bash
git clone https://github.com/luokelabs/luoke-mem.git
cd luoke-mem
python3 -m unittest discover -s tests
```

The tests pin `LUOKE_HOME` to a temporary directory and never touch a memory already on your machine.

As a library:

```python
from luoke_mem import Memory

mem = Memory("/tmp/demo-mem.db", tool="luoke-mail")      # no path: ~/.luoke/mem.db
mem.state("interest", "本地大模型")                       # said by the user
rid = mem.record("user", "activity", "打开过：本地大模型周报", topic="mail-read")
nid = mem.propose("interest", "AI 编程工具", cites=[rid])   # a guess, to be confirmed
mem.confirm(nid)
print(mem.context(["fact", "interest", "dislike"]))       # a passage for a model
```

From the command line:

```bash
python3 -m luoke_mem --path /tmp/demo-mem.db ui --open                  # open the memory page
python3 -m luoke_mem --path /tmp/demo-mem.db sources                    # how much each app wrote
python3 -m luoke_mem --path /tmp/demo-mem.db forget-tool luoke-mail --dry-run
echo '{"speaker":"user","kind":"statement","text":"…","topic":"demo"}' | python3 -m luoke_mem --path /tmp/demo-mem.db record --tool demo
```

`record` is for apps not written in Python: one JSON record per line on standard input, one `{"id"}` or `{"error"}` line back per line on standard output.

Evaluation: `bench/run_typing.py` and `bench/run_topics.py` score the learning tasks on made-up typing and mailboxes through Luoke; they need the socket of a Luoke instance (`--hub`).

## Data and privacy

- **On this Mac only**: one SQLite file, `~/.luoke/mem.db` (directory 700, file 600, readable by your account only; `LUOKE_HOME` moves it).
- **No network**: the memory talks only to this machine — learning uses a model through Luoke's local socket; the memory page listens on 127.0.0.1 only, carries a token in its address, and loads nothing from outside.
- **Every item has evidence**: each understanding cites the records it rests on, so you can see which sentence or which mail it came from; three confidence levels (a guess, fairly sure, sure), and apps use only fairly sure and above.
- **Correct and delete**: a revision keeps the old version; deleting takes the evidence with it, along with any understanding that rested only on what was deleted, and wipes it from the file too (`secure_delete`). When you uninstall an app, you choose whether what it wrote into the memory is forgotten with it.
- **Only the credential is masked**: passwords, one-time codes, keys, full card and ID numbers are replaced with a placeholder before they're written. Health, legal disputes, religion and politics are marked; what is drawn from them counts only when you said it or confirmed it.

Design and principles: [DESIGN.md](DESIGN.md) (in Chinese).

## Documents (in Chinese)

- [DESIGN.md](DESIGN.md): principles, data, interface
- [CHANGELOG.md](CHANGELOG.md): what changed in each version
- [bench/](bench/): evaluation sets for the learning tasks and how to run them
- [docs/community/luoke-mem.md](docs/community/luoke-mem.md): facts and numbers for the website
- Decisions that span components: top-level [docs/adr/](https://github.com/luokelabs/luoke/tree/main/docs/adr)

## License, security, contributing

- License: [MIT](LICENSE), © 2026 螺壳实验室 Luoke Labs
- Security issues: see [SECURITY.md](SECURITY.md)
- Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md)
