# Runbook — la pasada NWP del día no llega

Síntoma: `nwp-daily` falla con "la pasada 00Z de AAAA-MM-DD no está completa en dynamical a las 12:00 UTC", o `status` muestra `failed` en `nwp-daily`. Cierre de Predico: 13:00 UTC (verano) / 14:00 UTC (invierno). Referencia: dynamical termina a las 08:15–08:25 UTC.

1. **Estado del job y causa**
   `uv run python -m predico50hz.orchestration status` y `journalctl --user -u predico-nwp-daily.service -e`. Si el fallo es de otra fase (`ingest`, `validate`, `gold`) no es este runbook: la hora de disponibilidad ya consta en `data/ops/nwp_availability`; arregla la causa y relanza el paso 4.
2. **¿Publicó dynamical?** Mira los últimos snapshots:
   ```
   uv run python - <<'PY'
   import itertools, icechunk
   from predico50hz.ingest.dynamical.source import resolve_href
   repo = icechunk.Repository.open(icechunk.http_storage(resolve_href()))
   for s in itertools.islice(repo.ancestry(branch="main"), 6):
       print(s.written_at, s.message)
   PY
   ```
   - Hay `Expand dataset` de hoy pero no `Update at ...`: la ráfaga está en curso o se cortó. Espera y vuelve a mirar.
   - No hay `Expand dataset` de hoy: dynamical no ha publicado. Comprueba que el STAC responde (`https://stac.dynamical.org/catalog.json`) y que no es un problema de red local.
   - Hay `Update at ...` pero el job dice que no está completa: el mensaje o el chunk sonda cambiaron; comprueba a mano que `wind_u_100m` de hoy no es NaN y avisa (el criterio está en `orchestration/availability.py`).
3. **¿Existe el respaldo `enfo`?** `curl -sI https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/AAAAMMDD/00z/ifs/0p25/enfo/AAAAMMDD000000-54h-enfo-ef.index` (200 con `Last-Modified` = ya está). Hoy el respaldo GRIB (F1.7) **no está implementado**: solo sirve para saber si el problema es de dynamical o general.
4. **Cuando la pasada ya está, relanzar**: `systemctl --user start predico-nwp-daily.service` (o `uv run python -m predico50hz.orchestration nwp-daily --day AAAA-MM-DD`). Es idempotente (y recupera de paso las pasadas de los últimos 14 días que falten): detecta la pasada, registra su hora real (`available_at`), ingiere, valida y reconstruye las variables del día. Si ya pasó el límite de espera, un único sondeo basta; la pasada se marca `catch_up=true` (detección tardía, sin valor para el margen operativo).
5. **Si no llega antes del cierre**: no hay pronóstico con la pasada de hoy. Opciones (decide el responsable, no están automatizadas): enviar un pronóstico simple de seguridad (issue «Envío temprano de seguridad») o usar la pasada del día anterior. Anota el día: rompe la racha de G0 y debe quedar como hueco con su causa (criterio de F1: cobertura ≥ 98 %).
6. **Cambiar la espera**: `PREDICO_POLL_DEADLINE_UTC=13:00` y `PREDICO_POLL_INTERVAL_S=120` en `~/.config/predico/ops.env` (el timer de systemd no cambia; `TimeoutStartSec` del servicio es 5 h).
