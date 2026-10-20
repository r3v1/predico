# ADR 0014 — Cuantiles: Q50 con pérdida cuadrática, Q10/Q90 cuantílicos y calibración conformal

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Predico pide una predicción central y un intervalo. La central se puntúa con RMSE y el intervalo con el Winkler y la cobertura (ADR 0011).

## Decisión

- **Q50 con pérdida cuadrática**, no cuantílica al 0,5: se puntúa con RMSE y el RMSE lo minimiza la media, no la mediana.
- **Q10 y Q90 con pérdida cuantílica**, más **calibración conformal (CQR)** sobre una ventana móvil de 60 días.
- **Orden forzado:** Q10 ≤ Q50 ≤ Q90.
- Modelo base: **LightGBM**. La CQR es una implementación propia y pequeña, con tests (`STACK.md`).

## Consecuencias

- La calibración conformal nunca se recorta (ADR 0006). Hoy, sin calibrar, la cobertura por mes de las referencias oscila entre 69 y 83 %.
- La ventana de la CQR solo puede usar residuos con realidad disponible antes del cierre (ADR 0009).

## Referencias

- `STACK.md` §2, `docs/f4-backtest.md` §9
