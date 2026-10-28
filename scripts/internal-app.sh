#!/bin/sh
# Put a signed, notarized 螺壳记忆.app where luoke-core takes it for the daily internal build:
# build/release.noindex/internal/螺壳记忆.app, with .luoke-commit beside it -- the full hash of the last commit that changed what goes into the app (luoke_mem/, packaging/). Later commits that only
# touch tests or documents leave the app the same, so luoke-core checks that nothing under those two paths changed
# between .luoke-commit and the commit it pinned.
#   scripts/internal-app.sh <螺壳记忆.app>      e.g. build/release.noindex/0.15.4/螺壳记忆.app
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
src=$(cd "$1" && pwd)
cd "$repo"
git diff --quiet HEAD -- luoke_mem packaging || { echo "luoke_mem/ 或 packaging/ 有没提交的改动：先提交，再打包" >&2; exit 1; }
built=$(git log -1 --format=%H -- luoke_mem packaging)
want=$(git show "$built:packaging/accessory.json" | /usr/bin/python3 -c "import json, sys; print(json.load(sys.stdin)['version'])")
have=$(/usr/bin/python3 -c "import json, sys; print(json.load(open(sys.argv[1]))['version'])" "$src/Contents/Resources/luoke-accessory.json")
[ "$have" = "$want" ] || { echo "这份 app 是 $have，可 $built 是 $want：不是同一份" >&2; exit 1; }
spctl --assess --type execute "$src" || { echo "没过 Gatekeeper（没公证？）：$src" >&2; exit 1; }
xcrun stapler validate "$src" >/dev/null || { echo "没有 staple：$src" >&2; exit 1; }
sh "$repo/scripts/check-minos.sh" "$src" >/dev/null
out="$repo/build/release.noindex/internal"
rm -rf "$out"
mkdir -p "$out"
ditto "$src" "$out/螺壳记忆.app"
printf '%s\n' "$built" > "$out/.luoke-commit"
# last, after the checks that register what they look at: only the installed copy answers to the bundle id
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -u "$out/螺壳记忆.app" 2>/dev/null || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -u "$src" 2>/dev/null || true
echo "内部版用的螺壳记忆 $have：$out/螺壳记忆.app（.luoke-commit $built）"
