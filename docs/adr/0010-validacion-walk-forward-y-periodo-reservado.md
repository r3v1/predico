# ADR 0010 — Validación walk-forward, partición de la v0.1 y periodo reservado

- **Estado:** partición sustituida por ADR 0032 (el resto sigue vigente)
- **Fecha:** 2026-10-16

## Contexto

Hay que medir el modelo como funcionará en producción (entrenado solo con el pasado) y reservar un periodo que nunca toque la optimización, para confirmar al final que la mejora es real y no sobreajuste.

## Decisión

- **Walk-forward con ventana creciente y pliegues mensuales:** cada pliegue valida un mes natural y entrena con todo lo anterior; mínimo de 3 meses de entrenamiento.
- **Partición de la v0.1** (13 meses, ADR 0004):
  - entrenamiento: sep 2025 – may 2026, que incluye el invierno 2025/26;
  - validación: jun – jul 2026;
  - **periodo reservado (holdout): ago – 15 oct 2026.**
- El periodo reservado **nunca** lo ve la optimización de hiperparámetros. Su evaluación es explícita (`--holdout`) y cada vez que se mira queda anotada en `data/backtest/holdout_log.jsonl`.
- Test automático: ninguna fila de entrenamiento posterior al inicio de su pliegue de validación.

## Consecuencias

- **Limitación conocida:** el periodo reservado de la v0.1 es verano y otoño, con poco viento; no garantiza el comportamiento en diciembre. La v0.2, con el invierno 2024/25 en los pliegues de validación, sí lo cubre.
- Al ampliar el histórico (etapa 3) los pliegues cambian y hay que repetir las evaluaciones.
- El periodo reservado nunca se recorta (ADR 0006).

## Referencias

- `docs/f4-backtest.md` §3, `tests/leakage/test_backtest_folds.py`
