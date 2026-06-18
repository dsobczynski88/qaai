# Sync the vendored pyjama package (libs/pyjama) with its standalone repo
# (https://github.com/dsobczynski88/pyjama-fastapi.git, branch main) via git subtree.
#
#   scripts/pyjama_subtree.ps1 pull   # pull upstream pyjama-fastapi changes into libs/pyjama
#   scripts/pyjama_subtree.ps1 push   # push local libs/pyjama edits back to pyjama-fastapi
#
# Replaces the old update_pyjama.ps1 (which re-pinned a git SHA). pyjama is now an editable
# path dependency vendored at libs/pyjama, so day-to-day edits take effect with no reinstall;
# this script is only for syncing with the standalone repo.
param(
  [Parameter(Mandatory)][ValidateSet("pull", "push")][string]$Action
)
$ErrorActionPreference = "Stop"

$Prefix = "libs/pyjama"
$Remote = "pyjama-upstream"
$Url    = "https://github.com/dsobczynski88/pyjama-fastapi.git"
$Branch = "main"

# git subtree is a contrib command; confirm it's callable before doing anything.
git subtree --help *> $null
if ($LASTEXITCODE -ne 0) { throw "git subtree is not available on this Git install." }

# pull rewrites the working tree and aborts if it's dirty — fail early with a clear message.
if ($Action -eq "pull") {
  if (git status --porcelain) {
    throw "Working tree is dirty. Commit or stash changes before pulling."
  }
}

# Ensure the upstream remote exists (idempotent — a fresh clone won't have it yet).
if (-not ((git remote) -contains $Remote)) {
  git remote add $Remote $Url
  if ($LASTEXITCODE -ne 0) { throw "git remote add $Remote failed." }
}

git fetch $Remote
if ($LASTEXITCODE -ne 0) { throw "git fetch $Remote failed." }

switch ($Action) {
  # --squash matches how the subtree was added; keep it consistent on every pull.
  "pull" { git subtree pull --prefix=$Prefix $Remote $Branch --squash }
  "push" { git subtree push --prefix=$Prefix $Remote $Branch }
}
if ($LASTEXITCODE -ne 0) { throw "git subtree $Action failed (exit $LASTEXITCODE)." }

Write-Host "pyjama subtree $Action complete (prefix $Prefix, remote $Remote, branch $Branch)."
