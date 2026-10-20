# Plan — Predico 50Hertz, eólica (`germany_wind_50hertz_ca`)

**Versión 1.2 · 16 de octubre de 2026 · Fase 1: datos, entrenamiento y validación**

> **Leyenda de estado (a 16 oct 2026):** ✅ hecho (con evidencia en el repo, referenciada) · 🔄 en curso · 🟡 parcial (se indica qué falta) · sin marca = pendiente.

> **Cambios en v1.2:** la fuente NWP principal pasa a ser **ECMWF IFS ENS en Icechunk/Zarr de dynamical.org** (51 miembros, desde 2024-04-01), leída en lazy y recortada al dominio; los GRIB oficiales de ECMWF Open Data (stream `enfo`) quedan como **respaldo** de la diaria. El histórico empieza el 1 de abril de 2024. Motivo y hallazgos: `docs/stage0-index-check.md` (GRIB: 100 m solo desde 2024-03-06 y racha desde 2024-11-13) y la medida de lectura en dynamical (~4 s por variable y pasada para el dominio con los 51 miembros). La cadena GRIB HRES de la etapa 1 original se conserva como base del respaldo (`docs/stage1-pipeline.md`).
>
> **Cambios en v1.1:** descarga del histórico por etapas con una v0.1 entrenada sobre los últimos 13 meses (sección 2.5); stack técnico y buenas prácticas en `STACK.md`.

---

## 1. Calendario y puertas de decisión

| Fecha | Hito | Puerta |
|---|---|---|
| 17 oct | ✅ Etapas 0 y 1 sobre dynamical completadas | Variables y cobertura verificadas; cadena validada con 7 días; latencia de publicación medida |
| 22 oct | 🔄 Etapa 2 (13 meses) descargada ✅ y archivo diario vivo ✅ desde el 16 oct (timers systemd locales; comprobación de 7 días en una VM efímera de Hetzner con Docker) | **G0**: 7 días seguidos de archivo diario sin fallos (`python -m predico50hz.orchestration status`) |
| 29 oct | **v0.1**: LightGBM sin optimizar sobre 13 meses, extremo a extremo | Métricas registradas frente a las cuatro referencias |
| 5 nov | Etapa 3 completa; baseline en backtest | **G1**: RMSE del Q50 ≤ pronóstico del operador; cobertura del 80 % entre 76 y 84 % |
| 12 nov | **v0.2**: modelo final con optimización y benchmark cerrado | **G2**: mejora sobre G1 confirmada en el periodo reservado (holdout) |
| 13–24 nov | **Fase 2**: definición y construcción del sistema de producción | Fuera de este documento |
| 25–30 nov | Rodaje en Predico | Cero días perdidos; margen p95 ≥ 60 min antes del cierre |
| **1 dic** | **Primer mes de liga** | |

**Reglas de corte:**
- Si G1 no se supera el 5 de noviembre, el inicio pasa al 1 de enero.
- Si el 15 de noviembre no hay credenciales de Predico, el inicio pasa al 1 de enero: sin rodaje no se entra en liga.

**Cierre en UTC:** 15:00 CET equivale a 14:00 UTC en invierno y 13:00 UTC en verano. El día objetivo abarca de 23:00 UTC del día del cierre a 23:00 UTC del día siguiente en invierno, y de 22:00 a 22:00 UTC en verano.

---

## 2. Fuente NWP: ECMWF IFS ENS en dynamical.org (respaldo: ECMWF Open Data `enfo`)

**Decisión (v1.2):** IFS ENS de ECMWF a 0,25° desde el archivo Icechunk/Zarr público de dynamical.org (`ecmwf-ifs-ens-forecast-15-day-0-25-degree`, bucket `s3://dynamical-ecmwf-ifs-ens`, us-west-2, acceso anónimo, licencia CC BY 4.0 + ECMWF Terms of Use). **Respaldo** de la diaria: los GRIB del stream `enfo` en `s3://ecmwf-forecasts`, que son el mismo origen del que dynamical construye su archivo.

**Por qué cambia respecto a la v1.1 (IFS HRES en GRIB):**
1. **El histórico efectivo es el mismo.** En los GRIB abiertos, `100u/100v` solo existen desde 2024-03-06 y `10fg` desde 2024-11-13 (`docs/stage0-index-check.md`). dynamical empieza el 2024-04-01: se pierde un mes, no años.
2. **Ensemble de 51 miembros** (control + 50 perturbados). Media, dispersión y cuantiles del ensemble son variables directas para Q10/Q90 y el Winkler; HRES no las da.
3. **Lectura en lazy con recorte en origen.** Chunks de 32×32 puntos (8°) con todos los pasos y miembros: el dominio se lee con un `sel()` sin GRIB, sin eccodes y sin descargar campos globales. Medido: ~4 s por variable y pasada (51 miembros, dominio completo, pasos +18 a +54 h).
4. **Mismo formato que la capa bronce** (Icechunk) y versionado (`v0.1.0.icechunk`).

**Limitaciones aceptadas:**
- Solo pasada **00Z** (la 06Z del plan anterior se abandona; si hiciera falta, AIFS Single de dynamical tiene pasadas cada 6 h).
- No hay `u/v` en niveles de presión: se pierde el chorro de bajo nivel directo. Se conserva la estabilidad (`temperature_925hpa` − `temperature_2m`) y la cizalladura 10–100 m. Se espera que el ensemble compense con creces.
- El control del ENS no es HRES. Da igual mientras **entrenamiento y producción usen la misma fuente**.
- Dependencia de un tercero: fijar la versión del dataset, guardar nuestra copia del recorte y mantener vivo el respaldo GRIB `enfo`.
- **Latencia sin medir:** hay que medir a qué hora UTC aparece cada 00Z en dynamical. Si no llega con ≥ 60 min de margen antes del cierre, la diaria usa el respaldo GRIB `enfo` (su hora de publicación también se mide; como referencia, el `.index` HRES 00Z del 15 oct apareció a las 07:34 UTC).

### 2.1 Pasada y pasos

| Pasada | Uso | Pasos | Motivo |
|---|---|---|---|
| **00Z** | Única | +18 a +54 h, cada 3 h (13 pasos) | Cubre el día objetivo con margen para interpolar |

### 2.2 Variables

| Variable dynamical | Para qué |
|---|---|
| `wind_u_100m`, `wind_v_100m` | Variable principal: altura de buje aproximada |
| `wind_u_10m`, `wind_v_10m` | Cizalladura vertical frente a 100 m |
| `wind_gust_10m` | Picos y paradas por viento extremo (verificar cobertura antes de 2024-11-13) |
| `temperature_2m` | Densidad del aire y estabilidad |
| `pressure_surface` | Densidad del aire: potencia proporcional a ρ·v³ |
| `temperature_925hpa`, `temperature_850hpa` | Estabilidad atmosférica |
| `pressure_reduced_to_mean_sea_level`, `geopotential_height_850hpa` | Contexto sinóptico y gradientes de presión (opcional) |

Por miembro se guardan los 51 miembros en bronce; en plata se derivan control, media, desviación típica y cuantiles del ensemble. La máscara tierra-mar no está en dynamical: se toma una vez de los GRIB abiertos (`lsm`, disponible desde 2023-03-30).

### 2.3 Dominio y volumen

- **Recorte:** 49,5–56,0° N y 8,5–16,0° E (27 × 31 puntos a 0,25°).
- **Transferencia:** se mide en la etapa 1. Al leer por chunks de 8°, el volumen real es el de los chunks que tocan el dominio, no el del recorte.
- **Almacenamiento:** recorte de ~930 pasadas × 13 pasos × 51 miembros × ~10 variables × 837 puntos en float32 ≈ decenas de GB como máximo; trivial frente a los 5 TB libres.

### 2.4 Comprobaciones del primer día

1. ✅ Variables, cobertura temporal y huecos de `init_time` en dynamical; NaN por variable y fecha (en especial `wind_gust_10m` antes de 2024-11-13). → `docs/stage0-dynamical.md` §1–§2
2. ✅ **Latencia:** hora UTC a la que aparece cada 00Z (snapshots de Icechunk con su marca de tiempo). → `docs/stage0-dynamical.md` §3
3. Coherencia con el respaldo: para una fecha, comparar el control de dynamical con el GRIB `enfo` del miembro de control en los centroides.
4. **Cambios de ciclo de IFS** (actualizaciones del modelo): registrar el ciclo por pasada y revisar si los residuos saltan en esas fechas.

### 2.5 Descarga por etapas

**Principio:** nunca descargar volumen grande sin haber validado antes la cadena completa con un volumen pequeño.

| Etapa | Qué | Coste | Puerta para pasar a la siguiente |
|---|---|---|---|
| ✅ **0** (día 1) → `docs/stage0-dynamical.md` | Metadatos de dynamical: variables, cobertura, NaN, latencia de los últimos snapshots | Kilobytes | Variables presentes y cobertura documentada |
| ✅ **1** (días 1–2) → `docs/stage1-dynamical.md` §2–§5 | 7 días completos (1–7 oct 2026), extremo a extremo: lectura lazy → recorte → bronce Icechunk propio → plata por clústeres | Cientos de MB | Cadena funciona; medidos MB y segundos por pasada; extrapolación del histórico completo en horas |
| ✅ **2** (días 2–5) → 410/410 pasadas en bronce y plata, validadas al 100 % (`data/validation/ifs_ens_2025-09-01_2026-10-15.json`) | Últimos 13 meses: 1 sep 2025 → hoy | ~45 % del total | Datos validados (F1.6) → **entrenar v0.1** |
| **3** (en segundo plano) | 1 abr 2024 → 31 ago 2025 | Resto | Reentrenar como v0.2 con el histórico completo antes de G2 |

**Por qué 13 meses y no solo desde septiembre de 2026:**
- **Estacionalidad.** El concurso empieza en diciembre, el mes de más viento. Un modelo entrenado en septiembre y octubre nunca ha visto un temporal invernal ni las rampas de un frente atlántico.
- **Tamaño mínimo para evaluar.** Seis semanas son unos 40 días de muestra: no da para validación walk-forward ni para un periodo reservado.

**Partición de la v0.1:**
- **Entrenamiento:** sep 2025 – may 2026, que incluye el invierno 2025/26.
- **Validación:** jun – jul 2026.
- **Periodo reservado:** ago – 15 oct 2026.

**Limitación conocida:** el periodo reservado de la v0.1 es verano y otoño, con poco viento. Su resultado no garantiza el comportamiento en diciembre. La v0.2, con el invierno 2024/25 en los pliegues de validación, sí lo cubre.

**Siguientes fuentes (solo si IFS ENS no basta en G1):** AIFS Single y AIFS ENS de dynamical (mismo código) → ICON-D2 por puntos vía Open-Meteo Previous Runs.

---

## 3. Features, tareas y criterios de aceptación

### F1 — Ingesta de IFS ENS (histórico y diaria)

**Tareas**
- F1.1 ✅ Lector de dynamical (Icechunk, lazy) con lista de variables, pasos y dominio configurables; versión del dataset fijada. → `src/predico50hz/ingest/dynamical/source.py` (exige `dataset_version` 0.1.0)
- F1.2 ✅ Recorte al dominio y escritura en nuestro Icechunk (capa bronce: valores originales recortados, 51 miembros). → `src/predico50hz/ingest/dynamical/bronze.py`, `docs/stage1-dynamical.md` §1
- F1.3 ✅ Extracción en centroides de clústeres (capa plata): control, media, desviación típica y cuantiles del ensemble. → 21 centroides de MaStR (`config/centroids.yaml`), plata en `data/silver/ifs_ens_clusters/` reconstruida desde el bronce (`rebuild_silver.py`, `docs/stage1-dynamical.md` §7); validada al 100 %
- F1.4 🟡 Relleno histórico desde el 1 de abril de 2024, idempotente y reanudable. Implementado con `--skip-existing`; etapa 2 completa; falta la etapa 3. → `src/predico50hz/ingest/dynamical/__main__.py`
- F1.5 ✅ Descarga diaria registrando la **hora real de disponibilidad** de cada pasada en dynamical y en el respaldo. → job `nwp-daily` (07:45 UTC, sondeo hasta la pasada completa, ingesta, plata, validación y variables de D; se pone al día con las pasadas que falten de 14 días); hora del commit de dynamical y `Last-Modified` del `.index` `enfo` en `data/ops/nwp_availability` (16 oct: dynamical 08:18:58, `enfo` 07:41:26 UTC, margen 281 min). `docs/f1.5-f2.7-operations.md`
- F1.6 ✅ Validaciones con Pandera: rangos físicos, NaN, número de pasos, miembros y huecos. → validador de bronce, plata y huecos con 22 tests (`src/predico50hz/ingest/dynamical/validate.py`, `docs/f1.6-validation.md`); etapa 2: cobertura 100 % (410/410), sin incidencias (`data/validation/ifs_ens_2025-09-01_2026-10-15.json`).
- F1.7 🟡 **Respaldo GRIB `enfo`** de ECMWF Open Data, reutilizando la cadena GRIB de la etapa 1 original (`src/predico50hz/ingest/ifs/`), con salida idéntica en plata. Hecho: cadena GRIB validada con 7 pasadas en HRES (`docs/stage1-pipeline.md`). Falta: pasar de `oper` a `enfo` y comparación principal–respaldo.

**Criterios de aceptación**
- 🟡 Cobertura ≥ 98 % de días con pasada 00Z completa; huecos listados con su causa. (Dynamical sin huecos en 928 pasadas: `data/stage0/dynamical_check.json`; el archivo propio está incompleto.)
- ✅ Relanzar una fecha produce exactamente el mismo resultado (idempotencia verificada por hash). → `docs/stage1-dynamical.md` §5 y `docs/stage1-pipeline.md` (2026-10-03)
- ✅ La hora de disponibilidad queda registrada para cada pasada diaria. → `data/ops/nwp_availability` (con `host` y `catch_up`)
- Respaldo y principal coinciden en plata para una fecha de prueba (diferencia dentro de la precisión de empaquetado GRIB).

### F2 — Verdad terreno y datos auxiliares

**Tareas**
- F2.1 ✅ Token de la API de ENTSO-E (**pedirlo hoy**: tarda días). → en `.env` (`ENTSOE_API_KEY`), leído con `src/predico50hz/settings.py`
- F2.2 ✅ Generación real B18 + B19 en `10YDE-VE-------2`, cada 15 min, desde 2015. Reconstruir la malla con arrastre del último valor, igual que Predico. → `docs/f2-entsoe.md` §2 (413 426 filas, PT15M en toda la historia, sin huecos; valores A03 arrastrados y marcados)
- F2.3 ✅ Capacidad instalada por tipo, anual. → `docs/f2-entsoe.md` §4. Ojo: va retrasada respecto a las altas (generación marina > capacidad en varios años); no sirve como denominador del factor de capacidad
- F2.4 ✅ Precio day-ahead de DE-LU (zona de oferta desde octubre de 2018). → `docs/f2-entsoe.md` (SDAC; PT60M hasta 2025-09-30 y PT15M desde 2025-10-01)
- F2.5 🟡 Pronóstico day-ahead del operador, **solo como benchmark**, con su hora de publicación. Hecho: serie desde 2015 (`docs/f2-entsoe.md`). Falta: hora de publicación (`createdDateTime` no sirve). En medición desde el 16 oct con `entsoe-hourly` y `publication-times` (`data/ops/publication_times.json`, resolución horaria); a las 13:20 UTC del día del cierre aún no estaba publicado
- F2.6 ✅ MaStR: aerogeneradores con coordenadas y potencia → 15–30 clústeres terrestres más los marinos, con capacidad por fecha de alta. → `docs/f2.6-mastr.md` (export 2026-10-15; 16 clústeres terrestres + 5 marinos en `config/centroids.yaml`; capacidad diaria en `data/silver/mastr/`)
- F2.7 ✅ Guardar desde ahora versiones diarias de lo publicado por ENTSO-E, para medir las revisiones. → bronce por `fetched_at`; jobs `entsoe-daily` (06:00 UTC, `--days 3`) y `entsoe-hourly` (precio y pronóstico cada hora) con systemd (`deploy/systemd/`, con espera de red y reintentos) y Docker (`docker/`), registro en `data/ops/runs.jsonl`. 20 oct: el cliente reintenta los `400` intermitentes de ENTSO-E (hasta 2) y expone su `<Reason>`

**Criterios de aceptación**
- ✅ Serie objetivo sin huecos tras la reconstrucción, o con huecos documentados. → `data/validation/entsoe_generation.json`
- 🟡 Suma de capacidad de los clústeres coherente con la de ENTSO-E (desviación < 5 %). Tierra: < 5 % todos los años; mar: < 5 % salvo 2018 (+113 %: ENTSO-E omite Wikinger). Se toma MaStR como referencia → `docs/f2.6-mastr.md` §7
- Cuando lleguen las credenciales: coincidencia con los datos brutos que descarga Predico.

### F3 — Capa de variables y control de look-ahead

**Tareas**
- F3.1 ✅ Uniones "as-of": cada variable lleva su hora de disponibilidad real. → `available_at_{nwp,capacity,calendar}` por fila; NWP = pasada 00Z de D + 8 h 30 min (peor caso medido 08:23), capacidad y calendario a las 00:00 UTC de D (`docs/f3-features.md` §2)
- F3.2 🟡 Variables: viento a 100 m por clúster, v³ ponderado por capacidad, cizalladura, densidad, estabilidad (t925 − 2t), racha, dispersión espacial entre clústeres, precio day-ahead (señal de curtailment), calendario y hora local. Hecho: 239 variables (bloques nwp, capacity y calendar), con buje a 119/104 m, E[v³] del ensemble y pesos de capacidad MaStR de la fecha (`docs/f3-features.md` §4). Falta: el precio, fuera de la v0.1 porque su hora de publicación no está medida (§9)
- F3.3 ✅ Objetivo: factor de capacidad (generación / capacidad instalada). → `y_cf`, `y_cf_land`, `y_cf_sea` con la capacidad MaStR del día y la generación en MW (`y_gen_*`)
- F3.4 ✅ Interpolación temporal de 3 h a 15 min. → lineal al punto medio de cada intervalo; racha constante por tramos (máximo de ventana). PCHIP solo mejora un 2,3 % el viento en la prueba 6 h → 3 h (`data/validation/gold_interp_check.json`)

**Criterios de aceptación**
- ✅ **Test automático anti look-ahead:** falla si alguna variable tiene hora de disponibilidad ≥ 13:00 UTC (verano) o 14:00 UTC (invierno) del día del cierre. → `tests/leakage/test_leakage_features.py` (10 tests, incluida la tabla real); margen mínimo real 4 h 30 min
- ✅ El precio day-ahead solo se usa si su hora de publicación medida es anterior al cierre. → no medida: fuera de la v0.1 (`docs/f3-features.md` §9)
- ✅ Tabla oro `data/gold/features` (Delta): 39 360 filas (410 cierres; 92/96/100 pasos según el cambio de hora), 260 columnas, 76 MiB, ~80 s; idempotente y validada con Pandera (`data/validation/gold_features.json`, 0 problemas)

### F4 — Marco de backtest

**Tareas**
- F4.1 ✅ Validación walk-forward con ventana creciente y pliegues mensuales. → `evaluation/folds.py`: 8 pliegues (dic 2025 – jul 2026), 3 meses mínimos de entrenamiento
- F4.2 ✅ **Periodo reservado (holdout): agosto–octubre de 2026.** Nunca lo ve la optimización de hiperparámetros. → fuera de los pliegues (test); su evaluación es explícita (`--holdout`) y queda contada en `data/backtest/holdout_log.jsonl`
- F4.3 ✅ Módulo de métricas idéntico al de Predico: RMSE del Q50, Mean Winkler Interval con α = 0,2, cobertura empírica, error de pico y ranking diario simulado frente al operador. → `evaluation/metrics.py`; salidas para negocio: `data/backtest/report/` (scorecard con veredicto mejora/empeora, 5 PNG) y capítulo «¿Mejora el pronóstico?» del informe ejecutivo. `docs/f4-backtest.md`

**Criterios de aceptación**
- ✅ Tests unitarios de las métricas con casos calculados a mano.
- ✅ Ninguna fila de entrenamiento posterior al inicio de su pliegue de validación (test automático). → `tests/leakage/test_backtest_folds.py`, también sobre la tabla real

### F5 — Referencias

🟡 Persistencia, climatología por hora y mes, curva de potencia empírica sobre el viento a 100 m y pronóstico del operador. Hecho (`models/baselines.py`, validación dic 2025 – jul 2026, RMSE Q50): operador 931 MW, curva de potencia sobre el viento a buje 1 197 MW (cobertura 76,9 %), climatología 3 406 MW. Falta: persistencia (requiere medir hasta qué hora hay generación publicada antes del cierre) y MLflow (el registro propio está preparado para un adaptador).

**Criterio de aceptación:** las cuatro evaluadas con el mismo módulo de métricas y registradas en MLflow.

### F6 — LightGBM con optimización de hiperparámetros (Optuna)

**Diseño del modelo**
- **Q50 con pérdida cuadrática**, no cuantílica al 0,5: se puntúa con RMSE y el RMSE lo minimiza la media.
- **Q10 y Q90 con pérdida cuantílica**, más calibración conformal (CQR) sobre una ventana móvil de 60 días.
- Orden forzado: Q10 ≤ Q50 ≤ Q90.

**Optimización**
- Muestreador TPE, poda por mediana, 150–300 intentos por modelo, número de árboles por parada temprana.
- **Espacio de búsqueda:**
  - `num_leaves`: 15–255
  - `learning_rate`: 0,01–0,1 (logarítmico)
  - `min_data_in_leaf`: 20–500
  - `feature_fraction` y `bagging_fraction`: 0,5–1
  - `lambda_l1` y `lambda_l2`: 10⁻³–10 (logarítmico)
- **Objetivo:** media de la métrica en los pliegues de validación. Todo se registra en MLflow.

**Criterios de aceptación**
- G1 y G2 superadas sobre el periodo reservado.
- **Control de sobreajuste:** si la métrica en el periodo reservado empeora más de un 10 % respecto a validación, se simplifica el espacio de búsqueda.

### F7 — Benchmark de modelos fundacionales

**Tareas**
- F7.1 Arnés común: mismas variables NWP como covariables futuras conocidas, mismos pliegues y mismas métricas.
- F7.2 **Candidatos:**
  - "Timestral": pendiente de identificar el modelo exacto.
  - Chronos-2: verificar licencia.
  - TimesFM 2.5: apto para uso comercial.
  - TimesFM 3: **solo investigación**, su licencia prohíbe la producción.
- F7.3 Modo sin ajuste (zero-shot) primero; ajuste fino solo si queda a menos de un 10 % de LightGBM.

**Criterios de aceptación**
- Licencia compatible con uso comercial y en producción.
- Mejora de al menos un 5 % en RMSE o en Winkler sobre el periodo reservado frente a LightGBM.
- Si se adopta, entra como componente del ensemble, no como sustituto.

### F8 — Calidad e ingeniería

- 🟡 Tests unitarios y de integración en CI. (276 tests pasan con `uv run pytest`, los anti look-ahead de variables y de pliegues en `tests/leakage/`; no hay CI ni `.github/`; `tests/integration` está vacío.)
- Contratos de datos entre capas (bronce, plata, oro).
- Reproducibilidad: semillas fijadas, versiones de librerías bloqueadas y cada experimento enlazado a la versión de datos de Icechunk.

**Criterio de aceptación:** reentrenar desde cero reproduce las métricas registradas con una diferencia < 0,5 %.

### F9 — Cierre de la Fase 1

Informe corto con resultados frente a las puertas, modelo elegido y riesgos abiertos. Decisión: 1 de diciembre o 1 de enero.

---

## 4. Cómo recortar si el calendario aprieta

Orden de recorte, del primero al último:
1. F7 (modelos fundacionales) se mueve a después del 1 de diciembre.
2. Se aplaza la comparación detallada principal–respaldo (F1.7) a lo imprescindible para la diaria.
3. Optuna baja a 50 intentos por modelo.
4. **Nunca se recortan** el test anti look-ahead, el periodo reservado ni la calibración conformal.

---

## 5. Avance de la Fase 2 (sistema de producción, a definir)

- Orquestación diaria con Dagster.
- Cliente de Predico.
- Producción principal en el equipo de sobremesa y respaldo en Docker sobre TrueNAS.
- Alertas y observabilidad.
- **Envío temprano de seguridad:** si Predico permite reenviar antes del cierre, mandar a primera hora un pronóstico simple y sustituirlo por el bueno. Verificarlo en la documentación de la API.
