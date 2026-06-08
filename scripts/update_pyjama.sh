#!/bin/bash
# Pull the LATEST pyjama-fastapi from git (branch `main`) and re-pin uv.lock.
#
# A normal `uv sync --frozen` installs the commit SHA locked in uv.lock; it never
# advances to newer commits. This script re-resolves only `pyjama` to the current
# branch HEAD, rewrites the SHA in uv.lock, and reinstalls it. All other deps stay
# pinned. --refresh-package busts uv's git cache in case it holds a stale HEAD.
set -euo pipefail

echo "🔄 Pulling latest pyjama-fastapi from git (branch main)..."
# --native-tls uses the OS trust store (required behind corporate CAs, e.g. Baxter
# network, where pypi.org otherwise fails with "invalid peer certificate").
uv sync --upgrade-package pyjama --refresh-package pyjama --native-tls

echo "✓ pyjama updated. New pin in uv.lock:"
grep -A2 'name = "pyjama"' uv.lock | grep 'source'
