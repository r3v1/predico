# ADR 0032 — Partición y pliegues con el histórico completo

- **Estado:** aceptada (sustituye a la partición del ADR 0010)
- **Fecha:** 2026-10-23

## Contexto

El ADR 0010 fijó la partición de la v0.1 con 13 meses (sep 2025 – oct 2026) y un mínimo de 3 meses de entrenamiento por pliegue. La etapa 3 del histórico ya está en la tabla oro (#26): 936 cierres desde el 1 abr 2024. Con 3 meses, los primeros pliegues entrenaban con 90 días y la validación no veía el invierno 2024/25, que es lo que permite juzgar diciembre.

## Decisión

- **Partición por `target_date`:** `train` del 1 abr al 30 sep 2024 (solo entrena), `valid` del 1 oct 2024 al 31 jul 2026, `holdout` desde el 1 ago 2026 sin cambios.
- **Mínimo de 6 meses de entrenamiento** (`DEFAULT_MIN_TRAIN_MONTHS = 6`): 22 pliegues mensuales, de oct 2024 a jul 2026. El primero entrena con 182 días; los de diciembre y enero de 2024/25, con 8 y 9 meses. Un test comprueba que el fin de `train` coincide con el inicio del primer pliegue.
- **Racha antes del 13 nov 2024** (no existe en la NWP): NaN nativa de LightGBM con `gust_missing` = 1. Quitar la racha cambia el RMSE menos del 1 % (1 054 frente a 1 061 MW).
- Se mantiene lo demás del ADR 0010: walk-forward con ventana creciente, el periodo reservado nunca lo ve la optimización y cada evaluación queda anotada.

## Consecuencias

- La validación cubre dos inviernos. Las cifras ya no son comparables con las de 13 meses; `docs/f4-backtest.md` da también el periodo dic 2025 – jul 2026 para compararlas.
- G1 (#22) se evalúa sobre estos 22 pliegues.
- Para los cierres de 2024 la capacidad MaStR y la NWP son las reconstruidas, con las mismas limitaciones que el resto del histórico (`docs/f3-features.md` §11).

## Referencias

- ADR 0004, ADR 0010, #26
- `docs/f4-backtest.md` §3, `src/predico50hz/features/config.py`, `src/predico50hz/evaluation/folds.py`
