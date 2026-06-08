# Pull the LATEST pyjama-fastapi from git (branch `main`) and re-pin uv.lock.
#
# A normal `uv sync --frozen` installs the commit SHA locked in uv.lock; it never
# advances to newer commits. This script re-resolves only `pyjama` to the current
# branch HEAD, rewrites the SHA in uv.lock, and reinstalls it. All other deps stay
# pinned. --refresh-package busts uv's git cache in case it holds a stale HEAD.
$ErrorActionPreference = "Stop"

Write-Host "Pulling latest pyjama-fastapi from git (branch main)..."
# --native-tls uses the OS trust store (required behind corporate CAs, e.g. Baxter
# network, where pypi.org otherwise fails with "invalid peer certificate").
uv sync --upgrade-package pyjama --refresh-package pyjama --native-tls
if ($LASTEXITCODE -ne 0) { throw "uv sync failed (exit $LASTEXITCODE)" }

Write-Host "pyjama updated. New pin in uv.lock:"
Select-String -Path uv.lock -Pattern 'github.com/dsobczynski88/pyjama-fastapi' | Select-Object -Last 1
