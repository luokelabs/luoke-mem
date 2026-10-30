#!/bin/sh
# Export the private main as one new commit on the `public` branch (top-level ADR 0017, 10-29 amendment).
# The public repo's main is the `public` branch: this only commits locally and scans; pushing it is asked first.
#   scripts/export-public.sh [ref]        ref defaults to main
set -eu
cd "$(dirname "$0")/.."
ref=${1:-main}
# paths that stay private; files git does not track (CLAUDE.md, CLAUDE.local.md) never get in anyway
EXCLUDE=""

git rev-parse -q --verify refs/heads/public >/dev/null || { echo "no public branch" >&2; exit 2; }
src=$(git rev-parse --short "$ref")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
GIT_INDEX_FILE="$tmp/index"; export GIT_INDEX_FILE
git read-tree "$ref"
for p in $EXCLUDE; do git rm -r -q --cached --ignore-unmatch -- "$p"; done
tree=$(git write-tree)
unset GIT_INDEX_FILE

if [ "$tree" = "$(git rev-parse 'public^{tree}')" ]; then echo "public already has $src's tree"; exit 0; fi

# a leak: internal names, private repos and paths, personal mail, keys (the fake key in the masking tests aside).
# Names of people and their own addresses are not written here: one extended regex a line in
# ${LUOKE_LEAK_PATTERNS:-~/.config/luoke/leak-patterns}, kept on the machine, checked against every file, this one too
leaks=$(git grep -n -i -E '主人|luoke-top|proactive-mem|[a-z]+-private\b|/Users/|gmail\.com|ghp_[A-Za-z0-9]{20}|github_pat_|BEGIN [A-Z ]*PRIVATE KEY' "$tree" -- . ':!scripts/export-public.sh' || true)
keys=$(git grep -n -E 'sk-[A-Za-z0-9]{20}' "$tree" -- . ':!tests/test_sensitive.py' || true)
own=${LUOKE_LEAK_PATTERNS:-$HOME/.config/luoke/leak-patterns}
[ -s "$own" ] || { echo "no $own: write the names to keep out, one regex a line" >&2; exit 2; }
mine=$(git grep -n -i -E -f "$own" "$tree" -- . || true)
if [ -n "$leaks$keys$mine" ]; then printf '%s\n%s\n%s\n' "$leaks" "$keys" "$mine" | sed '/^$/d' >&2; echo "leak scan failed: nothing committed" >&2; exit 1; fi

version=$(git show "$ref:luoke_mem/__init__.py" | sed -n 's/^__version__ = "\(.*\)"/\1/p')
commit=$(GIT_AUTHOR_NAME="Luoke Labs" GIT_AUTHOR_EMAIL="zhixian@luoke.ai" \
         GIT_COMMITTER_NAME="Luoke Labs" GIT_COMMITTER_EMAIL="zhixian@luoke.ai" \
         git commit-tree "$tree" -p public -m "luoke-mem $version")
git update-ref refs/heads/public "$commit" "$(git rev-parse public)"
echo "public -> $(git rev-parse --short "$commit") (luoke-mem $version, from $src); scan clean; not pushed"
git diff --stat "$commit^" "$commit" | tail -1
