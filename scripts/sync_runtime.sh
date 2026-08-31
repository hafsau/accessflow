#!/usr/bin/env bash
# Rebuild the AgentCore deployment bundle from the source tree.
# The bundle is a COPY, so run this after any change to backend/app or policies/.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

rm -rf runtime/backend runtime/policies
mkdir -p runtime/backend

cp backend/__init__.py runtime/backend/__init__.py
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' backend/app/ runtime/backend/app/
rsync -a --exclude '__pycache__' --exclude '.DS_Store' policies/ runtime/policies/

echo "bundle rebuilt:"
find runtime -type f -not -path '*/__pycache__/*' | sort
