#!/bin/sh
# 把一份打好的「螺壳记忆.app」装进 ~/Applications，替换这台机器上在用的那份；能回滚到上一份。
# 开发机上用：发布版的记忆打在螺壳里，由螺壳装和更新。
#   scripts/install-app.sh <螺壳记忆.app>   装这一份（要过 Gatekeeper：签名公证版）；原来那份挪到 build/previous.noindex/
#   scripts/install-app.sh --adhoc <app>   明说要装开发版（ad-hoc 签名，过不了 Gatekeeper）
#   scripts/install-app.sh --rollback       把 build/previous.noindex/ 里的那份装回去（装过的这份挪到 build/rolled-back.noindex/）
# 装之前确认没有进程从那份 app 里跑着（界面服务 10 分钟没人用会自己退出），有就停下、不杀进程。
# 上一份放在仓库的 build/*.noindex 里，不放 ~/Applications：那里两份同一个 bundle id，系统可能打开旧的；
# .noindex 让 Spotlight 不进去，LaunchServices 也就不会自己再登记它。
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
dst="$HOME/Applications/螺壳记忆.app"
prev="$repo/build/previous.noindex/螺壳记忆.app"
lsregister=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister

running() {   # processes running the installed app's own Python (read only: nothing is stopped here). Not by
              # matching ps output: ps escapes the Chinese name of the path in some locales, and then nothing matches.
  [ -d "$dst/Contents/Resources/python/bin" ] || return 0
  lsof -t +d "$dst/Contents/Resources/python/bin" 2>/dev/null | sort -u | while read -r pid; do
    echo "$pid $(ps -o command= -p "$pid" 2>/dev/null)"
  done
}

adhoc=0
if [ "${1:-}" = "--adhoc" ]; then adhoc=1; shift; fi
if [ "${1:-}" = "--rollback" ]; then
  [ -d "$prev" ] || { echo "没有上一份可以回滚：$prev" >&2; exit 1; }
  new="$prev"
  keep="$repo/build/rolled-back.noindex/螺壳记忆.app"
else
  [ $# -eq 1 ] && [ -d "$1" ] || { echo "用法：$0 <螺壳记忆.app> | --rollback" >&2; exit 2; }
  new=$(cd "$1" && pwd)
  keep="$prev"
fi
id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$new/Contents/Info.plist")
[ "$id" = "ai.luoke.memory" ] || { echo "不是螺壳记忆：bundle id 是 $id" >&2; exit 1; }
codesign --verify --deep --strict "$new" || { echo "要装的这份签名不对：$new" >&2; exit 1; }
# what goes out must open on every macOS it says it supports; a rollback brings back what already ran here
[ "${1:-}" = "--rollback" ] || sh "$(dirname "$0")/check-minos.sh" "$new" >/dev/null || exit 1
# a release step that failed half way (signed but not notarized, or still ad-hoc) must not end up in place of a
# signed one by accident: only what Gatekeeper accepts, unless asked for a development build (2026-10-14 incident)
if [ "${1:-}" != "--rollback" ] && [ $adhoc -eq 0 ] && ! spctl --assess --type execute "$new" 2>/dev/null; then
  echo "这份没过 Gatekeeper（没公证，或是开发版）：$new。要装开发版就加 --adhoc。" >&2
  exit 1
fi

if [ -n "$(running)" ]; then
  echo "螺壳记忆还在跑，先关掉记忆页面（10 分钟没人用会自己退出），再装：" >&2
  running >&2
  exit 1
fi

staged="$repo/build/.staged-螺壳记忆.app"
rm -rf "$staged"
ditto "$new" "$staged"                       # copy first: the swap below is two renames
if [ -d "$dst" ]; then
  rm -rf "$keep"
  mkdir -p "$(dirname "$keep")"
  mv "$dst" "$keep"
fi
mv "$staged" "$dst"
[ "${1:-}" = "--rollback" ] && rm -rf "$prev"
"$lsregister" -f "$dst"
[ -d "$keep" ] && "$lsregister" -u "$keep" 2>/dev/null || true   # only the installed one answers to the bundle id
version=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$dst/Contents/Info.plist")
echo "已装 螺壳记忆 $version：$dst"
if [ "${1:-}" = "--rollback" ]; then
  echo "回滚前装着的那份在：$keep"
elif [ -d "$keep" ]; then
  echo "原来那份在：$keep（回滚：$0 --rollback）"
fi
if spctl --assess --type execute "$dst" 2>/dev/null; then
  echo "Gatekeeper：通过（签名公证版）"
else
  echo "Gatekeeper：没通过（开发版 ad-hoc 签名，只在本机能跑）"
fi
# last: the checks above (spctl, as stapler validate does) register what they look at, so whatever it was
# installed from and the copy kept for rollback are let go of again -- only the installed one answers to the id
for other in "$new" "$keep"; do
  [ -d "$other" ] && [ "$other" != "$dst" ] && "$lsregister" -u "$other" 2>/dev/null || true
done
