# ADR 0028 — Calidad de código, hooks de pre-commit e integración continua

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Un error en las métricas o en las variables cuesta dinero en la liga y es difícil de ver a simple vista. Las comprobaciones tienen que ser automáticas.

## Decisión

- **ruff** para lint y formato (reglas `E, F, I, B, UP, SIM, PL, NPY, PD, PT, RUF`).
- **pyright** en modo estricto en `src/`.
- **pytest + pytest-cov**, con cobertura ≥ 80 % en `evaluation/` y `features/`; **hypothesis** para tests por propiedades de las métricas (por ejemplo, que el Winkler nunca sea negativo).
- **nbstripout**: notebooks sin salidas en git.
- **Hooks de pre-commit:** ruff y ruff-format, pyright, `uv lock --check`, nbstripout, gitleaks, `check-added-large-files` (límite 1 MB) y commitizen (mensajes con Conventional Commits).
- **CI en cada push** con GitHub Actions: lint, tipos, tests unitarios y anti look-ahead.

## Consecuencias

- La CI y hypothesis aún no están: la CI es un issue del milestone `2 · Modelo v0.1`.

## Referencias

- `STACK.md` §4.2, §4.3 y §4.6, `.pre-commit-config.yaml`
