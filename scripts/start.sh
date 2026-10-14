#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
uv sync --frozen
exec uv run --no-sync campus all "$@"
