#!/usr/bin/env bash
set -euo pipefail

sudo systemctl disable --now codex-sudo.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/codex-sudo.service
sudo rm -rf /opt/codex-sudo
sudo systemctl daemon-reload
