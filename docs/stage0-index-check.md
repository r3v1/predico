# Etapa 0: comprobación de los `.index` de ECMWF Open Data

Fecha de ejecución: 2026-10-16. Fuente de todos los datos: `data/stage0/index_check.json`, generado por `scripts/stage0_index_check.py` (solo listados S3 y ficheros `.index`; el único acceso a un GRIB2 fue una petición HEAD para leer su tamaño, sin descargar contenido). Total: 411 peticiones. El dato de `Last-Modified` (apartado 7) procede de una petición HEAD aparte.

Fechas inspeccionadas (pasada 00Z, `oper`, `fc`): 2023-01-18, 2024-05-15 y 2026-10-15. A las 06:22 UTC de hoy la 00Z del 2026-10-16 aún no estaba publicada (`20261016/00z/ifs/` vacío, solo `aifs-*`), así que se usa **ayer, 2026-10-15**.

## 1. Reglas de ruta por fecha

Base: `https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/`. Fichero: `{AAAAMMDD}{HH}0000-{paso}h-{stream}-fc.{grib2|index}`.

| Fecha | Directorio de la 00Z `oper` |
|---|---|
| 2023-01-18 | `20230118/00z/0p4-beta/oper/` (sin prefijo `ifs/`) |
| 2024-05-15 | `20240515/00z/ifs/0p25/oper/` y también `…/ifs/0p4-beta/oper/` (conviven) |
| 2026-10-15 | `20261015/00z/ifs/0p25/oper/` |

Regla del resolvedor: listar `{fecha}/{HH}z/`; si existe `ifs/`, listar dentro para ver las resoluciones; si no, las resoluciones cuelgan directamente de la pasada. Preferir `0p25` si existe y caer a `0p4-beta` si no.

- **Cambio de resolución (acotado con listados, búsqueda binaria):** el último día sin `0p25` es **2024-01-31** y el primero con `0p25` es **2024-02-01**. Ese día aparecen `0p25` y `0p4-beta`; en 2024-01-31 solo existe `0p4-beta`. En 2024-05-15 `0p4-beta` sigue publicándose, pero sin `100u`/`100v` (ver 3).
- **Prefijo `ifs/`:** ausente el 2023-01-18, presente el 2024-03-01 (listado de comprobación) y el 2024-05-15. **Fecha exacta de aparición: pendiente** (no acotada). El 2024-02-01 aparece `0p25/` sin `ifs/` y el 2024-03-01 con `ifs/`, así que el cambio cae entre ambas fechas.
- **Directorios hermanos nuevos:** el 2024-05-15 hay `aifs/` y el 2026-10-15 `aifs-single/` y `aifs-ens/`.
- **Hoy:** la 00Z del 2026-10-16 todavía no tenía `ifs/` a las 06:22 UTC.

## 2. Campos: matriz por fecha

Presencia comprobada en los 14 `.index` leídos por fecha (paso 0 y pasos 18 a 54 cada 3 h). El resultado es idéntico en todos los pasos de cada fecha.

| Campo | 2023-01-18 (0p4-beta) | 2024-05-15 (0p25) | 2026-10-15 (0p25) |
|---|---|---|---|
| `10u`, `10v`, `2t`, `sp` | presente | presente | presente |
| `u`, `v`, `t` a 1000, 925 y 850 hPa | presente | presente | presente |
| `100u`, `100v` | **ausente** | presente | presente |
| `10fg` | **ausente** | **ausente** | presente (nombre real: `10fg`) |
| `lsm` | **ausente** (tampoco en paso 0) | presente | presente |
| Campos presentes de los 15 | 11 | 14 | 15 |

Nombre alternativo de la racha: ninguno. Se buscó cualquier parámetro que contenga `fg` o `gust` y solo aparece `10fg` (2026). En 2023 y 2024 no hay ningún parámetro de racha en el `.index`. En 2026 `10fg` está en los 14 pasos leídos.

Observación: el `0p4-beta` del 2024-05-15 carece también de `100u` y `100v` (y de `10fg`), mientras que su `0p25` sí tiene `100u/100v`.

## 3. Profundidad efectiva de los campos que faltan

Búsqueda binaria sobre fechas, un `.index` (paso 18, 00Z) por fecha. Supone que la presencia es monótona; la traza completa está en el JSON.

| Campo | Último día ausente | Primer día presente |
|---|---|---|
| `lsm` | 2023-03-29 | **2023-03-30** |
| `100u`, `100v` | 2024-03-05 | **2024-03-06** |
| `10fg` | 2024-11-12 | **2024-11-13** |

Consecuencias para el plan:

- `100u`/`100v` (variable principal) solo existen desde 2024-03-06: unos 2,6 años de histórico, no 3,75.
- `10fg` solo desde 2024-11-13: unos 1,9 años.
- `lsm` es estático: basta tomarlo de cualquier día posterior a 2023-03-30. Con el muestreo por puntos no depende de la resolución.
- Antes del 2024-03-06 hay que reconstruir la altura de buje con `10u/10v` más los niveles de presión, o excluir ese periodo.

## 4. Bytes por paso y por pasada (15 campos frente a GRIB2 completo)

Suma de `_length` de los mensajes de los campos pedidos, frente al `Content-Length` del GRIB2 completo. En 2023 y 2024 la suma cubre solo los campos presentes (11 y 14).

| Fecha | MB por paso (15 campos), rango en pasos 18-54 | GRIB2 completo por paso (MB) | Pasada 00Z, 13 pasos: campos / total | Fracción |
|---|---|---|---|---|
| 2023-01-18 | 7,92 (constante) | 52,0 | 102,9 MB / 675,6 MB | 15,2 % |
| 2024-05-15 | 12,24 a 12,62 | 116,5 a 118,9 | 161,0 MB / 1533,0 MB | 10,5 % |
| 2026-10-15 | 13,48 a 13,82 | 145,9 a 149,5 | 177,6 MB / 1920,9 MB | 9,25 % |

Nota: la suma incluye `lsm` en cada paso; en descarga real `lsm` se pide una sola vez, y es menos de 1 MB por paso. El paso 0 no entra en las sumas (12,3 MB en 2026).

## 5. Extrapolación del histórico (00Z, pasos 18 a 54)

Rangos de días: 2023-01-18 a 2026-10-15, 1367 días. Se aplica el volumen medido de cada fecha a su era: A (0p4-beta) 379 días hasta 2024-01-31, B (0p25 sin racha) 286 días hasta 2024-11-12, C (0p25 con racha) 702 días.

| Era | Días | MB/pasada | Total |
|---|---|---|---|
| A: 2023-01-18 a 2024-01-31 | 379 | 102,9 | 39,0 GB |
| B: 2024-02-01 a 2024-11-12 | 286 | 161,0 | 46,0 GB |
| C: 2024-11-13 a 2026-10-15 | 702 | 177,6 | 124,7 GB |
| **Total 00Z** | 1367 | | **≈ 210 GB** |

- Es un orden de magnitud con 3 muestras: la era C usa la muestra de 2026 para todo el periodo, y 2023 subestima (faltan 4 campos que en 0p4-beta pesarían algo más). Los tamaños por día varían unos ±3 %.
- Alternativa sin selección por rangos: GRIB2 completos serían unos 256 + 438 + 1348 ≈ 2,0 TB para la 00Z. El `.index` reduce el tránsito a ≈ 10 %.
- Este cálculo supone que existen todos los días y que 2023 usa la resolución `0p4-beta`. No se ha verificado la ausencia de días sueltos.
- Hay un hueco de la Etapa 2: los 13 últimos meses (desde 2025-09-01) caen íntegramente en la era C: 410 días × 177,6 MB ≈ 73 GB.

## 6. Pasada 06Z (y 12Z y 18Z)

| Fecha | 06Z | 12Z | 18Z |
|---|---|---|---|
| 2023-01-18 | stream `scda`, pasos 0 a 90 h cada 3 h | `oper`, hasta 240 h | `scda`, hasta 90 h |
| 2024-05-15 | `scda` (0p25 y 0p4-beta), pasos 0 a 90 h | `oper`, hasta 240 h | `scda`, hasta 90 h |
| 2026-10-15 | **`oper`**, 49 pasos, hasta 144 h | `oper`, hasta 360 h | `oper`, hasta 144 h |

- Para el experimento 06Z (+12 a +48 h, 13 pasos) bastan los 90 h de `scda` en 2023-2024 y los 144 h de `oper` en 2026. Los pasos 12, 18, 36 y 48 existen en todos los casos.
- El stream de 06Z/18Z pasa de `scda` a `oper` entre 2024-05-15 y 2026-10-15; **fecha exacta del cambio: pendiente** (no acotada).
- Campos en 06Z: mismo patrón que 00Z. 2023 sin `100u`/`100v`/`10fg`/`lsm`; 2024 `0p25` sin `10fg`; 2026 completos. En `0p4-beta` de 2024 faltan además `100u/100v`.
- Volumen estimado (13 pasos; media de los 4 pasos leídos: 12, 18, 36 y 48): 2023 ≈ 102,9 MB, 2024 `0p25` ≈ 162,5 MB, 2026 ≈ 175,8 MB por pasada. Por era, con los mismos días que en el apartado 5: ≈ 39,0 + 46,5 + 123,4 ≈ **209 GB** para toda la 06Z. Es una estimación (4 muestras por fecha).

## 7. Publicación de la 00Z

- El `.index` de `20261015/00z/ifs/0p25/oper/` paso 18 tiene `Last-Modified: Thu, 15 Oct 2026 07:34:06 GMT` y el de paso 54 `07:34:10 GMT`. La 00Z de ese día queda disponible hacia las **07:34 UTC**. Es una sola muestra: hay que medir más días para F1.5.
- A las 06:22 UTC del 16-oct la 00Z de hoy aún no estaba.

## 8. Pendientes y notas

- Fecha de aparición del prefijo `ifs/` (entre 2024-02-01 y 2024-03-01): sin acotar.
- Fecha del cambio de stream 06Z/18Z `scda` a `oper`: sin acotar.
- Durante la ejecución S3 devolvió 503 «Slow Down» de forma intermitente; el script reintenta con espera creciente, hace las peticiones en serie con 0,4 s de pausa y cachea listados e índices.
- No se ha comprobado ningún día intermedio salvo los de las bisecciones (la traza queda en el JSON).
