# ADR 0019 — Registrar las decisiones con ADR y documentar con reglas fijas

- **Estado:** aceptada
- **Fecha:** 2026-10-15 (`STACK.md` v1.0)

## Contexto

El proyecto lo lleva una sola persona con un calendario de seis semanas. Sin un registro, el porqué de cada decisión se pierde y se vuelve a discutir lo ya decidido.

## Decisión

- Cada decisión de arquitectura o de stack se registra como un **ADR** en `docs/adr/`, con formato MADR resumido: contexto, decisión (con las opciones descartadas) y consecuencias. **Una página como máximo.**
- **Un ADR aceptado no se edita:** si la decisión cambia, se escribe uno nuevo que lo sustituye y el anterior pasa a «sustituida por ADR NNNN».
- **README:** qué es, cómo arrancar en pocos comandos, arquitectura en un diagrama y enlaces a ADR y runbooks.
- **Runbooks** en `docs/runbooks/`: uno por modo de fallo de producción (no llega la NWP, no responde Predico, cae el equipo principal).
- **Docstrings** en las funciones públicas de `evaluation/` y `features/`, con unidades y hora de disponibilidad de cada variable.
- El trabajo pendiente vive en los issues y milestones de GitHub, no en documentos del repo.

## Consecuencias

- Las decisiones del antiguo `PLAN.md` se trasladaron a los ADR 0001–0018 el 20 oct 2026; las de `STACK.md`, a los ADR 0019–0029.

## Referencias

- `STACK.md` §4.4 y §5, índice en `docs/adr/README.md`
