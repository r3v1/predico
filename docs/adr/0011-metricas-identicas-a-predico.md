# ADR 0011 — Métricas idénticas a las de Predico

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Lo que importa es la puntuación en la liga, no una métrica cómoda de entrenar. Si el backtest mide otra cosa, las decisiones se toman con la vara equivocada.

## Decisión

Un único módulo de métricas, el mismo para todos los modelos y referencias, en MW y sobre cuartos de hora:

- **RMSE del Q50** (métrica principal de la puerta G1).
- **Mean Winkler Interval** con α = 0,2 para el intervalo Q10–Q90.
- **Cobertura empírica** del intervalo (G1 exige 76–84 %).
- **Error de pico** por día (magnitud y desfase).
- **Ranking diario simulado** frente al operador (% de días en que el modelo tiene menor RMSE).

Con tests unitarios de casos calculados a mano.

## Consecuencias

- Si un modelo no da cuantiles (el operador), Winkler y cobertura quedan vacíos; nunca se rellenan.
- Al conocer la API de Predico hay que confirmar que las definiciones coinciden.

## Referencias

- `docs/f4-backtest.md` §2, `src/predico50hz/evaluation/metrics.py`
