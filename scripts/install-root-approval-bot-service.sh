#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
run_as_user="$(id -un)"
run_as_group="$(id -gn)"
home_dir="$HOME"
venv_python="$repo_dir/.venv/bin/python"
unit_source="$repo_dir/systemd/codex-root-approval-bot.service"
unit_target="/etc/systemd/system/codex-root-approval-bot.service"
path_value="/home/$run_as_user/.local/bin:/home/$run_as_user/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [[ ! -x "$venv_python" || ! -f "$repo_dir/root_approval_bot.py" || ! -f "$unit_source" ]]; then
  echo "Root approval bot files or Python environment are missing." >&2
  exit 1
fi

if ! grep -qE '^ROOT_APPROVAL_BOT_TOKEN=.+$' "$repo_dir/.env"; then
  echo "Set ROOT_APPROVAL_BOT_TOKEN in $repo_dir/.env first." >&2
  exit 1
fi

sed \
  -e "s|__RUN_AS_USER__|$run_as_user|g" \
  -e "s|__RUN_AS_GROUP__|$run_as_group|g" \
  -e "s|__PROJECT_DIR__|$repo_dir|g" \
  -e "s|__HOME_DIR__|$home_dir|g" \
  -e "s|__VENV_PYTHON__|$venv_python|g" \
  -e "s|__PATH__|$path_value|g" \
  "$unit_source" | sudo tee "$unit_target" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable --now codex-root-approval-bot.service
sudo systemctl --no-pager --full status codex-root-approval-bot.service
