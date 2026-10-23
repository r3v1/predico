# F2 — Ingesta de ENTSO-E (generación, capacidad, precio y pronóstico del operador)

Cubre F2.2, F2.3, F2.4, F2.5 y F2.7 (no F2.6, MaStR). Descarga completa del 2026-10-16. Informes de validación en `data/validation/entsoe_<dataset>.json`.

## 1. Arquitectura

Paquete `src/predico50hz/ingest/entsoe/`, lanzado con `uv run python -m predico50hz.ingest.entsoe --dataset generation --start 2015-01-01` (`--dataset all` encadena los cuatro; `--days N` descarga solo los últimos N días, para el modo diario; `--resume <fetched_at>` continúa una descarga interrumpida; `--no-fetch` reconstruye plata e informe sin red). Todo en UTC.

| Módulo | Función |
|---|---|
| `redact.py` | Borra el token de textos, excepciones y logs: expresión regular sobre `securityToken=`, más el valor literal registrado, más una fábrica de `LogRecord` que redacta todo registro (cubre `httpx`, `httpcore` y `entsoe`) |
| `client.py` | Cliente `httpx` mínimo que devuelve **bytes crudos** (XML o ZIP). Separa peticiones ≥ 0,2 s (300/min, bajo el límite de ~400/min), reintenta con backoff exponencial (1–60 s, respeta `Retry-After`) en 429/5xx y errores de red, y distingue "sin datos" (el `Acknowledgement` de 200 con "No matching data found") de un fallo. Todo error sale redactado y con `from None` |
| `datasets.py` | Catálogo: parámetros de la API, variantes (`psrType` B18/B19), inicio de historia y troceado (mes o año natural) |
| `bronze.py` | Respuesta cruda por descarga: `data/bronze/entsoe/<dataset>/fetched_at=<AAAAMMDDTHHMMSSZ>/<variante>_<inicio>_<fin>.xml.gz` (los ZIP se guardan tal cual; una ventana sin datos deja un `.nodata` vacío; `_run.json` con las métricas). Escritura atómica; nada se sobrescribe |
| `pipeline.py` | Troceado, reanudación (salta lo ya presente en esa descarga), recuperación en la zona DE-LU y métricas |
| `parse.py` | XML → Polars (serie, resolución, posición → hora UTC, `createdDateTime`, `revisionNumber`); expande las series A03 |
| `silver.py` | Bronce → Delta Lake (`data/silver/entsoe/<dataset>`) con `deltalake` + Polars; gana la descarga más reciente por clave; escritura idempotente |
| `validate.py` | Esquemas Pandera (backend Polars) e informe JSON |
| `../../settings.py` | `pydantic-settings`: `ENTSOE_API_KEY` desde `.env` como `SecretStr` |

**Por qué no `EntsoeRawClient`.** Devuelve `response.text` (rompe los ZIP, que la API usa si una consulta abarca varios documentos), no reintenta por código HTTP y deja `securityToken=` en las excepciones. Se usa la misma API REST y los mismos parámetros, y `entsoe-py` solo se usó para contrastar (§4). El parseo se hace sobre el XML propio.

**Seguridad del token.** Nunca se imprime ni se escribe. Se ha comprobado que el valor no aparece en `data/`, `src/`, `tests/`, `docs/` ni en los logs de las ejecuciones. Tests: redacción de URL, de valor literal suelto y de logs; excepciones de conexión y de HTTP 400 sin token ni cadena de excepciones; bronce sin token.

## 2. Datasets, cobertura y huecos

| Dataset | Petición | Rango en plata | Filas | Plata |
|---|---|---|---|---|
| `generation` | A75/A16, B18 y B19, `10YDE-VE-------2` | 2015-01-01 00:00 → 2026-10-16 12:15 UTC | 413 426 | malla 15 min |
| `capacity` | A68/A33, B18 y B19, `10YDE-VE-------2`, anual | 2015–2026 | 24 | 12 años × 2 tipos |
| `prices` | A44, `10Y1001A1001A82H`, posición 1 (SDAC) | 2018-09-30 22:00 → 2026-10-17 21:45 UTC | 98 040 | hora o cuarto de hora nativos |
| `forecast` | A69/A01 (day-ahead), B18 y B19, `10YDE-VE-------2` | 2015-01-01 → 2026-10-16 21:45 UTC | 826 832 (413 464 B19 + 413 368 B18) | 15 min |

**Generación (F2.2).** La plata tiene `ts_utc`, `wind_offshore_mw`, `wind_onshore_mw`, `wind_mw` (suma), y por serie `*_src` (`original`, `a03`, `hourly`, `gap`; nulo antes del primer dato) y `*_gap_len` (longitud de la racha rellenada, 0 si no hay relleno), más `is_gap_filled`. Resultado: **cero huecos reales** en 11,8 años (413 426 pasos sin un solo `gap`), malla de 15 min completa (Pandera `full_grid`), sin duplicados, sin nulos en `wind_mw`.

Lo que sí hay son valores **omitidos por la propia API** (`curveType` A03: "bloques de tamaño variable", el valor no se repite si no cambia respecto a la posición anterior). Se materializan arrastrando el último valor dentro del periodo y se marcan `a03`: eólica marina 19 367 pasos (4,7 %; 19 % en 2015, 5–6 % en 2016–17, ~2 % desde 2023, típico de periodos de producción nula o constante), terrestre 146 pasos (0,04 %). Es exactamente la semántica del arrastre del último valor, así que la reconstrucción "como Predico" se cumple sin tener que inventar nada. **Suposición:** el repositorio no documenta cómo lo hace Predico más allá de "arrastre del último valor" (ADR 0013); se aplica la misma regla a los huecos entre documentos (`gap`) y, si apareciera una serie horaria, se repite cada valor en sus 4 cuartos (`hourly`). La rutina existe y está probada, pero no se ha activado nunca con datos reales. La malla termina en el mínimo de las dos últimas marcas (no se arrastra por delante del último dato real).

**Resolución.** La generación y el pronóstico son **PT15M en toda la historia** (2015 → hoy; se comprobó por mes y serie, `resolution` del informe). No hay tramo horario. El precio sí cambia: **PT60M hasta 2025-09-30 (61 368 horas) y PT15M desde 2025-10-01 (36 672 cuartos)**, coincidiendo con el paso de SDAC a 15 min. La plata de precios guarda la resolución nativa (`resolution_min`); la unión as-of (F3.1) debe tratar esa columna.

**Precios (F2.4).** Se pide solo la posición 1 (SDAC). Sin filtro la API devuelve además la posición 2, la subasta de 15 min de EPEX para Alemania, con valores distintos y más volátiles; se descarta y queda anotado aquí. Desde 2018-10-01 no hay huecos; rango −500,0 a 936,3 EUR/MWh, 4 080 observaciones negativas. La última marca es mañana: a las 13:20 UTC del 2026-10-16 ya estaba publicado el día siguiente completo. La plata guarda además `first_seen_at` (mínimo `fetched_at` del bronce de cada marca, como el pronóstico; todo lo anterior al 2026-10-16 12:56 UTC vale esa primera descarga y está censurado). Desde 2024-03-30 no hay huecos ni mezcla de zonas. El bloque `price` de la tabla de variables la usa (`docs/f3-features.md` §9).

**Capacidad (F2.3).** Hay datos directamente en el área de control; no hizo falta la zona DE-LU (la recuperación existe y está probada, columna `area`).

| Año | Marina (MW) | Terrestre (MW) |
|---|---|---|
| 2015 | 48 | 14 749 |
| 2016 | 336 | 15 835 |
| 2017 | 336,3 | 16 900 |
| 2018 | 336 | 17 939 |
| 2019 | 1 068 | 18 425 |
| 2020 | 1 068 | 18 809 |
| 2021 | 1 080 | 19 272 |
| 2022 | 1 093 | 19 735 |
| 2023 | 1 093 | 20 756,4 |
| 2024 | 1 350 | 20 794 |
| 2025 | 1 828 | 21 617 |
| 2026 | 1 828 | 22 629 |

**Pronóstico del operador (F2.5).** Un hueco: B18, 2015-01-04 (96 pasos, un día completo, sin valor). B19 sin huecos. Con la serie real de 2024–2026, `wind_mw` frente a la suma de pronósticos B18+B19: error absoluto medio 587 MW sobre una media de 4 512 MW (13 %), sesgo +57 MW. Es solo orientativo (benchmark, sin ajustar a horizonte).

## 3. Hora de publicación del pronóstico del operador

**`createdDateTime` no es la hora de publicación.** Es la hora en que ENTSO-E genera la *respuesta* a la consulta: en las 284 ventanas descargadas (2015 a 2026) vale entre 2 s y 20 min después del arranque de la descarga, es decir, la hora de la propia petición, también para datos de 2015. `revisionNumber` vale **3 en el 100 % de los documentos** del pronóstico, de modo que tampoco discrimina. Se guardan ambos (`doc_created`, `doc_revision`) para que quede constancia, pero no sirven para fijar la disponibilidad ni para el test anti look-ahead (ADR 0009).

**Lo único medible es la primera vez que se ve un valor** (`first_seen_at` en plata = mínimo `fetched_at` del bronce en que aparece la clave). Al principio hubo una sola descarga completa, así que todo valía 2026-10-16 12:56 UTC. Desde entonces la VM descarga cada hora y la serie del 15 al 23 oct ya está medida (bloque final de esta sección). Medición puntual: a las **13:20 UTC (15:20 CEST) del 16 de octubre el pronóstico day-ahead del día siguiente aún no existía** (el último valor era 2026-10-16 21:45 UTC, y la API sí tenía los precios del 17). Con el cierre a las 13:00 UTC en verano o 14:00 UTC en invierno, **el pronóstico del operador no está disponible antes del cierre**: solo sirve como benchmark, nunca como variable (coherente con el ADR 0017). Para fijar la hora real hay que ejecutar `--dataset forecast --days 2` **cada hora** (cron o timer) durante unas semanas; el `first_seen_at` resultante tendrá resolución horaria. Esa medición ya corre en la VM (resultado en el bloque final). Limitación: se mide cuándo lo vimos nosotros, no cuándo lo publicó el TSO (cota superior), y para el histórico anterior a la primera descarga no hay ningún dato.

**Generación real publicada antes del cierre (#11).** Cada ejecución de `entsoe-hourly` consulta además la generación real (A75/A16) de B18 y B19 de las últimas 36 h y anota en `data/ops/generation_lag.jsonl` la observación `(observed_at, last_step)`; `last_step` es el inicio del último cuarto de hora con dato en ambos tipos (el menor de los dos; los puntos rellenados por arrastre no cuentan). La consulta no hace fallar el job: si falla, queda en `details.generation_error` de `runs.jsonl`. `publication-times` añade a `publication_times.json` el bloque `generation_lag`: retraso `observed_at − last_step` (mediana y peor caso) y, por día de cierre, `cierre − last_step` según la última observación anterior al cierre (15:00 de Berlín; se descartan las de más de 90 min antes). Es el dato que fija hasta qué cuarto de hora puede apoyarse la persistencia de F5. Prueba puntual del 2026-10-21: a las 06:25 UTC el último cuarto de hora publicado empezaba a las 05:45 UTC (unos 40 min). Además, `publication-times` reconstruye observaciones equivalentes a partir del bronce de generación: cada carpeta `fetched_at=` cuya ventana llega hasta el día de la descarga da `observed_at = fetched_at` (`observations_from_bronze`). Si coinciden en el minuto, gana la consulta (`merge_observations`), y cada observación lleva `source` (`probe` o `bronze`). El bloque añade `n_by_source` y `near_closure`, con las observaciones de las 2 h previas al cierre de su día.

**Resultado del 16 al 22 oct 2026** (portátil: 35 observaciones, 10 del bronce y 25 de consultas; VM: 8, 7 del bronce y 1 consulta):

| | n | Mediana | Peor caso |
|---|---|---|---|
| Todas las observaciones (portátil) | 35 | 33 min | 166 min |
| En las 2 h previas al cierre | 5 | 39 min | 61 min |
| Descargas de las 06:00 UTC de la VM (17–22 oct) | 6 | 31 min | 32 min |

- **Lo normal: unos 30–35 min.** Es el cuarto de hora más 15–20 min de publicación.
- **El 22 oct hubo un atasco** alrededor del cierre: a las 11:01 UTC el retraso era de 61 min, a las 13:01 de 76 (último cuarto publicado, 11:45), a las 14:39 de 160 y a las 15:01 de 166 (12:15). A las 16:01 bajó a 91 min, a las 17:00 a 46 y a las 18:32 a 33. Si el atasco hubiera empezado una hora antes, en el cierre faltarían unas 2 h 45 min de datos.
- **`at_closure` exagera unos 60 min**, porque la consulta horaria de las 13:00 UTC llega justo después del cierre: se usa la de las 12:00 (21 oct: 90 min; 22 oct: 120 min; 16 oct, con la descarga de las 12:53: 45 min). Una consulta extra unos minutos antes del cierre la afinaría.
- **Decisión para la persistencia (F5, `docs/f4-backtest.md` §8.3): margen de 180 min.** Solo se usan cuartos de hora que empiezan como muy tarde a `cierre − 180 min`. Cubre un atasco como el del 22 oct y, con horizontes de 10 a 34 h, apenas cuesta error.
- **Limitación:** es una semana de octubre y la resolución de las consultas es horaria. La medición sigue corriendo en la VM, y si aparece un retraso mayor habrá que revisar el margen.

**Precio y pronóstico del operador, cierres del 15 al 23 oct 2026 (#10, datos de la VM).** Las horas son UTC y la resolución es horaria: cada hora se anota la primera descarga que ve el dato completo, así que la hora medida es una cota superior. El cierre de Predico fue a las 13:00 UTC (15:00 de Berlín, horario de verano). El margen es el cierre menos la hora medida; si es negativo, el dato llegó después del cierre.

| Cierre (día D) | Objetivo (D+1) | Precio D+1: primera vez visto | Margen (min) | Pronóstico operador: primera vez visto | Margen (min) |
|---|---|---|---|---|---|
| 15 oct | 16 oct | censurado | — | censurado | — |
| 16 oct | 17 oct | censurado | — | censurado | — |
| 17 oct | 18 oct | 12:00 | +60 | 17:00 | −240 |
| 18 oct | 19 oct | 13:00 | −0 | 17:00 | −240 |
| 19 oct | 20 oct | 12:00 | +60 | 17:00 | −240 |
| 20 oct | 21 oct | 19:00 | −360 | 17:00 | −240 |
| 21 oct | 22 oct | 12:00 | +60 | 17:00 | −240 |
| 22 oct | 23 oct | 13:00 | −0 | 17:00 | −240 |
| 23 oct | 24 oct | sin publicar | — | sin publicar | — |

**Resumen.**

- **Precio (6 medidos):** primera vez visto entre las 12:00 y las 19:00 UTC, mediana 12:30. Antes del cierre, 3 de 6 (50 %). Margen mediano +30 min; peor caso −360 min (20 oct).
- **Pronóstico del operador (6 medidos, 17–22 oct):** todos a las 17:00 UTC (entre 17:00:19 y 17:00:32), margen −240 min. Antes del cierre, 0 %.
- **Generación real (#11):** 36 observaciones (8 del bronce, 28 consultas). Retraso mediano 31 min y peor caso 166 min. En las 2 h previas al cierre (3 observaciones), mediana 32 min y peor caso 61 min. Coincide con lo anterior: el margen de 180 min no cambia.

**Conclusiones.**

- El pronóstico del operador llega siempre hacia las 17:00 UTC, cuatro horas después del cierre. Solo sirve como referencia (ADR 0017).
- El precio de D+1 estuvo publicado antes del cierre en 3 de 6 días, y el 20 oct llegó a las 19:00 UTC. Con la regla del ADR 0009 (solo entra lo que llega antes del cierre en el peor caso medido), no entra como variable. La decisión está en el ADR 0031 (#12).
- El precio de D, publicado la víspera, sí está siempre antes del cierre.

**Limitación.** Es una semana de octubre con resolución horaria. Los días 18 y 22 «−0 min» significan que a las 12:00 UTC aún no estaba y se vio en la descarga de las 13:00, justo después del cierre. Los días 15 y 16 no tienen una descarga anterior que los viera incompletos, así que no entran en el resumen. El 23 oct aún no estaba publicado en la última descarga copiada.

## 4. Comprobación de coherencia

Suma B18+B19 de la plata frente a `EntsoePandasClient.query_generation` (columnas "Wind Offshore" y "Wind Onshore", "Actual Aggregated"), día completo (96 cuartos) en tres fechas: 2016-03-10, 2021-07-04 y 2026-09-30. Diferencia máxima 2·10⁻¹² MW (redondeo de coma flotante) en las tres. Valores de ejemplo, 2016-03-10 10:00 UTC: marina 130,42 + terrestre 837,18 = 967,60 MW, igual en ambas fuentes; 2026-09-30 10:00 UTC: 585,55 + 3 804,78 = 4 390,33 MW.

La capacidad anual de ENTSO-E **no cubre la generación observada** en varios años (ratio máximo generación/capacidad marina: 6,9 en 2015, 1,6 en 2017, 3,1 en 2018, 1,18 en 2023, 1,10 en 2024 y 2026; terrestre siempre ≤ 0,85). La capacidad anual declarada va retrasada respecto a las altas de parques durante el año. Hallazgo para F3.3: el factor de capacidad con esta capacidad **superaría 1** en la marina; hace falta MaStR con fecha de alta (F2.6) y no usar la capacidad anual de ENTSO-E como denominador.

## 5. Métricas de descarga

| Dataset | Peticiones | Raw | Bronce (gzip) | Tiempo | Reintentos |
|---|---|---|---|---|---|
| `generation` | 284 (58 + 226 en la reanudación) | ~120 MB (85 MB solo en la reanudación) | 4,9 MB la reanudación | 815 s la reanudación | 0 |
| `prices` | 97 | 16,6 MB | 0,73 MB | 146 s | 0 |
| `forecast` | 284 | 117,6 MB | 6,2 MB | 1 183 s | 0 |
| `capacity` | 24 | 0,04 MB | 0,015 MB | 58 s | 0 |

Bronce total: 14 MB (compresión ~19×). Plata total: 53 MB. Ritmo: 0,5–1 petición/s con tres descargas simultáneas (≈ 3 s/petición, limitado por la latencia del servidor y no por el límite de la API). La descarga de `generation` se interrumpió una vez (mi propio `pkill`) y se **reanudó con `--resume`** sin repetir los 58 trozos ya descargados. Ejecución diaria de prueba (`--days 3`, dos días de ventana): 7 peticiones por dataset en 7–20 s.

## 6. Revisiones y versionado diario (F2.7)

Cada ejecución crea `fetched_at=<UTC>` nuevo; la plata toma el valor de la descarga más reciente y las **revisiones** salen de comparar versiones del bronce por clave (`revisions` del informe: claves vistas en ≥ 2 descargas, cuántas cambian y el cambio máximo). Con dos descargas separadas por 20 min no hay ninguna revisión (676 claves de generación, 752 de pronóstico, 480 de precios comparadas, 0 cambios), lo que solo prueba que el mecanismo funciona; la medición real exige acumular descargas diarias. Para esto hay que lanzar `--dataset all --days 3` cada día (generación, para ver cuánto se revisa el dato "real" tras publicarse; pronóstico, para el `first_seen_at`).

**Idempotencia.** `silver.write_silver` compara el contenido con la tabla existente y no crea versión de Delta si es idéntico; las claves únicas (`ts_utc`; `(year, psr_type)`; `(ts_utc, psr_type)`) se comprueban al construir y en Pandera. Relanzar `--no-fetch` dos veces deja la versión de Delta intacta en los cuatro datasets. Una descarga nueva solo reescribe plata en los datasets que llevan `fetched_at` como columna (`capacity`, `prices`, `forecast`), por trazabilidad; sin duplicados.

**Revisiones del pronóstico del operador (#28).** Con las 173 descargas horarias de la VM (16 a 23 oct 2026), el pronóstico de cada día objetivo del 17 al 23 oct no cambia nunca: la versión primera y la última coinciden al MW en los 96 cuartos de B18 + B19 (RMSE y MAE de 0 MW). La referencia `operator` puede leer la primera versión publicada (`version="first_published"`, bronce) o la última (`latest`, plata); detalle y límites en `docs/f4-backtest.md` §9.

## 7. Validación (Pandera)

- Generación: `ts_utc` único, malla de 15 min sin saltos, `wind_*_mw` ≥ 0 y ≤ 8 000 (marina) / 40 000 MW (terrestre) en el esquema, `*_src` en {original, a03, hourly, gap}, `*_gap_len` ≥ 0. El informe añade el cociente máximo/capacidad por año (y lo lista si supera 1,10).
- Precios: único, −1 000 a 5 000 EUR/MWh (admite negativos), resolución 15 o 60.
- Pronóstico: clave única `(ts_utc, psr_type)`, 0–48 000 MW, `doc_created`/`first_seen_at` presentes.
- Capacidad: clave `(year, psr_type)` única, 0–40 000 MW.

Las cuatro plata pasan sin incidencias. Tests unitarios sin red en `tests/unit/test_entsoe_ingest.py` (23): parseo A03/ZIP/Acknowledgement, `createdDateTime`, relleno de malla con flag y longitud de hueco, serie con arranque tardío, cambio de resolución, última descarga gana, `first_seen_at`, idempotencia de plata (generación y pronóstico), esquemas (negativos, hueco de malla, duplicados), troceado, reintentos, token, reanudación y recuperación DE-LU.

## 8. Decisiones y limitaciones

- **Plata reconstruida entera desde el bronce** (≈ 1,3 M filas, segundos): más simple y más fácil de razonar que el merge incremental; por eso es idempotente por construcción. Si el volumen crece se puede pasar a `merge`.
- **Un `fetched_at` por ejecución**, no por trozo; la hora real de cada respuesta está en `doc_created`.
- **Sin cobertura anterior a 2015** ni intradiario (`A40`) ni corriente (`A18`) del pronóstico.
- **El precio se queda en resolución nativa** (hora hasta 2025-09-30); no se remuestrea.
- **Lo no verificado:** la hora de publicación del pronóstico (§3), la tasa de revisiones (§6) y la comparación con los datos brutos de Predico (ADR 0013, cuando lleguen las credenciales).
- `ENTSOE_API_KEY` se lee de `.env`; el repo solo contiene `.env.example` sin valor.
