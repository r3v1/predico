# Etapa 1 — Cadena GRIB (IFS HRES, ECMWF Open Data)

**16 oct 2026 · Estado: cadena validada; desde PLAN v1.2 es la base del respaldo (F1.7), no la fuente principal.**

El agente que la construyó se detuvo antes de escribir este informe. Los datos salen de `data/stage1/metrics.json` y `data/stage1/week.log`; lo no medido se indica.

## Cadena

`paths` (ruta por fecha, 0p4-beta / ifs/0p25, oper/scda) → `download` (`.index` + rangos HTTP fusionados, concurrencia 2, backoff ante 503) → `crop` (eccodes, recorte 49,5–56° N × 8,5–16° E, sin guardar globales) → `bronze` (Icechunk en `/mnt/data/nwp/IFS/icechunk`, un commit por pasada) → `extract` (bilineal en `config/centroids_provisional.yaml`, Parquet en `data/silver/ifs_points/`).

Código en `src/predico50hz/ingest/ifs/`; CLI `uv run python -m predico50hz.ingest.ifs`; 16 tests unitarios pasan.

## Métricas (00Z, 13 pasos, 15 campos, concurrencia 2)

| Pasada | MB | Descarga (s) | MB/s | 503 | Decodif. + recorte (s) | Bronce (s) |
|---|---|---|---|---|---|---|
| 2026-10-01 | 172,6 | 40,6 | 4,2 | 15 | 4,4 | 3,6 |
| 2026-10-02 | 174,3 | 30,3 | 5,7 | 9 | 4,6 | 2,8 |
| 2026-10-03 | 172,5 | 32,1 | 5,4 | 7 | 4,9 | 2,5 |
| 2026-10-04 | 167,9 | 22,9 | 7,3 | 5 | 4,2 | 2,2 |
| 2026-10-05 | 168,9 | 18,3 | 9,2 | 3 | 4,2 | 2,2 |
| 2026-10-06 | 173,6 | 23,7 | 7,3 | 4 | 4,3 | 2,5 |
| 2026-10-07 | 174,8 | 16,7 | 10,5 | 0 | 4,2 | 2,5 |
| **Media** | **172** | **26,4** | **6,5** | 6 | 4,4 | 2,6 |

Plata: ~0,1 s por pasada. Disco tras la semana: bronce 4,4 MB, plata 204 KB.

**Independencia de la resolución:** una pasada de 2023-06-01 (0,4°; faltan `100u/100v/10fg`, registrados sin fallar) y otra de 2024-06-01 (0,25°; falta `10fg`) pasaron la cadena completa con un paso cada una.

**Idempotencia (2026-10-03):** hash del array bronce y del contenido de plata idénticos antes y después de relanzar. El tamaño en disco del bronce crece (2,78 → 3,16 MB) porque Icechunk conserva el snapshot anterior; es esperado.

## Extrapolación (no medida, calculada con la media de arriba)

~34 s por pasada en serie (descarga + decodificación + bronce). Desde 2024-03-06 (primer día con 100 m) hasta hoy son ~955 pasadas → **~9 h** con concurrencia 2. Muy por debajo de la puerta de 3 días. Los 503 bajan a lo largo de la semana; no se ha probado una concurrencia mayor.

## Pendiente para el respaldo (F1.7)

- Pasar de `oper` (HRES) a `enfo` (ENS) para que el respaldo sea la misma fuente que dynamical.
- Medir la hora de publicación de `enfo` 00Z.
