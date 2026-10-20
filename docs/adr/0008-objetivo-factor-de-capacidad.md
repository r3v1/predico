# ADR 0008 — Objetivo: factor de capacidad con la capacidad de MaStR

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

La potencia instalada crece durante el histórico. Predecir los MW directamente obliga al modelo a aprender esa tendencia. La capacidad anual de ENTSO-E va retrasada respecto a las altas: en varios años la generación marina observada supera a la capacidad declarada (hasta 6,9 veces en 2015), así que no sirve como denominador.

## Decisión

- El modelo predice el **factor de capacidad**: generación / capacidad instalada (`y_cf`, y por separado `y_cf_land` y `y_cf_sea`).
- El denominador es la **capacidad de MaStR de la fecha UTC de cada intervalo**, nunca la de ENTSO-E.
- La predicción se reconvierte a MW con la capacidad del día; la evaluación se hace en MW, como Predico.
- Coherencia exigida entre la suma de los clústeres y ENTSO-E: desviación < 5 %. Se cumple en tierra todos los años y en mar salvo 2018, porque ENTSO-E omite Wikinger; se toma MaStR como referencia.

## Consecuencias

- Las altas registradas a posteriori en MaStR afectan igual al modelo y al operador (que se compara en MW).
- En producción, el denominador depende de tener MaStR al día.

## Referencias

- `docs/f3-features.md` §5 y §10, `docs/f2.6-mastr.md` §7–§8, `docs/f2-entsoe.md` §4
