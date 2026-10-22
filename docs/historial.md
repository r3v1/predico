# Historial de lo hecho

Lo completado en la Fase 1 hasta el 20 oct 2026, cuando el seguimiento pasó a los [issues](https://github.com/r3v1/predico/issues) y [milestones](https://github.com/r3v1/predico/milestones) de GitHub. A partir de esa fecha, lo hecho queda en los issues cerrados. Las decisiones que hay detrás de cada punto están en los [ADR](adr/README.md). Los identificadores (F1.1, F2.2…) son los de la antigua `PLAN.md` y aparecen en docs, código y commits.

## Hitos

| Fecha | Hito |
|---|---|
| 16 oct | Plan v1.2: la fuente NWP pasa a IFS ENS de dynamical.org ([ADR 0001](adr/0001-fuente-nwp-ifs-ens-dynamical.md)) |
| 16 oct | Etapa 0 (metadatos de dynamical) y etapa 1 (7 días de extremo a extremo) completadas |
| 16 oct | Archivo diario en marcha: timers systemd en local y comprobación de 7 días en una VM de Hetzner con Docker |
| 16 oct | Etapa 2 (13 meses) descargada y validada al 100 % |
| 16 oct | Tabla oro de variables, marco de backtest y tres referencias evaluadas; informe ejecutivo v0.4 |
| 20 oct | Racha G0 en 5/7; reintentos de red y de ENTSO-E en la operación diaria; seguimiento movido a GitHub |
| 22 oct | **G0 cumplida** en `hetzner-g0`: 7/7 días (16–22 oct) con `nwp-daily` y `entsoe-daily` en `ok`; dynamical llegó entre 08:15 y 08:18 UTC, `enfo` a las 07:40–07:41 y el margen hasta el cierre osciló entre 281 y 284 min. `entsoe-hourly` tuvo 4 fallos sueltos (HTTP 400 de ENTSO-E) recuperados a la hora siguiente; no cuentan para G0 |

## NWP: ingesta de IFS ENS (F1)

- **Comprobaciones del primer día.** Variables, cobertura temporal y huecos de `init_time` en dynamical; NaN por variable y fecha (la racha existe desde el 2024-11-13). Latencia: hora UTC a la que aparece cada 00Z. → `docs/stage0-dynamical.md` §1–§3, `docs/stage0-index-check.md`
- **Etapa 0.** Metadatos de dynamical: variables, cobertura, NaN y latencia de los últimos snapshots. → `docs/stage0-dynamical.md`
- **Etapa 1.** 7 días (1–7 oct 2026) de extremo a extremo: lectura lazy → recorte → bronce Icechunk propio → plata por clústeres; MB y segundos por pasada medidos. → `docs/stage1-dynamical.md` §2–§5
- **Etapa 2.** 410/410 pasadas (sep 2025 → 15 oct 2026) en bronce y plata, validadas al 100 %. → `data/validation/ifs_ens_2025-09-01_2026-10-15.json`
- **F1.1 Lector de dynamical** (Icechunk, lazy) con variables, pasos y dominio configurables y versión del dataset fijada (exige `dataset_version` 0.1.0). → `src/predico50hz/ingest/dynamical/source.py`
- **F1.2 Recorte y bronce:** escritura en nuestro Icechunk con los valores originales recortados y los 51 miembros. → `src/predico50hz/ingest/dynamical/bronze.py`, `docs/stage1-dynamical.md` §1
- **F1.3 Plata en centroides:** control, media, desviación típica y cuantiles del ensemble en los 21 centroides de MaStR (`config/centroids.yaml`), reconstruida desde el bronce (`rebuild_silver.py`). → `data/silver/ifs_ens_clusters/`, `docs/stage1-dynamical.md` §7
- **F1.4 Relleno histórico** idempotente y reanudable (`--skip-existing`); etapas 2 y 3 completas (2024-04-01 → hoy, issue #24). → `src/predico50hz/ingest/dynamical/__main__.py`
- **F1.5 Descarga diaria** con la hora real de disponibilidad: job `nwp-daily` (07:45 UTC; sondea hasta tener la pasada completa, ingesta, plata, validación y variables de D; recupera las pasadas que falten de los últimos 14 días). Hora del commit de dynamical y `Last-Modified` del `.index` `enfo` en `data/ops/nwp_availability` (16 oct: dynamical 08:18:58, `enfo` 07:41:26 UTC, margen 281 min). → `docs/f1.5-f2.7-operations.md`
- **F1.6 Validaciones con Pandera:** rangos físicos, NaN, número de pasos, miembros y huecos en bronce y plata, con 22 tests. → `src/predico50hz/ingest/dynamical/validate.py`, `docs/f1.6-validation.md`
- **F1.7 (parcial) Cadena GRIB** de la etapa 1 original validada con 7 pasadas en HRES (`oper`); base del respaldo `enfo`. → `docs/stage1-pipeline.md`
- **Criterios cumplidos:** relanzar una fecha produce exactamente el mismo resultado (idempotencia verificada por hash, `docs/stage1-dynamical.md` §5 y `docs/stage1-pipeline.md`); la hora de disponibilidad queda registrada para cada pasada diaria, con `host` y `catch_up`; dynamical sin huecos en 928 pasadas (`data/stage0/dynamical_check.json`).

## ENTSO-E, MaStR y datos auxiliares (F2)

- **F2.1 Token de la API de ENTSO-E** en `.env` (`ENTSOE_API_KEY`), leído con `src/predico50hz/settings.py`.
- **F2.2 Generación real** B18 + B19 en `10YDE-VE-------2`, cada 15 min, desde 2015: 413 426 filas, PT15M en toda la historia, sin huecos; valores A03 arrastrados y marcados. → `docs/f2-entsoe.md` §2, `data/validation/entsoe_generation.json`
- **F2.3 Capacidad instalada** por tipo, anual. Va retrasada respecto a las altas y no sirve como denominador. → `docs/f2-entsoe.md` §4
- **F2.4 Precio day-ahead** de DE-LU (SDAC; PT60M hasta 2025-09-30 y PT15M desde 2025-10-01). → `docs/f2-entsoe.md`
- **F2.5 (parcial) Pronóstico day-ahead del operador:** serie desde 2015; su hora de publicación se mide desde el 16 oct con `entsoe-hourly` y `publication-times`. → `docs/f2-entsoe.md`, `data/ops/publication_times.json`
- **F2.6 MaStR:** export del 2026-10-15; 16 clústeres terrestres y 5 marinos; capacidad diaria por clúster. → `docs/f2.6-mastr.md`, `config/centroids.yaml`, `data/silver/mastr/`
- **F2.7 Versiones diarias de ENTSO-E:** bronce por `fetched_at`; jobs `entsoe-daily` (06:00 UTC, `--days 3`) y `entsoe-hourly` con systemd (`deploy/systemd/`) y Docker (`docker/`); registro en `data/ops/runs.jsonl`. → `docs/f1.5-f2.7-operations.md`
- **Criterios:** serie objetivo sin huecos tras la reconstrucción; capacidad de los clústeres coherente con ENTSO-E (< 5 % en tierra todos los años y en mar salvo 2018, cuando ENTSO-E omite Wikinger). → `docs/f2.6-mastr.md` §7

## Variables y control de look-ahead (F3)

- **F3.1 Uniones «as-of»:** `available_at_{nwp,capacity,calendar}` por fila; NWP = 00Z de D + 8 h 30 min (peor caso medido 08:23), capacidad y calendario a las 00:00 UTC de D. → `docs/f3-features.md` §2
- **F3.2 (parcial) Variables:** 239 en los bloques `nwp`, `capacity` y `calendar`, con buje a 119/104 m, E[v³] del ensemble y pesos de capacidad de MaStR de la fecha. El precio queda fuera hasta medir su hora de publicación. → `docs/f3-features.md` §4 y §9
- **F3.3 Objetivo:** `y_cf`, `y_cf_land`, `y_cf_sea` con la capacidad MaStR del día y la generación en MW (`y_gen_*`). → `docs/f3-features.md` §5
- **F3.4 Interpolación** de 3 h a 15 min: lineal al punto medio; racha constante por tramos. PCHIP solo mejora un 2,3 % el viento. → `data/validation/gold_interp_check.json`
- **Test anti look-ahead:** falla si alguna variable está disponible después del cierre; 10 tests, incluida la tabla real; margen mínimo real 4 h 30 min. → `tests/leakage/test_leakage_features.py`
- **Tabla oro** `data/gold/features` (Delta): 39 360 filas (410 cierres; 92/96/100 pasos según el cambio de hora), 260 columnas, 76 MiB, ~80 s; idempotente y validada con Pandera (0 problemas). → `data/validation/gold_features.json`

## Backtest y referencias (F4, F5)

- **F4.1 Walk-forward** con ventana creciente y pliegues mensuales: 8 pliegues (dic 2025 – jul 2026), 3 meses mínimos de entrenamiento. → `src/predico50hz/evaluation/folds.py`
- **F4.2 Periodo reservado** ago – oct 2026 fuera de los pliegues; su evaluación es explícita (`--holdout`) y queda contada en `data/backtest/holdout_log.jsonl`.
- **F4.3 Métricas como Predico:** RMSE del Q50, Winkler con α = 0,2, cobertura, error de pico y ranking diario frente al operador; scorecard con veredicto y 5 PNG en `data/backtest/report/`; capítulo «¿Mejora el pronóstico?» del informe ejecutivo. → `src/predico50hz/evaluation/metrics.py`, `docs/f4-backtest.md`
- **Criterios:** tests unitarios de las métricas con casos calculados a mano; ninguna fila de entrenamiento posterior al inicio de su pliegue (`tests/leakage/test_backtest_folds.py`, también sobre la tabla real).
- **F5 (parcial) Referencias** en validación dic 2025 – jul 2026, RMSE del Q50: operador 931 MW, curva de potencia sobre el viento a buje 1 197 MW (cobertura 76,9 %), climatología 3 406 MW. Faltan persistencia y MLflow (en GitHub). → `src/predico50hz/models/baselines.py`, `docs/f4-backtest.md` §8

## Operación diaria

- **Timers systemd de usuario** para `nwp-daily`, `entsoe-daily` y `entsoe-hourly`, con registro de fallos (`OnFailure`) y `status` con la puerta G0. → `deploy/systemd/`, `docs/f1.5-f2.7-operations.md` §3
- **Docker y VM de Hetzner** para la comprobación de 7 días (desplegada el 16 oct). → `docker/`, `deploy/hetzner/`, `docs/f1.5-f2.7-operations.md` §10
- **20 oct:** cada job de systemd espera a tener red (`wait-online`) y se reintenta si falla (`Restart=on-failure`); el cliente de ENTSO-E reintenta los HTTP 400 intermitentes de la API y muestra su `<Reason>`. → `docs/f1.5-f2.7-operations.md` §3 y §7

## Calidad e ingeniería (F8)

- **(Parcial)** 276 tests con `uv run pytest`, incluidos los anti look-ahead de variables y de pliegues en `tests/leakage/`. CI, tests de integración, contratos y reproducibilidad siguen pendientes en GitHub.

## Informe ejecutivo

- Página única para negocio, sin jerga, generada con `uv run python -m predico50hz.report`; versiones y novedades en `report/changelog.yaml`. → `docs/report.md`
