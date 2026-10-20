# ADR 0012 — Interpolación temporal de 3 h a 15 min

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

La NWP llega cada 3 h y Predico puntúa cuartos de hora. Hay que llevar las variables a la malla de 15 min sin inventar picos ni romper relaciones físicas (velocidad ≥ 0, p10 ≤ p50 ≤ p90).

## Decisión

- Cada intervalo `[t, t + 15 min)` toma el valor en su **punto medio**, porque la generación de ENTSO-E es la media del intervalo.
- **Lineal** para las variables instantáneas y todos sus estadísticos del ensemble; se interpolan primero los estadísticos crudos y después se derivan v³, densidad, etc.
- **Racha constante por tramos**: es el máximo de la ventana anterior y una lineal suavizaría el pico.
- PCHIP se descartó: en la prueba 6 h → 3 h solo mejora un 2,3 % el viento.

## Consecuencias

- La lineal conserva por construcción el orden de cuantiles y la no negatividad; si se cambiase de método habría que reordenar y recortar.

## Referencias

- `docs/f3-features.md` §3, `scripts/f3_interp_check.py`, `data/validation/gold_interp_check.json`
