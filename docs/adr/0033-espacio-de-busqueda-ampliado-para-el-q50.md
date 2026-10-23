# ADR 0033 — Espacio de búsqueda ampliado para el Q50

- **Estado:** aceptada (enmienda el espacio de búsqueda del ADR 0015 para el Q50)
- **Fecha:** 2026-10-23

## Contexto

En #32 se optimizó LightGBM con Optuna sobre los 11 pliegues pares de la validación (ADR 0032), con el espacio del ADR 0015. En el Q50 el óptimo quedó junto al borde (`num_leaves` 21, `feature_fraction` 0,52), y la mejora no se sostuvo en los 11 pliegues impares, que no se usaron para elegir: −1,2 % en los pares y +0,1 % en los impares.

Un estudio exploratorio (`lgbm-wide-q50`, 149 intentos) amplió dos rangos: `num_leaves` 4–255 y `feature_fraction` 0,2–1. Su mejor intento usa solo un 25 % de las variables en cada árbol. En los 22 pliegues da un RMSE de 1 041,0 MW frente a los 1 060,8 de la v0.1 (−1,9 %), con −2,3 % en los pares y **−1,5 % en los impares**. Es el único ajuste cuya mejora se mantiene fuera de los pliegues usados para elegir. `feature_fraction` es el hiperparámetro más importante del estudio (0,41).

## Decisión

- Para el Q50 el espacio es: `num_leaves` 4–255 y `feature_fraction` 0,2–1. El resto de rangos sigue igual que en el ADR 0015.
- `config/lgbm_tuned.yaml` toma para el Q50 el mejor intento de `lgbm-wide-q50` (nº 115). Q10 y Q90 siguen con los estudios del espacio del ADR 0015.
- Lo demás del ADR 0015 (TPE, poda por mediana, objetivo y control de sobreajuste con el periodo reservado) no cambia.

## Consecuencias

- La mejora es pequeña: el Q50 sigue un 19 % por encima del operador. El ajuste de hiperparámetros no basta para G1 (#22); la mejora hay que buscarla en las variables o en el modelo.
- Q10 y Q90 no se han repetido con el espacio ampliado. Su efecto pasa por la CQR, que ya corrige la cobertura.
- La confirmación real es el periodo reservado (#33, #34).

## Referencias

- ADR 0006, ADR 0015, ADR 0032, #32
- `docs/f4-backtest.md` §8.6, `config/lgbm_tuned.yaml`
