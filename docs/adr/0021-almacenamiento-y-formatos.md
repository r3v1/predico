# ADR 0021 — Almacenamiento: Zarr + Icechunk para campos, Delta + Parquet para tablas

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Hay dos tipos de datos: campos meteorológicos multidimensionales (miembro × paso × latitud × longitud) y tablas (plata por clúster, ENTSO-E, MaStR, variables). Ambos necesitan versionado y escrituras idempotentes con un solo escritor.

## Decisión

- **Campos meteorológicos:** xarray + Zarr v3 + **Icechunk** (versionado por commits). Lectura de dynamical con icechunk + xarray en lazy y recorte en origen.
- **Respaldo GRIB:** eccodes + cfgrib con un descargador propio por rangos de bytes (`src/predico50hz/ingest/ifs/`).
- **Tablas:** **Polars** + Parquet con **Delta Lake** (delta-rs); **DuckDB** para consultas.
- **Nada de datos en git:** los datos viven en el NAS (`/mnt/data/…`) y en `data/` (ignorado); el repo solo tiene código y configuración.

Descartado: NetCDF (sin versionado ni escritura concurrente segura); Iceberg (más pesado de operar sin catálogo; Delta basta con un escritor); ecmwf-opendata y Herbie para el respaldo (las rutas del bucket cambian con la fecha y la etapa 0 tuvo que resolverlas listando).

## Consecuencias

- Cada experimento puede enlazarse a una versión de datos concreta (snapshot de Icechunk, versión Delta).
- Un solo escritor por almacén: la operación diaria usa locks de fichero.

## Referencias

- `STACK.md` §2, ADR 0003, `docs/stage1-dynamical.md`, `docs/stage1-pipeline.md`
