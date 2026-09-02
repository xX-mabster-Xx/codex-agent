#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)"
run_as_user="$(id -un)"
run_as_group="$(id -gn)"
home_dir="$HOME"
venv_python="$repo_dir/.venv/bin/python"
unit_source="$repo_dir/systemd/telegram-codex.service"
unit_target="/etc/systemd/system/telegram-codex.service"
path_value="/home/$run_as_user/.local/bin:/home/$run_as_user/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if [[ ! -x "$venv_python" ]]; then
  echo "Python virtual environment is missing: $venv_python" >&2
  exit 1
fi
if [[ ! -f "$repo_dir/.env" ]]; then
  echo "Missing $repo_dir/.env" >&2
  exit 1
fi
if [[ ! -f "$unit_source" || ! -f "$repo_dir/scripts/wait_for_tcp.py" ]]; then
  echo "Systemd service files are missing from the project." >&2
  exit 1
fi

chmod 600 "$repo_dir/.env"

# The previous launcher used a tmux pane. Stop only that Python bot process before
# systemd starts its replacement; do not kill the user's tmux session or other panes.
if command -v tmux >/dev/null && tmux has-session -t codex-telegram 2>/dev/null; then
  old_bot_pane="$(
    tmux list-panes -a -F '#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}' \
      | awk -F '\t' '$1 ~ /^codex-telegram:/ && $2 == "python" { print $1; exit }'
  )"
  if [[ -n "$old_bot_pane" ]]; then
    echo "Stopping previous tmux bot in $old_bot_pane"
    tmux send-keys -t "$old_bot_pane" C-c
    sleep 2
  fi
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
sudo systemctl enable --now telegram-codex.service
sudo systemctl --no-pager --full status telegram-codex.service

echo
echo "Logs: journalctl -u telegram-codex.service -f"
