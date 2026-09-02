#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
allowed_uid="$(id -u)"
service_source="$repo_dir/systemd/codex-sudo.service"
install_dir="/opt/codex-sudo"
service_target="/etc/systemd/system/codex-sudo.service"

if [[ ! -f "$repo_dir/sudo_daemon.py" || ! -f "$service_source" ]]; then
  echo "Run this script from the checked-out agent project." >&2
  exit 1
fi

sed "s/__ALLOWED_UID__/$allowed_uid/g" "$service_source" | sudo tee "$service_target" >/dev/null
sudo install -d -o root -g root -m 0755 "$install_dir"
sudo install -o root -g root -m 0755 "$repo_dir/sudo_daemon.py" "$install_dir/sudo_daemon.py"
sudo systemctl daemon-reload
sudo systemctl enable --now codex-sudo.service
sudo systemctl --no-pager --full status codex-sudo.service
