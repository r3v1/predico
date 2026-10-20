# ADR 0009 — Uniones «as-of» y test automático anti look-ahead

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

El error más caro en un backtest de previsión es usar sin querer información que no habría estado disponible a la hora del cierre: el resultado parece bueno y luego en producción no se repite.

## Decisión

- Cada bloque de variables lleva su **hora de disponibilidad real** (`available_at_<bloque>`): NWP = pasada 00Z de D + 8 h 30 min (peor caso medido 08:23 UTC más margen), capacidad y calendario a las 00:00 UTC de D. La latencia de producción se aplica también al histórico.
- Solo se usa la pasada 00Z del día del cierre; si falta, no hay filas de ese día (no se rellena con otra pasada).
- **Test automático** que falla si alguna variable tiene hora de disponibilidad ≥ 13:00 UTC (verano) o 14:00 UTC (invierno) del día del cierre. Se ejecuta también sobre la tabla real.
- El **precio day-ahead** solo se usa como variable si su hora de publicación medida es anterior al cierre. Mientras no esté medida, queda fuera.
- Nunca se usan como variables la producción real del día objetivo ni el pronóstico del operador.

## Consecuencias

- El test anti look-ahead nunca se recorta (ADR 0006).
- Cualquier bloque nuevo de variables necesita su `available_at` y su caso en el test.

## Referencias

- `docs/f3-features.md` §2, §8 y §9, `tests/leakage/test_leakage_features.py`
