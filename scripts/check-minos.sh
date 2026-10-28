#!/bin/sh
# Every program in 螺壳记忆.app must run on the macOS the app says it needs (LSMinimumSystemVersion). A launcher
# built for the packaging machine's own macOS (minos 27.0) is refused on 26 by LaunchServices ("You can't use
# this version of the application"; luoke-core 86b1c89, 2026-10-15) -- invisible on the machine that built it.
#   scripts/check-minos.sh <螺壳记忆.app>
set -eu
app=$1
want=$(/usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$app/Contents/Info.plist")
bad=0
for bin in "$app"/Contents/MacOS/* "$app"/Contents/Resources/python/bin/python3.[0-9]*; do
  [ -f "$bin" ] || continue
  case $(file -b "$bin") in Mach-O*) ;; *) continue ;; esac     # programs only, not python3.12-config
  minos=$(otool -l "$bin" | awk '/LC_BUILD_VERSION/ {f = 1} f && $1 == "minos" {print $2; exit}')
  if [ -z "$minos" ] || ! /usr/bin/python3 -c 'import sys; v = lambda s: tuple(int(x) for x in s.split(".")); sys.exit(v(sys.argv[1]) > v(sys.argv[2]))' "$minos" "$want"; then
    echo "$(basename "$bin") 要 macOS ${minos:-?}，比这个 app 写的最低版本 $want 高，老一些的系统打不开它。用修好的 luoke-core 打包脚本重打。" >&2
    bad=1
  else
    echo "检查：$(basename "$bin") 最低 macOS $minos（app 写的是 $want）"
  fi
done
exit $bad
