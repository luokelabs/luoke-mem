#!/bin/sh
# 用 luoke-core 的 Python 配件打包脚本把 luoke_mem 打成「螺壳记忆.app」（开发版，ad-hoc 签名），放在 build/app.noindex/。
# 清单是 packaging/accessory.json（luoke-core V1 §8）；正式的签名公证由发布脚本做，不在这里。
#   scripts/package-app.sh            打包并检查（导入、签名、最低 macOS）
#   scripts/package-app.sh --install  再拷到 ~/Applications 并注册：会替换这台机器上在用的螺壳记忆.app，
#                                     只在开发机上用
#   LUOKE_CORE=<luoke-core 仓库>       默认是并排的 ../luoke-core
set -eu
repo=$(cd "$(dirname "$0")/.." && pwd)
core=${LUOKE_CORE:-$repo/../luoke-core}
# build/app.noindex: Spotlight does not look into *.noindex, so LaunchServices does not take a dev build in the
# repo for the 螺壳记忆 in use (a newer version there would win the bundle id).
out="$repo/build/app.noindex"
/usr/bin/python3 "$core/tools/package_python_app.py" --manifest "$repo/packaging/accessory.json" \
  --package "$repo/luoke_mem" --out "$out" --check "$@"
sh "$repo/scripts/check-minos.sh" "$out/螺壳记忆.app"     # opens on the oldest macOS it says it supports
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -u "$out/螺壳记忆.app" 2>/dev/null || true
