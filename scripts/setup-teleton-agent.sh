#!/usr/bin/env zsh
# Starts Teleton's first-login wizard with a Telegram-only SOCKS5 proxy.
set -euo pipefail

project_root="/home/mabster/progs/agent"
export TELETON_HOME="/home/mabster/.teleton-agent"
export TELETON_TG_PROXY_TYPE="socks5"
export TELETON_TG_PROXY_HOST="127.0.0.1"
export TELETON_TG_PROXY_PORT="2060"

exec node "$project_root/third_party/teleton-agent/dist/cli/index.js" setup --ui
