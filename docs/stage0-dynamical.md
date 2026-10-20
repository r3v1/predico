# Etapa 0 — Metadatos de dynamical.org (IFS ENS 0,25°)

Fecha de la comprobación: 2026-10-16, ~07:55 UTC. Datos completos en `data/stage0/dynamical_check.json`.

## 1. Dataset

| Campo | Valor medido |
|---|---|
| href (STAC `icechunk-https`) | `.../ecmwf-ifs-ens-forecast-15-day-0-25-degree/v0.1.0.icechunk`, `dataset_version` = 0.1.0 |
| Snapshot de `main` al comprobar | `2RQMR16TJ5RH8EFE53RG` (2026-10-15 08:17:57 UTC); ramas: solo `main`; tags: ninguno |
| Dimensiones | init_time 928, lead_time 85, ensemble_member 51, latitude 721, longitude 1440 |
| `init_time` | 2024-04-01 a 2026-10-15, **928 pasadas 00Z cada 24 h exactas, 0 huecos** |
| `lead_time` | 0 a 360 h: cada 3 h hasta 144 h y cada 6 h después |
| Malla | latitud **descendente** (90 a -90), longitud -180 a 179,75 |
| Zarr | chunk (1, 85, 51, 32, 32) dentro de shards (1, 85, 51, 320, 320); Blosc zstd nivel 3; float32 |
| `ingested_forecast_length` | **NaT en las 928 pasadas: inutilizable** para detectar completitud (`expected_forecast_length` = 15 d en todas) |

Variables presentes (19): las 10 del PLAN 2.2 (`wind_u/v_100m`, `wind_u/v_10m`, `wind_gust_10m`, `temperature_2m`, `pressure_surface`, `temperature_925hpa`, `temperature_850hpa`, `pressure_reduced_to_mean_sea_level`) más `geopotential_height_850hpa/925hpa/500hpa`, `dew_point_temperature_2m`, `total_cloud_cover_atmosphere`, `precipitation_surface`, `categorical_precipitation_type_surface` y dos radiaciones de superficie. No hay u/v en niveles de presión. Unidades: temperaturas en °C, presiones en Pa, viento en m/s. `wind_gust_10m` es el máximo desde el paso anterior (`step_type` = max).

## 2. Cobertura en el dominio (49,5–56,0° N, 8,5–16,0° E)

Método: miembros 0–50 y pasos +18..+54 h, sobre latitud 56,0–50,25° N y longitud 8,5–16,0° E (`wind_u/v_100m`, `wind_gust_10m`) u 8,5–11,75° E (resto). Muestreo semanal para `wind_u/v_100m` y `wind_gust_10m` (134–137 pasadas) y cada 28 días para el resto (35 pasadas), con bisección en cualquier cambio de la fracción de NaN. Transferencia de una pasada de muestreo: ~2,9 GB (medido en `/proc/net/dev`). **Limitación:** solo se han mirado las pasadas muestreadas y esa subventana; la validación diaria (F1.6) debe revisar todas.

| Variable | Pasadas muestreadas | NaN > 0 | Rango observado |
|---|---|---|---|
| `wind_u_100m` / `wind_v_100m` | 134 | 0 (desde 2024-04-01) | -22,5..30,0 / -29,8..30,5 m/s |
| `wind_u_10m` / `wind_v_10m` | 35 | 0 | -20,5..26,3 / -17,3..26,8 m/s |
| `temperature_2m`, `_925hpa`, `_850hpa` | 35 | 0 | -16,3..32,3 / -12,4..25,1 / -13,6..19,0 °C |
| `pressure_surface`, `pressure_reduced_to_mean_sea_level` | 35 | 0 | 91 104..103 936 / 96 512..104 000 Pa |
| `geopotential_height_850hpa` | 35 | 0 | 1021..1660 m |
| **`wind_gust_10m`** | 137 | **100 % NaN hasta 2024-11-12; 0 % NaN desde 2024-11-13** | 0,11..41,5 m/s |

Conclusión sobre la racha: igual que en los GRIB abiertos, la racha existe desde el **2024-11-13** (transición exacta por bisección: 2024-11-12 todo NaN, 2024-11-13 completo). Las primeras 226 pasadas (2024-04-01 a 2024-11-12, ~7,4 de los 30 meses) no tienen racha: el modelo debe tratarla como opcional o usar un proxy (máximo de u10/v10) para el entrenamiento del tramo previo. El viento a 100 m **sí** cubre desde el primer día del archivo.

## 3. Latencia de publicación (últimas 3,5 semanas)

`repo.ancestry("main")` da 1044 snapshots desde 2026-09-22. Cada día hay una **única ráfaga** en la que se publica la pasada 00Z completa: un snapshot `Expand dataset` (~08:05 UTC, añade el `init_time` con NaN) seguido de ~38 snapshots `Update worker N` (uno por bloque de trabajo) y uno final `Update at ...`. No hay publicación parcial por pasos: los 85 pasos se escriben a la vez, y en la prueba del 15 de octubre un chunk de `wind_u_100m` de esa pasada seguía 100 % NaN hasta el penúltimo snapshot (08:17:47) y estaba 100 % completo desde él. **La pasada se considera disponible en el snapshot final `Update at`.** (`ingested_forecast_length` no sirve: es NaT.)

| Estadístico (24 días, 2026-09-22 a 2026-10-15) | Hora UTC del snapshot final |
|---|---|
| Mínimo | 08:15:49 (10-07) |
| Mediana | ~08:18 |
| Máximo | 08:22:55 (10-12) |

Detalle por día en el JSON. Cada día se reescribe también la pasada anterior (se vio en el diff de chunks) y hay ráfagas extra (snapshots finales a las 12:04 del 10-01, 11:49 del 10-02, 08:33 y 11:45 del 10-12 UTC) que reprocesan la pasada del día previo. En las dos comprobaciones hechas (pasadas 2026-10-14 y 2026-10-11, un chunk de `wind_u_100m`) el contenido es **idéntico bit a bit** antes y después (esta comparación y la de completitud de arriba se hicieron en una ventana lon -1,5..4 E por un error de índices, corregido después en el muestreo de cobertura), pero no se ha verificado para todas las variables ni chunks; la reescritura no se puede descartar como fuente de cambios en el histórico.

**Margen frente al cierre.** Cierre de 13:00 UTC (verano) o 14:00 UTC (invierno), margen mínimo exigido 60 min:

| Cierre | Peor caso medido (08:23) | Margen | ¿Cumple 60 min? |
|---|---|---|---|
| 13:00 UTC | 08:23 | 4 h 37 min | Sí |
| 14:00 UTC | 08:23 | 5 h 37 min | Sí |

Con ese margen queda mucho espacio para reintentos. El respaldo `enfo` solo hace falta si falla la publicación (no se observó ningún día sin pasada en las 3,5 semanas). Referencia GRIB: el `.index` HRES 00Z del 15 oct apareció a las 07:34 UTC, unos 45 min antes de que dynamical complete la pasada.

## 4. Hallazgos que afectan al diseño

1. Latitud descendente: hay que ordenar el eje antes de la interpolación bilineal (`extract.py` HRES asume ascendente).
2. El shard (320×320) contiene chunks internos de 32×32 que se leen por rango; leer un punto cuesta el chunk entero con 51 miembros y 85 pasos: ~5 MB comprimidos por variable y chunk (medido), así que las 10 variables del dominio (4 chunks de 32×32) rondan 200 MB por pasada. Se mide con precisión en la Etapa 1.
3. `ingested_forecast_length` es NaT: la completitud se debe validar mirando los datos (p. ej. NaN en el último paso).
