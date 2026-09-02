#!/usr/bin/env zsh
# Runs the dedicated Teleton installation without exposing a network port.
set -euo pipefail

project_root="/home/mabster/progs/agent"
teleton_home="${TELETON_AGENT_HOME:-/home/mabster/.teleton-agent}"
export TELETON_HOME="$teleton_home"

exec node "$project_root/third_party/teleton-agent/dist/cli/index.js" start \
  --config "$teleton_home/config.yaml" "$@"
