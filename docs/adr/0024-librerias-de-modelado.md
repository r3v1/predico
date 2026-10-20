# ADR 0024 — Librerías de modelado: LightGBM, Optuna, PyTorch y MLflow

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Hay que fijar las librerías para el modelo principal, la optimización, las redes neuronales y el seguimiento de experimentos, en una sola máquina con GPU de portátil.

## Decisión

| Función | Elección | Descartado |
|---|---|---|
| Gradient boosting | **LightGBM** | XGBoost: solo como comparación si el entrenamiento supera 2 h en CPU |
| Hiperparámetros | **Optuna**, con almacenamiento en SQLite o PostgreSQL | Ray Tune: excesivo para una sola máquina |
| Calibración conformal | **CQR propia** (unas 50 líneas, con tests) | MAPIE: válido, pero añade una dependencia para algo pequeño y crítico que conviene controlar línea a línea |
| Redes y modelos fundacionales | **PyTorch** | |
| Seguimiento de experimentos | **MLflow** | |

## Consecuencias

- LightGBM, Optuna y MLflow aún no están en `pyproject.toml`: entran con sus issues (v0.1 y G2).

## Referencias

- `STACK.md` §2, ADR 0014, ADR 0015, ADR 0016
