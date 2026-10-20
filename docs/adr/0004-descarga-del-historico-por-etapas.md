# ADR 0004 — Descarga del histórico por etapas y v0.1 sobre 13 meses

- **Estado:** aceptada
- **Fecha:** 2026-10-15 (versión 1.1 del plan)

## Contexto

Descargar todo el histórico de una vez sin haber validado la cadena es arriesgado: un error de recorte, unidades o esquema obligaría a repetirlo todo. Además, el calendario no permite esperar al histórico completo para tener un primer modelo.

## Decisión

**Principio:** nunca descargar volumen grande sin haber validado antes la cadena completa con un volumen pequeño.

| Etapa | Qué | Coste | Puerta para pasar a la siguiente |
|---|---|---|---|
| 0 | Metadatos de dynamical: variables, cobertura, NaN, latencia | Kilobytes | Variables presentes y cobertura documentada |
| 1 | 7 días completos (1–7 oct 2026) de extremo a extremo: lectura lazy → recorte → bronce → plata | Cientos de MB | La cadena funciona; MB y segundos por pasada medidos |
| 2 | Últimos 13 meses: 1 sep 2025 → hoy | ~45 % del total | Datos validados → entrenar la v0.1 |
| 3 | 1 abr 2024 → 31 ago 2025, en segundo plano | Resto | Reentrenar como v0.2 con el histórico completo antes de G2 |

**Por qué 13 meses y no solo desde septiembre de 2026:**

- **Estacionalidad.** El concurso empieza en diciembre, el mes de más viento. Un modelo entrenado solo con septiembre y octubre nunca habría visto un temporal invernal ni las rampas de un frente atlántico.
- **Tamaño mínimo para evaluar.** Seis semanas son unos 40 días: no dan para validación walk-forward ni para un periodo reservado.

## Consecuencias

- La v0.1 se entrena antes de tener el histórico completo; sus pliegues y métricas habrá que repetirlos con la etapa 3.
- La ingesta tiene que ser idempotente y reanudable (`--skip-existing`).

## Referencias

- ADR 0010 (partición de la v0.1), `docs/stage0-dynamical.md`, `docs/stage1-dynamical.md`
