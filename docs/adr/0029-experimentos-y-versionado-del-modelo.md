# ADR 0029 — Trazabilidad de experimentos y versionado semántico del modelo

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Para comparar modelos y reproducir un resultado hay que saber exactamente con qué código, datos y dependencias se obtuvo.

## Decisión

- **Cada experimento en MLflow** enlaza el commit de git, la versión del snapshot de Icechunk (y la versión Delta del oro) y el hash del lockfile.
- **Versionado semántico del modelo:** v0.1, v0.2… Un cambio de variables sube la versión menor; un cambio de fuente NWP, la mayor.

## Consecuencias

- El criterio de reproducibilidad (reentrenar desde cero reproduce las métricas con < 0,5 % de diferencia) depende de este enlace.
- Los milestones de GitHub usan un número de orden, no la versión del modelo, para no mezclar ambas cosas.

## Referencias

- `STACK.md` §4.5, ADR 0024
