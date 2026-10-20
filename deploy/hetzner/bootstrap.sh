#!/usr/bin/env bash
# Prepara una VM vacía (p. ej. Hetzner) para la comprobación de G0 y la arranca con Docker.
# Se ejecuta DESDE ESTE EQUIPO; copia lo mínimo hacia la VM (nunca de vuelta).
#
#   deploy/hetzner/bootstrap.sh --dry-run root@IP            # solo muestra qué haría (no conecta)
#   deploy/hetzner/bootstrap.sh root@IP --env-file .env      # copia, construye y arranca
#
# Opciones:
#   --remote-dir DIR   raíz en la VM (defecto /srv/predico)
#   --env-file FILE    .env con ENTSOE_API_KEY; se copia con permisos 600 (si no, créalo tú)
#   --no-entsoe        no copia la plata de ENTSO-E (hay que reconstruirla: ver doc, ~40 min)
#   --dry-run          imprime los comandos en vez de ejecutarlos
# Requisitos en la VM: docker con el plugin compose, rsync y acceso SSH por clave.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REMOTE_DIR=/srv/predico
ENV_FILE=""
WITH_ENTSOE=1
DRY=0
TARGET=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --remote-dir) REMOTE_DIR="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --no-entsoe) WITH_ENTSOE=0; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    -*) echo "opción desconocida: $1" >&2; exit 2 ;;
    *) TARGET="$1"; shift ;;
  esac
done
[[ -n "$TARGET" ]] || { echo "falta el destino (usuario@host)" >&2; exit 2; }

run() {
  if [[ $DRY == 1 ]]; then printf '[dry-run]'; printf ' %q' "$@"; printf '\n'; else "$@"; fi
}

APP="$REMOTE_DIR/app"
DATA="$REMOTE_DIR/data"
NWP="$REMOTE_DIR/nwp/IFS-ENS"

echo "== 1. directorios en la VM"
run ssh "$TARGET" "mkdir -p '$APP' '$DATA/silver' '$NWP' && chmod 700 '$REMOTE_DIR' && chown -R 1000:1000 '$DATA' '$NWP'"

echo "== 2. código y configuración (sin data/, .env ni .git)"
run rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'data' --exclude '.env' --exclude '.env.*' \
  --exclude 'notebooks' --exclude 'report' --exclude 'tests' --exclude '__pycache__' \
  "$REPO/src" "$REPO/config" "$REPO/docker" "$REPO/deploy" "$REPO/pyproject.toml" \
  "$REPO/uv.lock" "$REPO/README.md" "$REPO/.dockerignore" "$TARGET:$APP/"

echo "== 3. plata de MaStR (necesaria para las variables)"
run rsync -az "$REPO/data/silver/mastr" "$TARGET:$DATA/silver/"

if [[ $WITH_ENTSOE == 1 ]]; then
  echo "== 4. plata de ENTSO-E (generación: necesaria para las variables)"
  run rsync -az "$REPO/data/silver/entsoe" "$TARGET:$DATA/silver/"
else
  echo "== 4. (omitida) reconstruir en la VM: docker compose run --rm predico-ops python -m predico50hz.ingest.entsoe --dataset all"
fi

if [[ -n "$ENV_FILE" ]]; then
  echo "== 5. .env con el token (permisos 600; el token no va en la imagen)"
  run rsync -z --chmod=F600 "$ENV_FILE" "$TARGET:$APP/.env"
else
  echo "== 5. (omitida) crea $APP/.env con ENTSOE_API_KEY=... y chmod 600"
fi

echo "== 6. imagen y arranque"
run ssh "$TARGET" "cd '$APP' && PREDICO_DATA_HOST='$DATA' PREDICO_NWP_HOST='$NWP' PREDICO_HOST_ID=hetzner-g0 \
  docker compose -f docker/compose.yaml up -d --build"

echo "== listo. Revisión: ssh $TARGET 'cd $APP && docker compose -f docker/compose.yaml exec predico-ops python -m predico50hz.orchestration status'"
