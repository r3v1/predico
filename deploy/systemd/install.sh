#!/usr/bin/env bash
# Instala (o actualiza) los timers de systemd de usuario de la operación diaria. Idempotente.
# Uso: deploy/systemd/install.sh [--uninstall]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
TIMERS=(predico-nwp-daily.timer predico-entsoe-daily.timer predico-entsoe-hourly.timer
        predico-vm-pull.timer)
UNITS=(predico-nwp-daily.service predico-entsoe-daily.service predico-entsoe-hourly.service predico-vm-pull.service
       predico-failure@.service "${TIMERS[@]}")

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable --now "${TIMERS[@]}" 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "$DEST/$u"; done
  systemctl --user daemon-reload
  echo "desinstalado (los datos y runs.jsonl no se tocan)"
  exit 0
fi

[[ -x "$REPO/.venv/bin/python" ]] || { echo "falta $REPO/.venv (ejecuta 'uv sync')" >&2; exit 1; }
mkdir -p "$DEST"
for u in "${UNITS[@]}"; do
  sed "s|@REPO@|$REPO|g" "$HERE/$u" > "$DEST/$u.tmp"
  mv "$DEST/$u.tmp" "$DEST/$u"
done
systemctl --user daemon-reload
systemctl --user enable --now "${TIMERS[@]}"
systemctl --user list-timers 'predico-*' --all --no-pager
