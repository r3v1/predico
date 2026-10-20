# ADR 0025 — Validación de datos con Pandera

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Cada capa (bronce, plata, oro) necesita comprobar esquema, rangos físicos y huecos antes de que los datos sigan adelante.

## Decisión

- **Pandera** en el código (backend Polars), con esquemas por capa y un informe JSON por ejecución en `data/validation/`.
- Great Expectations solo si se necesita su informe HTML.

Descartado: Great Expectations como primera opción, más pesado de configurar para un solo desarrollador.

## Referencias

- `STACK.md` §2, `docs/f1.6-validation.md`, `docs/f3-features.md` §7
