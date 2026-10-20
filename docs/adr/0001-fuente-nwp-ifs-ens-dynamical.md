# ADR 0001 — Fuente NWP principal: ECMWF IFS ENS en dynamical.org, con respaldo GRIB `enfo`

- **Estado:** aceptada
- **Fecha:** 2026-10-16 (sustituye a la elección de IFS HRES en GRIB de la versión anterior del plan)

## Contexto

El modelo necesita una previsión meteorológica numérica (NWP) con histórico suficiente para entrenar y una entrega diaria fiable antes del cierre. La primera opción fue IFS HRES en los GRIB abiertos de ECMWF Open Data. La etapa 0 (`docs/stage0-index-check.md`) mostró que en esos GRIB `100u/100v` solo existen desde 2024-03-06 y `10fg` desde 2024-11-13, así que el histórico útil es corto en cualquier caso.

## Decisión

- **Principal:** IFS ENS de ECMWF a 0,25° desde el archivo Icechunk/Zarr público de dynamical.org (`ecmwf-ifs-ens-forecast-15-day-0-25-degree`, bucket `s3://dynamical-ecmwf-ifs-ens`, us-west-2, acceso anónimo, licencia CC BY 4.0 + ECMWF Terms of Use), con la versión del dataset fijada (`dataset_version` 0.1.0).
- **Respaldo de la diaria:** los GRIB del stream `enfo` en `s3://ecmwf-forecasts`, el mismo origen del que dynamical construye su archivo. Se reutiliza la cadena GRIB de la etapa 1 original (`src/predico50hz/ingest/ifs/`).
- **Si IFS ENS no basta en G1**, siguientes fuentes en este orden: AIFS Single y AIFS ENS de dynamical (mismo código) y después ICON-D2 por puntos vía Open-Meteo Previous Runs.

Motivos:

1. **El histórico efectivo es el mismo.** dynamical empieza el 2024-04-01: se pierde un mes frente a los GRIB, no años.
2. **Ensemble de 51 miembros** (control + 50 perturbados). Media, dispersión y cuantiles del ensemble son variables directas para Q10/Q90 y el Winkler; HRES no las da.
3. **Lectura en lazy con recorte en origen.** Chunks de 32×32 puntos (8°) con todos los pasos y miembros: el dominio se lee con un `sel()`, sin GRIB, sin eccodes y sin descargar campos globales. Medido: ~4 s por variable y pasada (51 miembros, dominio completo, pasos +18 a +54 h).
4. **Mismo formato que la capa bronce** (Icechunk) y versionado.

## Consecuencias

- Solo hay pasada 00Z útil (ver ADR 0002); si hiciera falta otra, AIFS Single de dynamical tiene pasadas cada 6 h.
- No hay `u/v` en niveles de presión: se pierde el chorro de bajo nivel directo. Se conservan la estabilidad (`temperature_925hpa` − `temperature_2m`) y la cizalladura 10–100 m. Se espera que el ensemble lo compense con creces.
- El control del ENS no es HRES. Da igual mientras entrenamiento y producción usen la misma fuente.
- Dependencia de un tercero: hay que fijar la versión del dataset, guardar nuestra copia del recorte (bronce propio) y mantener vivo el respaldo `enfo`.
- La latencia de dynamical se midió (`docs/stage0-dynamical.md` §3): la 00Z llega hacia las 08:20 UTC, con ~280 min de margen sobre el cierre; `enfo` llega ~40 min antes. Si dynamical no llega con ≥ 60 min de margen, la diaria debe usar `enfo`.

## Referencias

- `docs/stage0-index-check.md`, `docs/stage0-dynamical.md`, `docs/stage1-dynamical.md`, `docs/stage1-pipeline.md`
- `STACK.md` §2 (NWP principal y respaldo)
