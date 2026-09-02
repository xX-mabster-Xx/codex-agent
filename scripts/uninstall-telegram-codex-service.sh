#!/usr/bin/env bash
set -euo pipefail

sudo systemctl disable --now telegram-codex.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/telegram-codex.service
sudo systemctl daemon-reload
