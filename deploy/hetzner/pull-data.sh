#!/usr/bin/env bash
# Copia los datos archivados en la VM hacia este equipo (issue #6). Solo ida VM -> local.
# Es un archivo acumulativo: NO usa --delete, así que lo que se borre en la VM se conserva aquí.
#
#   deploy/hetzner/pull-data.sh --dry-run             # muestra qué copiaría (rsync -n, solo lista)
#   deploy/hetzner/pull-data.sh --with-nwp            # data/ y nwp/ (bronce NWP)
#
# Opciones:
#   --host HOST        alias SSH de la VM (defecto antuzanos)
#   --remote-dir DIR   raíz en la VM, relativa al home remoto (defecto predico)
#   --dest DIR         destino local (defecto <repo>/data/remote/hetzner-g0)
#   --with-nwp         incluye nwp/ (por defecto solo data/)
#   --dry-run          rsync -n: no escribe nada ni deja last_pull.json
# Solo se copian data/ (y nwp/ con --with-nwp): nunca .env ni nada más de la VM.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST=antuzanos
REMOTE_DIR=predico
DEST="$REPO/data/remote/hetzner-g0"
WITH_NWP=0
DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --with-nwp) WITH_NWP=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "opción desconocida: $1" >&2; exit 2 ;;
  esac
done

# Bajo systemd no se hereda SSH_AUTH_SOCK: se toma del fichero que deja el ssh-agent de la sesión
# (solo la ruta del socket; el fichero no se ejecuta). Sin agente con la clave cargada, SSH falla.
AGENT_ENV="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/ssh-agent.env"
if [[ -z "${SSH_AUTH_SOCK:-}" && -r "$AGENT_ENV" ]]; then
  sock="$(sed -n 's/^SSH_AUTH_SOCK=\([^;]*\);.*/\1/p' "$AGENT_ENV" | head -n1)"
  [[ -S "$sock" ]] && export SSH_AUTH_SOCK="$sock"
fi

SSH_CMD="ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=30 -o ServerAliveCountMax=4"
RSYNC_OPTS=(-rltz --partial --stats -e "$SSH_CMD"
  --exclude 'ops/locks/' --exclude 'ops/export_*.tar.gz' --exclude '*.tmp' --exclude '__pycache__')
[[ $DRY == 1 ]] && RSYNC_OPTS+=(--dry-run --itemize-changes)

SECTIONS=(data)
[[ $WITH_NWP == 1 ]] && SECTIONS+=(nwp)

[[ $DRY == 1 ]] || mkdir -p "$DEST"
LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

status=ok
rc=0
for s in "${SECTIONS[@]}"; do
  echo "== $HOST:$REMOTE_DIR/$s/ -> $DEST/$s/"
  [[ $DRY == 1 ]] || mkdir -p "$DEST/$s"
  src_rc=0
  rsync "${RSYNC_OPTS[@]}" "$HOST:$REMOTE_DIR/$s/" "$DEST/$s/" > >(tee -a "$LOG") 2>&1 || src_rc=$?
  wait
  if [[ $src_rc != 0 ]]; then rc=$src_rc; status=error; fi
done

[[ $DRY == 1 ]] && exit "$rc"

# Suma de "Total transferred file size: N bytes" de cada rsync (vacío si no se puede leer).
BYTES="$(awk -F': ' '/Total transferred file size/ {gsub(/[^0-9]/, "", $2); t += $2} END {print t + 0}' "$LOG")"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf '{"pulled_at": "%s", "host": "%s", "remote_dir": "%s", "with_nwp": %s, "bytes_transferred": %s, "status": "%s"}\n' \
  "$NOW" "$HOST" "$REMOTE_DIR" "$([[ $WITH_NWP == 1 ]] && echo true || echo false)" "$BYTES" "$status" \
  > "$DEST/last_pull.json"
echo "== $status ($BYTES bytes) -> $DEST/last_pull.json"
exit "$rc"
