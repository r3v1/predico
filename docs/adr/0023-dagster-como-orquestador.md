# ADR 0023 — Dagster como único orquestador; Kedro aplazado

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

El principio del stack es **una herramienta por función**: cada framework añadido es mantenimiento y una fuente más de fallos a la hora del cierre. Kedro aporta catálogo de datos, plantilla de proyecto y pipelines de nodos; Dagster da todo eso con su grafo de assets y además hace falta para producción.

## Decisión

- **Dagster** (assets) como único orquestador.
- **Kedro, no en la v0.1.** Tener ambos significa dos grafos de dependencias que sincronizar.
- **Umbral para reconsiderarlo:** si en la v0.2 los experimentos de modelado (muchas variantes de variables y modelos sobre los mismos datos) se vuelven incómodos en Dagster, se introduce Kedro solo para la capa de modelado.

## Consecuencias

- Hasta la producción, la operación diaria corre con timers de systemd y con el planificador propio `serve` en Docker; pasar a Dagster es un issue del milestone `5 · Producción`, que decidirá si los sustituye o conviven.

## Referencias

- `STACK.md` §1 y §2.1
