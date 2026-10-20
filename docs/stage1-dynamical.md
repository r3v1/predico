# Etapa 1 — Ingesta IFS ENS desde dynamical.org, extremo a extremo

Siete pasadas 00Z (2026-10-01 a 2026-10-07) más cuatro (2026-09-26 a 09-29, prueba de lectura en paralelo). Métricas en `data/stage1/dynamical_metrics.json`. Latencia en `docs/stage0-dynamical.md`.

## 1. Arquitectura

Paquete `src/predico50hz/ingest/dynamical/`, lanzado con `uv run python -m predico50hz.ingest.dynamical --start 2026-10-01 --end 2026-10-07`.

| Módulo | Función |
|---|---|
| `source.py` | Abre el Icechunk de origen (href del STAC, `--href` o `--snapshot-id` para fijarlo), exige `dataset_version` = 0.1.0, y lee por `isel` lazy: pasos +18..+54 h, 51 miembros, dominio 49,5–56,0° N × 8,5–16,0° E (27 × 31 puntos). Las variables se leen en hilos (`--workers`). La latitud de origen es descendente y se invierte a ascendente |
| `bronze.py` | Icechunk propio en `/mnt/data/nwp/IFS-ENS/icechunk` (escribible, NFS). Un array float32 por variable, dims `init_time, lead_time, ensemble_member, latitude, longitude`; eje `init_time` diario prerreservado (2024-04-01 a 2027-12-31) y chunk (1, 13, 51, 27, 31): una pasada = una región fija, sobrescribible y en cualquier orden. Un commit por pasada con la fecha y el snapshot de origen en el mensaje; en attrs del grupo: `source_href`, `source_dataset_version` y `provenance[fecha].source_snapshot_id`; flag `ingested` por pasada |
| `silver.py` | Bilineal por miembro en los centroides provisionales (reutiliza `bilinear` y `load_points` de `ingest/ifs/extract.py`), luego **velocidad por miembro** (100 m y 10 m) desde (u, v) ya interpolados, luego agregados del ensemble: `ctrl` (miembro 0), `mean`, `std` (muestral, ddof=1), `p10`, `p50`, `p90`. 12 variables × 6 estadísticos = 72 columnas más claves; 104 filas por pasada (8 puntos × 13 pasos). Parquet zstd en `data/silver/ifs_ens_points/init_date=AAAA-MM-DD/part-0.parquet`; escritura atómica |
| `pipeline.py` | `read_stage` (lectura + MB de red) y `write_stage` (bronce, plata, hashes) |

Para los puntos de mar el interpolado es el mismo (no hay máscara tierra-mar en dynamical; sigue pendiente, ADR 0003). El CLI admite `--skip-existing` (reanudar) y `--init-workers N` (pasadas leídas a la vez; la escritura sigue siendo secuencial y en orden).

## 2. Métricas (7 pasadas, `--workers 5`, secuencial)

| Métrica por pasada | Media | Mediana | Mín–máx |
|---|---|---|---|
| Lectura (s) | 14,8 | 12,9 | 11,2–24,8 |
| Escritura de bronce (s) | 3,0 | 2,9 | 2,7–3,8 |
| Escritura de plata (s) | 0,06 | 0,06 | 0,04–0,07 |
| MB recibidos en la lectura | 176,6 | 176,0 | 175,2–178,9 |
| Plata (KB en disco) | 59,5 | 59,5 | 59,0–60,1 |

Tiempo total real por pasada: 15,2 s (106,5 s las 7). **Bronce**: 43,5 MB para las 7 pasadas (6,2 MB/día, tras zstd), medido con `du` del repositorio antes de las repeticiones de idempotencia. **Plata**: 416 KB las 7 (59 KB/día).

**Cómo se miden los MB.** Delta del contador de bytes recibidos (`/proc/net/dev`) de la interfaz de la ruta por defecto durante `read_cube`, en un proceso que solo hace esa lectura. Incluye cabeceras HTTP/TLS y cualquier tráfico ajeno de la máquina en ese instante (no se aisló), pero las 7 pasadas dan 175–179 MB, así que el ruido es pequeño. Es mayor que el tamaño del recorte (22 MB sin comprimir por pasada) porque el chunk de origen es de 32 × 32 puntos con 85 pasos y 51 miembros: el dominio toca 2 × 2 chunks por variable (~4,4 MB cada uno) y se descarga entero aunque solo se use el 15 % de los pasos (13 de 85). Con `--init-workers` > 1 el delta mezcla pasadas concurrentes (en la prueba de 2 en paralelo salen ~345 MB por pasada, el doble); esos valores no son atribuibles y no se usan.

**Concurrencia de variables** (lectura de una pasada, 10 variables; la red es compartida y hay ruido, la misma configuración varió ±2 s entre repeticiones):

| `--workers` | 1 | 2 | 3 | 4 | 5 | 8 | 10 | 20 |
|---|---|---|---|---|---|---|---|---|
| Lectura (s) | 36,7 | 26,8 | 14,8 | 12,2 | 13,6 | 32,4 | 14,9 | 16,9 |

Se mantiene **5** (4–5 es el mínimo estable; más no ayuda y el punto de 8 fue un valor atípico lento). El rendimiento queda en ~14 MB/s por pasada, bastante por debajo de lo que daría la línea, y escalar variables no lo sube: probablemente lo limita la latencia por chunk. Leer **2 pasadas a la vez** (cada una con 5 hilos) sí ayuda: la lectura de una pasada tarda ~15–17 s pero salen dos a la vez, y el tiempo total medido es **9,5 s por pasada** (4 pasadas, 38,0 s, incluyendo la escritura), frente a 15,2 s en serie. Con 3 pasadas simultáneas el tiempo de pared por pasada no mejoró (8,7 s de solo lectura frente a 7,6 s con 2, medición de apoyo en `/tmp`, no incluida en el JSON).

## 3. Extrapolación del histórico

Base: 176,6 MB, 6,2 MB de bronce y 59,5 KB de plata por pasada; 15,2 s por pasada en serie y 9,5 s con `--init-workers 2`. Supone que todas las pasadas cuestan lo mismo (antes de 2024-11-13 la racha es NaN y comprime mejor, así que es una cota algo alta de bronce) y no incluye reintentos.

| Etapa | Días | Transferencia | Bronce | Plata | Horas (serie) | Horas (`--init-workers 2`) |
|---|---|---|---|---|---|---|
| 2: 2025-09-01 → 2026-10-15 | 410 | 72 GB | 2,5 GB | 24 MB | 1,7 | 1,1 |
| 3: 2024-04-01 → 2025-08-31 | 518 | 91 GB | 3,2 GB | 31 MB | 2,2 | 1,4 |
| Total | 928 | 164 GB | 5,8 GB | 55 MB | 3,9 | 2,4 |

El coste real del histórico está en la **transferencia (164 GB)**, no en el disco ni en la CPU. Son horas, no días.

## 4. Latencia

Detalle en `docs/stage0-dynamical.md`. La pasada 00Z completa está en dynamical a las **08:15–08:23 UTC** (24 días medidos), es decir, 4 h 37 min antes del cierre de 13:00 UTC en el peor caso. La lectura diaria (una pasada, ~15 s) no añade nada apreciable.

## 5. Idempotencia

Pasada 2026-10-04 relanzada dos veces: una con el mismo snapshot de origen y otra con el `main` actual (el mismo contenido):

| Comparación | Resultado |
|---|---|
| SHA-256 de los valores de bronce de la pasada (10 variables, float32) | idéntico en las tres lecturas |
| Hash del contenido de plata (columnas y valores) | idéntico |
| SHA-256 del fichero Parquet de plata | idéntico (el fichero no lleva el snapshot de origen) |

Cada relanzamiento crea un commit nuevo (se reescriben los chunks) y el repositorio crece ~5,9 MB hasta que se expiren los snapshots antiguos y se haga gc; en producción conviene `--skip-existing` o un expire periódico.

## 6. Problemas y decisiones

- **Latitud descendente en origen.** Se invierte a ascendente al leer (el `bilinear` reutilizado lo exige). El test `test_read_cube_crops_domain_and_flips_latitude` usa un campo cuyo valor es la latitud para detectar una inversión mal hecha; además se comprobó que 4 valores de bronce coinciden con el origen en celdas concretas.
- **`ingested_forecast_length` es NaT** en todo el archivo: no sirve para validar completitud. El campo `n_nan_fraction` por pasada queda en el JSON (0,0 en las 7 pasadas) y F1.6 debe validar NaN por paso.
- **Bronce con eje `init_time` prerreservado** en lugar de redimensionar: simplifica el relleno hacia atrás y la idempotencia; coste: el array es de 1370 pasadas aunque se escriban pocas (los chunks no escritos no ocupan disco).
- **Velocidad tras interpolar u/v por miembro**, no interpolando la velocidad; el orden cambia el valor porque ambas operaciones no conmutan. El test `test_speed_per_member_differs_from_speed_of_mean` fija la diferencia con la velocidad de la media (3 frente a 0 m/s en un caso sintético).
- **Std muestral (ddof=1)** y cuantiles lineales de `numpy` sobre 51 miembros; el control es el miembro 0 (comprobado: `ensemble_member` 0..50).
- **Escritura en NFS** (`/mnt/data` es `nas:/mnt/main/share/data`): icechunk avisa de que el almacenamiento local no es seguro para commits concurrentes; el pipeline usa un único escritor. Hay que mantenerlo así o pasar a un almacenamiento de objetos.
- **Centroides provisionales** (`config/centroids_provisional.yaml`, eliminado): solo valían para validar la cadena; sustituidos por los de MaStR (§7).
- **Sin cambios en `ingest/ifs/`**; los 28 tests unitarios pasan (16 previos + 12 nuevos en `tests/unit/test_dynamical_ingest.py`). `ruff` y `pyright` limpios sobre el paquete nuevo.
- **No verificado:** coherencia con el GRIB `enfo` y el ciclo de IFS por pasada; están en el backlog de GitHub.


## 7. Reconstrucción de la plata desde el bronce

Los centroides reales de MaStR (`config/centroids.yaml`, 21 puntos: 16 terrestres + 5 marinos) sustituyen a los provisionales. La plata se recalcula **solo desde el bronce** (sesión Icechunk de solo lectura, sin tocar dynamical) con `build_table`/`write_silver` (misma lógica: bilineal por miembro, velocidad por miembro, agregados):

```
uv run python -m predico50hz.ingest.dynamical.rebuild_silver \
    --start 2025-09-01 --end 2026-10-15 \
    --centroids config/centroids.yaml --silver data/silver/ifs_ens_clusters --workers 4
```

- Pasadas independientes calculadas en hilos (`--workers`); `--snapshot-id` fija el snapshot de bronce; las pasadas no ingeridas se saltan.
- Cada Parquet guarda en sus metadatos `centroids_file`, `centroids_sha256`, `bronze_snapshot_id` y `bronze_digest` (hash de los valores de la pasada). **Idempotente**: si el fichero ya lleva el mismo hash de centroides y de bronce se omite (`--force` lo regenera); si cambian los centroides, se recalcula.
- Nuevos valores por defecto en `config.py`: `DEFAULT_CENTROIDS = config/centroids.yaml` y `DEFAULT_SILVER = data/silver/ifs_ens_clusters`; la ingesta diaria no cambia. La plata provisional `data/silver/ifs_ens_points/` queda intacta.
- Columnas extra del YAML (`kind`, `capacity_mw`, `n_units`): **no** se llevan a la plata (`load_points` solo expone `kind` y el esquema fijo de la plata no lo incluye; se recuperan uniendo por `point_id` con el YAML).
- El validador (`validate.py`) toma `n_puntos` de `--centroids` (por defecto el mismo fichero): filas esperadas = n_puntos × 13.

**Resultado** (410 pasadas, 2025-09-01 a 2026-10-15; las de la etapa 1 están dentro de ese rango, no hay otras en bronce): reconstrucción completa 54,0 s con 4 hilos (0,13 s por pasada); relanzada sin cambios 21,4 s (410 omitidas por hash). Validador sobre el mismo rango: 410/410 cubiertas, cobertura 100 % (umbral 98 %), 1 min 50 s. Comprobación manual: la plata coincide con una bilineal hecha a mano desde el bronce (ver el informe de la tarea; errores del orden de 1e-6 por el redondeo a float32).
