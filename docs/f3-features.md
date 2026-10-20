# F3 — Capa de variables y control de look-ahead

Cubre F3.1 a F3.4 y el test anti look-ahead de F3. Código en `src/predico50hz/features/`; tabla oro en `data/gold/features` (Delta Lake); informes en `data/validation/gold_features.json` y `data/validation/gold_interp_check.json`. Todo el tiempo es UTC salvo que se diga Europe/Berlin; los identificadores van en inglés.

## 1. Cómo ejecutarlo

```
uv run python -m predico50hz.features                                   # todas las pasadas de la plata
uv run python -m predico50hz.features --start 2026-10-01 --end 2026-10-15  # solo esas fechas de cierre
uv run python -m predico50hz.features --validate-only                   # no construye, solo valida
uv run python scripts/f3_interp_check.py                                # comprobación empírica de la interpolación
```

`--start/--end` son fechas de cierre (inclusivas): solo reemplazan esas fechas (overwrite Delta con predicado); sin ellas se reescribe la tabla entera. Opciones de rutas: `--gold`, `--validation`, `--nwp`, `--generation`, `--cap-cluster`, `--cap-total`, `--centroids`. Código de salida 1 si la validación encuentra problemas (o si no hay pasadas en el rango). La tabla es **reconstruible desde la plata** e **idempotente**: si el contenido a escribir coincide con el que ya hay (en todo el rango o en el pedido) no se crea versión Delta nueva, igual que la plata de ENTSO-E.

| Módulo | Función |
|---|---|
| `config.py` | Rutas, latencia NWP, alturas de buje, partición de la v0.1, método de interpolación |
| `timing.py` | Cierre, ventana del día objetivo, malla de 15 min y `available_at_*` |
| `interpolate.py` | Lineal / PCHIP / spline cúbico y constante por tramos (racha); nunca extrapola |
| `nwp.py` | Lectura de la pasada 00Z de D e interpolación de los estadísticos por clúster |
| `variables.py` | Variables derivadas (v³, buje, densidad, estabilidad, racha, dirección, dispersión) |
| `capacity.py` | Pesos por capacidad MaStR de la fecha D y variables de capacidad |
| `target.py` | Objetivo `y_*` (generación y factor de capacidad) |
| `calendar.py` | Calendario en hora local de Berlín |
| `registry.py` | Registro explícito de columnas: rol (`meta`/`feature`/`target`) y bloque |
| `build.py` | Construcción por día, ensamblado y escritura Delta idempotente |
| `validate.py` | Esquema Pandera e informe JSON |

## 2. Tiempos

`forecast_issue_date` D es la fecha local del cierre. **Cierre = 15:00 Europe/Berlin de D.** El día objetivo es el día local D+1. La malla son intervalos de 15 min identificados por su inicio `ts_utc`.

| D (cierre) | Cierre UTC | Día objetivo (UTC) | Pasos | `available_at_nwp` | Margen NWP → cierre |
|---|---|---|---|---|---|
| 2026-01-15 (invierno) | 14:00 | 01-15 23:00 → 01-16 23:00 | 96 | 08:30 | 5 h 30 min |
| 2026-07-01 (verano) | 13:00 | 07-01 22:00 → 07-02 22:00 | 96 | 08:30 | 4 h 30 min |
| **2026-03-28** (cambio a verano el día objetivo) | 14:00 | 03-28 23:00 → 03-29 22:00 | **92** | 08:30 | 5 h 30 min |
| **2025-10-25** (cambio a invierno el día objetivo) | 13:00 | 10-25 22:00 → 10-26 23:00 | **100** | 08:30 | 4 h 30 min |

Disponibilidad por bloque de variables (cada bloque tiene su columna `available_at_<bloque>`):

| Bloque | `available_at` | Origen |
|---|---|---|
| `nwp` | D 00:00 + 8 h 30 min | Pasada IFS ENS con `init_time` = D 00:00 UTC. Publicación medida en dynamical: 08:15–08:23 UTC (`docs/stage0-dynamical.md` §3); 8 h 30 min es el peor caso más margen (`NWP_LATENCY`, configurable). Es la semántica de producción aplicada al histórico |
| `capacity` | D 00:00 | Capacidad MaStR de la fecha D |
| `calendar` | D 00:00 | Determinista |

Reglas: solo se usa la pasada 00Z de D (si falta, no hay filas de D: no se rellena con otra pasada); la pasada de D+1 nunca se lee para D. Las variables agregadas por capacidad llevan el bloque `nwp`: dependen también de los pesos de la capacidad de D, pero esa llega antes (00:00) que la pasada, así que `available_at_nwp` es el máximo de ambas. Para el objetivo se usa la capacidad de la **fecha UTC de cada intervalo** (no es una variable).

## 3. Interpolación 3 h → 15 min (F3.4)

Los pasos NWP son +18…+54 h cada 3 h (13 pasos). Cada intervalo `[ts_utc, ts_utc + 15 min)` toma el valor en su **punto medio** (`ts_utc + 7,5 min`), porque la generación de ENTSO-E es la media del intervalo. Se interpola por (clúster, columna) primero los estadísticos crudos y **después** se derivan v³, densidad, etc. (el orden importa: v³ no conmuta con la interpolación).

| Variables | Método | Justificación |
|---|---|---|
| Instantáneas: velocidades, u/v, temperaturas, presiones, y todos sus estadísticos (ctrl, mean, std, p10, p50, p90) | **Lineal** (`INSTANT_INTERP`) | Combinación convexa de los nodos: no sobreoscila, velocidad ≥ 0, std ≥ 0 y p10 ≤ p50 ≤ p90 se conservan. Es la opción por defecto salvo que PCHIP gane en viento (regla abajo) |
| Racha `wind_gust_10m_*` | **Constante por tramos** (`step_after`) | Es el máximo de la ventana `(s − 3 h, s]`. El intervalo `[t, t+15)` toma el primer paso `s ≥ t + 15 min`, que es el que lo contiene. Una lineal suavizaría el pico. Si la plata la trae en NaN: `gust_missing` = 1 y las columnas de racha quedan NaN (único sitio donde se admite NaN) |

Si se activase PCHIP o el spline, `nwp._postprocess` reordena p10/p50/p90 y recorta std ≥ 0 y velocidades ≥ 0 (con lineal es una identidad).

### Comprobación empírica (`scripts/f3_interp_check.py`, `data/validation/gold_interp_check.json`)

Sobre las 410 pasadas reales y 21 clústeres se quitan los pasos impares (malla de 6 h: +18, +24, …, +54) y se reconstruyen los 6 pasos intermedios de 3 h con cada método. Error = reconstruido − real (m/s, °C, Pa).

| Variable | Lineal RMSE / sesgo | PCHIP RMSE / sesgo | Spline cúbico RMSE / sesgo |
|---|---|---|---|
| `wind_speed_100m_mean` | 0,604 / −0,020 | **0,590** / −0,004 | 0,634 / −0,003 |
| `wind_speed_100m_ctrl` | 0,902 / −0,019 | 0,908 / −0,004 | 0,989 / −0,002 |
| `wind_speed_100m_p10` / `_p90` | 0,646 / 0,677 | 0,638 / 0,667 | 0,690 / 0,717 |
| `wind_speed_100m_std` | 0,182 / 0,003 | 0,185 / 0,001 | 0,203 / −0,002 |
| `temperature_2m_mean` | 0,958 / 0,107 | 0,768 / 0,014 | **0,660** / 0,051 |
| `pressure_surface_mean` | 38,1 / 2,37 | **36,2** / 3,52 | 37,3 / 4,93 |

**Regla:** se queda lineal salvo que PCHIP mejore el RMSE de `wind_speed_100m_mean` en más de un 5 %. Mejora un **2,34 %** → **lineal**. En el viento las tres curvas dan errores del mismo orden y el spline cúbico es peor que la lineal (sobreoscila); en temperatura sí ganan PCHIP/spline (ciclo diurno suave, −20 % a −31 % de RMSE), pero la temperatura solo entra en la densidad (ρ varía ~0,3 % por grado) y en la estabilidad, y se prefirió un único método para todas las instantáneas. Si en F6 se quisiera, `INSTANT_INTERP = "pchip"` es un cambio de una línea.

**Salvedad:** la prueba 6 h → 3 h **exagera** el error frente al uso real (3 h → 15 min): el paso a reconstruir es el doble de largo y la curvatura que debe seguir es la de 6 h, mucho mayor. Los RMSE de arriba son una cota superior, no el error de la tabla oro. Además el error de reconstrucción (≈ 0,6 m/s) es del mismo orden que la propia dispersión del ensemble (`std` medio ≈ 1 m/s), así que no se ha medido el efecto sobre el modelo.

## 4. Variables (F3.2)

Formato ancho, una fila por intervalo. Todas son `Float64` salvo los indicadores (`Int8`) y `step_of_day` (`Int16`). `<g>` ∈ {`land`, `sea`, `all`}; los pesos son `cap_c / Σ cap` del grupo con la capacidad MaStR de la fecha D. `<pid>` es el `point_id` de `config/centroids.yaml` (16 de tierra, 5 de mar). Las velocidades están en m/s, temperaturas en °C, presiones en Pa.

### Bloque `nwp` (225 columnas)

| Columna(s) | Significado |
|---|---|
| `ws100_<pid>_{ctrl,mean,p10,p50,p90,std}` (126) | Velocidad del viento a 100 m del miembro de control, media, cuantiles y desviación típica del ensemble en el clúster (m/s) |
| `wshub_<pid>_mean` (21) | Velocidad media a la altura de buje del clúster (119 m tierra, 104 m mar) |
| `ws100_<g>_{mean,ctrl,p10,p90,std}` | Agregados ponderados por capacidad; `std` es la media ponderada de la desviación del ensemble (dispersión de la incertidumbre) |
| `ws10_<g>_mean` | Viento a 10 m medio ponderado (m/s); auxiliar de cizalladura y factor de racha |
| `v3_<g>_ctrl` | Σ w·ctrl³ (m³/s³) |
| `v3_<g>_ens` | Σ w·(μ³ + 3μσ²): E[v³] suponiendo ensemble gaussiano con media μ y desviación σ |
| `v3_<g>_p10`, `v3_<g>_p90` | Σ w·pX³ (cubo del cuantil) |
| `shear_<g>` | Exponente de cizalladura α = ln(v100/v10)/ln(10) por clúster, recortado a [0; 0,5] y 1/7 si v10 o v100 < 0,5 m/s; media ponderada |
| `wshub_<g>_mean` | Velocidad de buje: v100·(h/100)^α, h = 119 m (tierra) / 104 m (mar) (`docs/f2.6-mastr.md` §8) |
| `v3hub_<g>_ens` | E[v_hub³] con la misma corrección μ³ + 3μσ² (σ escalada por el mismo factor que μ) |
| `rho_<g>` | Densidad del aire ρ = p_s / (287,05·(T2m + 273,15)) con las medias (kg/m³) |
| `pdens_<g>` | Proxy de potencia Σ w·ρ·E[v_hub³] |
| `stab_<g>` | Estabilidad Σ w·(T925 − T2m) (°C) |
| `t2m_<g>` | Temperatura a 2 m ponderada (°C) |
| `gust_<g>_mean`, `gust_<g>_p90` | Racha a 10 m (máximo de la ventana de 3 h) media y p90 del ensemble, ponderada (m/s). NaN si `gust_missing` = 1 |
| `gustfac_<g>` | Factor de racha: racha media / máx(ws10 medio; 0,1) (NaN si falta la racha) |
| `u100_<g>`, `v100_<g>` | Componentes medias del viento a 100 m (m/s) |
| `wdir100_<g>_{sin,cos}` | Seno y coseno de la dirección meteorológica (de dónde sopla): −u/\|V\|, −v/\|V\|; 0 si el viento medio es nulo |
| `disp_land_std`, `disp_land_range`, `disp_land_cv` | Dispersión espacial de `ws100_mean` entre los 16 clústeres de tierra: desviación típica ponderada por capacidad, máx − mín y std/media (m/s, m/s, adimensional) |
| `ws100_sea_minus_land` | Viento a 100 m medio de mar menos el de tierra (m/s) |
| `gust_missing` | 1 si la plata NWP no trae racha para esa pasada (anterior al 2024-11-13); 0 en toda la tabla actual |
| `lead_hours` | Horas desde el `init_time` hasta el punto medio del intervalo (22,125 a 46,875 en la tabla real) |

### Bloque `capacity` (4 columnas)

`cap_land_mw`, `cap_sea_mw`, `cap_all_mw`: capacidad instalada MaStR (MW) de la fecha D; `cap_sea_share`: cuota marina. Es la capacidad **de D**, no el denominador del objetivo.

### Bloque `calendar` (10 columnas)

Hora local de Europe/Berlin del inicio del intervalo: `hour_local` (fraccional, 0–23,75), `hour_sin/cos`, `doy_sin/cos` (día del año local sobre 365,25), `dow` (0 = lunes), `month`, `is_weekend`, `is_dst` y `step_of_day` (0 … n−1). **Sin festivos**: no hay dependencia que los aporte; queda para cuando el modelo lo pida.

### Meta (11 columnas)

`forecast_issue_date`, `target_date` (D+1 local), `ts_utc`, `closure_utc`, `nwp_init_time`, `available_at_nwp`, `available_at_capacity`, `available_at_calendar`, `n_steps_day` (92/96/100), `capacity_ffilled` y `split_v01`. **`split_v01` es meta, nunca feature**: por `target_date`, `train` sep 2025 – may 2026, `valid` jun – jul 2026, `holdout` ago 2026 en adelante (ADR 0010).

## 5. Objetivo (F3.3)

Columnas `y_*`: `y_gen_mw` (= `wind_mw` de ENTSO-E), `y_gen_land_mw`, `y_gen_sea_mw`, `y_cap_mw`, `y_cap_land_mw`, `y_cap_sea_mw` (capacidad MaStR de la **fecha UTC** de `ts_utc`), `y_cf` = generación / capacidad total, `y_cf_land`, `y_cf_sea` y `y_is_gap_filled`. Se conserva la generación en MW para evaluar como Predico. Más allá del último día de MaStR (2026-10-15) se arrastra el último valor y `capacity_ffilled` vale `true` (en la tabla real: 88 filas, las del 16 de octubre UTC del cierre 2026-10-15). Nulos solo donde falta la generación: el cierre 2026-10-15 tiene 38 filas sin generación (ENTSO-E llega hasta el 2026-10-16 12:15 UTC). Nunca se lee el pronóstico del operador; el precio tampoco (ver §9).

## 6. Tabla oro

| Aspecto | Valor |
|---|---|
| Ruta | `data/gold/features` (Delta Lake), una fila por (`forecast_issue_date`, `ts_utc`), clave única |
| Filas / días | 39 360 / 410 cierres (2025-09-01 … 2026-10-15): 408 días de 96 pasos, 1 de 92 (cierre 2026-03-28) y 1 de 100 (cierre 2025-10-25) |
| Columnas | 260: 11 meta + 239 features (225 `nwp`, 4 `capacity`, 10 `calendar`) + 10 objetivo |
| Tamaño en disco | 76 MiB (78,7 MB, un Parquet snappy + `_delta_log`) |
| Construcción completa | 76–84 s en 4 ejecuciones (≈ 0,2 s por cierre; lectura de los 410 Parquet incluida) |
| Validación | 2,5 s |
| Idempotencia | Relanzada 3 veces sin cambios: `written: false`, versión Delta 0 |

## 7. Validación (`validate.py`, Pandera con backend Polars)

Esquema derivado del registro: tipos; clave única; malla contigua de 15 min por día; `n_steps_day` igual al nº de filas del día y a la ventana de Berlín (92/96/100); rango físico por familia de variable (`RANGES`, con margen); sin NaN ni nulos en features salvo las columnas de racha cuando `gust_missing` = 1; `y_cf*` en [0; 1,1]; `y_cap_*` nunca nulos y `y_cf` nulo si y solo si lo es `y_gen`; `available_at_* < closure_utc`; `nwp_init_time` = D 00:00 UTC; `target_date` = D+1.

Resultado sobre la tabla real: `schema_ok = true`, 0 problemas, 0 NaN en 239 features, `days_by_steps` = {92: 1, 96: 408, 100: 1}, margen mínimo NWP → cierre 270 min, 1 día con objetivo incompleto (2026-10-15, 38 filas). El informe incluye además NaN por columna, mínimos y máximos observados de cada feature y la media de `y_cf` por partición. Observados (resumen): `ws100_all_mean` 0,98–21,7 m/s, `shear_*` 0–0,45, `rho_*` 1,13–1,34, `stab_*` −9,8 a 9,95 °C, `y_cf` máx. 0,72 (tierra 0,76; mar 0,96).

### Comprobaciones manuales de sentido (sobre la tabla real)

| Comprobación | Resultado |
|---|---|
| Correlación de Pearson con `y_cf` | `wshub_all_mean` **0,938**, `ws100_all_mean` 0,936, `ws100_all_ctrl` 0,934, `pdens_all` 0,882, `v3hub_all_ens` 0,880, `v3_all_ens` 0,874. Por tipo: tierra `wshub_land_mean` vs `y_cf_land` 0,934; mar 0,827. La correlación lineal de v³ es menor que la de la velocidad porque la curva de potencia satura (por encima de ~12 m/s el factor de capacidad deja de crecer con v³) |
| Media de `y_cf` por partición | train 0,203 (272 días), valid 0,152 (61), holdout 0,181 (77; 7 354 filas con objetivo). El valid (jun–jul) tiene menos viento que train, coherente con `wshub_all_mean`: 6,75, 5,89 y 6,45 m/s |
| Un valor a mano | Cierre 2026-03-28, clúster `bb_01`, intervalo 2026-03-29 10:00 UTC: punto medio a +34,125 h; silver `wind_speed_100m_mean` = 5,0786 (+33 h) y 5,9140 (+36 h) → lineal 5,39185 a mano; la tabla oro da 5,39185 (coincide) |

## 8. Tests anti look-ahead (`tests/leakage/test_leakage_features.py`)

Con datos sintéticos (fixture `synth` de `tests/conftest.py`: plata NWP mínima de 3 puntos, generación y capacidad en `tmp_path`, con los cierres de los dos cambios de hora; no dependen de `data/`):

1. Falla si algún `available_at_*` ≥ `closure_utc`, si `closure_utc` no es el cierre de Berlín, o si `nwp_init_time` ≠ D 00:00 UTC. Se aplica siempre a la tabla sintética y además a `data/gold/features` si existe (si no, `skip`). Un caso negativo comprueba que detecta la violación (también el esquema Pandera).
2. Todo feature del registro pertenece a un bloque con columna `available_at`; ninguna columna `y_*`, de generación, de capacidad del objetivo, `split_v01`, de precio ni del pronóstico del operador es feature.
3. Invariancia: al perturbar la generación (×3,7, o recortada) y escribir un «pronóstico del operador» cualquiera, las columnas feature y las `available_at` son idénticas bit a bit (el test comprueba también que el objetivo sí cambia).
4. La pasada de D+1 (o la de D−1) no se usa: modificarla o añadirla no cambia las features de D; sin la pasada de D no hay filas de D. El caso contrario (cambiar la de D sí cambia las features) comprueba que el test tiene dientes.
5. Estático: ningún módulo de `predico50hz.features` importa de `ingest.entsoe` ni contiene `entsoe/forecast`, `entsoe/prices` o los datasets `forecast`/`prices`.

Además, 40 tests unitarios nuevos y 10 de fuga (50 en total) (`tests/unit/test_features_*.py`): tiempos (cierre 13/14 UTC, ventanas, 92/96/100 con las fechas reales), interpolación y racha por tramos con casos a mano, v³ y E[v³], pesos con capacidad que cambia por fecha, densidad, cizalladura con recortes, objetivo y arrastre de capacidad, idempotencia de la escritura con Delta (incluidas columnas NaN y reemplazo por rango) y validación (detecta cf fuera de rango, NaN, duplicados, huecos y `available_at` tardío). La suite completa (`uv run pytest`: 162 tests), `ruff check`, `ruff format --check` y `pyright` (estricto en `src/`) pasan.

## 9. Precio day-ahead: fuera de la v0.1

El ADR 0009 exige usar el precio solo si su hora de publicación medida es anterior al cierre. Solo existe **una** observación de su hora de publicación (`fetched_at` 2026-10-16 13:17 UTC, posterior al cierre de verano de ese día), así que **no está medida y no entra**: no hay bloque `price` ni columna `available_at_price`. Para medirla: descargas horarias `uv run python -m predico50hz.ingest.entsoe --dataset prices --days 2` entre las 10:00 y las 14:00 UTC durante varios días, y tomar para cada D la primera `fetched_at` del bronce en la que aparece D+1 (primer `ts_utc` ≥ el inicio del día objetivo). Solo se incorporaría como bloque nuevo con su `available_at_price` si esa hora (con margen, p. ej. el máximo observado) es anterior al cierre (13:00 UTC en verano).

## 10. Decisiones

- **Latencia NWP de producción aplicada al histórico:** `available_at_nwp` = 00Z + 8 h 30 min para todas las fechas, aunque el histórico se descargó después. Un test comprueba que todo cierra con ≥ 4 h de margen.
- **Un único `available_at` por bloque.** Las agregadas por capacidad van en `nwp` (ver §2); `lead_hours` también.
- **Interpolación lineal** para las instantáneas y constante por tramos para la racha (§3).
- **Denominador del objetivo = MaStR de la fecha UTC del intervalo**, nunca ENTSO-E (`docs/f2.6-mastr.md` §8).
- **NaN solo en racha**, con `gust_missing`. Es un NaN explícito y no un relleno, para que LightGBM lo trate como ausente.
- **Dirección con la media de los vectores** (u, v ponderados), no la media de las direcciones; con viento flojo la dirección es ruidosa (el seno y coseno tienden a valores arbitrarios), pero acotada.
- **Cada cierre se calcula de forma independiente** (sin estado entre días), así la reconstrucción parcial por rango es idéntica a la completa.
- **Factor de racha con denominador mínimo de 0,1 m/s** y `disp_land_cv` igual; evita divisiones por cero sin introducir NaN.

## 11. Limitaciones y no verificado

- **La latencia NWP es la de producción, no la histórica.** Además dynamical reescribe la pasada previa (`docs/stage0-dynamical.md` §3): la pasada de D guardada en bronce puede no ser bit a bit la que habría visto producción a las 08:30 (se comprobó en dos pasadas y un chunk de una variable, no en todas).
- **MaStR con altas registradas a posteriori.** La capacidad de la fecha D se reconstruye con el export del 2026-10-15; una unidad dada de alta con retraso aparece en fechas pasadas en las que aún no estaba registrada. Hay una pequeña fuga de información conocida en los pesos (y en `cap_*_mw`) y en el denominador histórico; su magnitud no se ha medido.
- **Precio sin medir y fuera** (§9). **Sin festivos.**
- **E[v³] asume gaussianidad** del ensemble (μ³ + 3μσ²); no la ha contrastado con los 51 miembros (la plata solo guarda estadísticos).
- **Altura de buje constante por tipo** (119 m y 104 m, ponderadas por potencia hoy); los parques recientes tienen buje más alto y la capacidad histórica tenía otro reparto.
- **Racha:** el camino con racha ausente (`gust_missing` = 1) solo se ha ejercitado con datos sintéticos; la plata actual (desde 2025-09-01) siempre la trae. Los valores de racha agregados no se han contrastado a mano con la plata real (sí con casos sintéticos).
- **Interpolación:** el efecto de lineal frente a PCHIP sobre el error final del modelo no se ha medido; la comprobación empírica es 6 h → 3 h (§3).
- **Las pasadas de la etapa 3 (1 abr 2024 – 31 ago 2025) aún no están** en la plata: la tabla cubre solo los 410 cierres de la etapa 2. Se amplía lanzando de nuevo el CLI.
- Esta capa aún no se ha usado con un modelo (F4 y siguientes no existen todavía).
