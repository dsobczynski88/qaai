#!/usr/bin/env bash
# Sync the vendored pyjama package (libs/pyjama) with its standalone repo
# (https://github.com/dsobczynski88/pyjama-fastapi.git, branch main) via git subtree.
#
#   scripts/pyjama_subtree.sh pull   # pull upstream pyjama-fastapi changes into libs/pyjama
#   scripts/pyjama_subtree.sh push   # push local libs/pyjama edits back to pyjama-fastapi
#
# Replaces the old update_pyjama.sh (which re-pinned a git SHA). pyjama is now an editable
# path dependency vendored at libs/pyjama, so day-to-day edits take effect with no reinstall;
# this script is only for syncing with the standalone repo.
set -euo pipefail

action="${1:?usage: pyjama_subtree.sh <pull|push>}"

prefix="libs/pyjama"
remote="pyjama-upstream"
url="https://github.com/dsobczynski88/pyjama-fastapi.git"
branch="main"

case "$action" in
  pull|push) ;;
  *) echo "unknown action: $action (expected pull|push)" >&2; exit 2 ;;
esac

# git subtree is a contrib command; confirm it's callable before doing anything.
git subtree --help >/dev/null 2>&1 || { echo "git subtree is not available on this Git install." >&2; exit 1; }

# pull rewrites the working tree and aborts if it's dirty — fail early with a clear message.
if [ "$action" = "pull" ] && [ -n "$(git status --porcelain)" ]; then
  echo "Working tree is dirty. Commit or stash changes before pulling." >&2
  exit 1
fi

# Ensure the upstream remote exists (idempotent — a fresh clone won't have it yet).
if ! git remote | grep -qx "$remote"; then
  git remote add "$remote" "$url"
fi

git fetch "$remote"

case "$action" in
  # --squash matches how the subtree was added; keep it consistent on every pull.
  pull) git subtree pull --prefix="$prefix" "$remote" "$branch" --squash ;;
  push) git subtree push --prefix="$prefix" "$remote" "$branch" ;;
esac

echo "pyjama subtree $action complete (prefix $prefix, remote $remote, branch $branch)."
