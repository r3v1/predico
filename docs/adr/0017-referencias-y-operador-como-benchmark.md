# ADR 0017 — Cuatro referencias de comparación; el operador solo como benchmark

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Para saber si un modelo aporta hay que compararlo con alternativas sencillas medidas exactamente igual. El pronóstico day-ahead del operador (TSO) es la referencia que hay que batir en la liga, pero no se puede usar como variable: su hora de publicación no está garantizada antes del cierre.

## Decisión

- Cuatro referencias, evaluadas con el mismo módulo de métricas (ADR 0011) y registradas en MLflow:
  1. **Persistencia** (requiere saber hasta qué hora hay generación publicada antes del cierre);
  2. **climatología** por hora y mes;
  3. **curva de potencia empírica** sobre el viento a 100 m (altura de buje);
  4. **pronóstico del operador**.
- El pronóstico del operador se descarga y guarda **solo como benchmark**, con su hora de publicación; nunca entra como variable del modelo.

## Consecuencias

- La comparación justa con el operador exige usar la versión que estaba publicada antes del cierre, no su última revisión.

## Referencias

- `docs/f4-backtest.md` §8–§9, `src/predico50hz/models/baselines.py`, `docs/f2-entsoe.md` §3
