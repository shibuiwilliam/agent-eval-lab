#!/bin/sh
# CI と同じ検査（テストを走らせる）
set -eu
cd "$(dirname "$0")/.."
exec python -m pytest -q "$@"
