# ADR 0015 — Optimización de hiperparámetros con Optuna y control de sobreajuste

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

LightGBM mejora con ajuste de hiperparámetros, pero optimizar mucho sobre pocos pliegues sobreajusta a la validación.

## Decisión

- **Optuna** con muestreador TPE, poda por mediana y **150–300 intentos por modelo** (50 si el calendario aprieta, ADR 0006). Número de árboles por parada temprana.
- **Espacio de búsqueda:**
  - `num_leaves`: 15–255
  - `learning_rate`: 0,01–0,1 (logarítmico)
  - `min_data_in_leaf`: 20–500
  - `feature_fraction` y `bagging_fraction`: 0,5–1
  - `lambda_l1` y `lambda_l2`: 10⁻³–10 (logarítmico)
- **Objetivo:** media de la métrica en los pliegues de validación. Todo se registra en MLflow.
- **Control de sobreajuste:** si la métrica en el periodo reservado empeora más de un 10 % respecto a validación, se simplifica el espacio de búsqueda.

## Consecuencias

- El periodo reservado queda fuera de la optimización (ADR 0010).
- Criterio de aceptación del modelo: G1 y G2 superadas sobre el periodo reservado (ADR 0005).

## Referencias

- `STACK.md` §2 (Optuna, MLflow)
