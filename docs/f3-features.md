# F3 — Capa de variables y control de look-ahead

Cubre F3.1 a F3.4 y el test anti look-ahead de F3. Código en `src/predico50hz/features/`; tabla oro en `data/gold/features` (Delta Lake); informes en `data/validation/gold_features.json` y `data/validation/gold_interp_check.json`. Todo el tiempo es UTC salvo que se diga Europe/Berlin; los identificadores van en inglés.

## 1. Cómo ejecutarlo

```
uv run python -m predico50hz.features                                   # todas las pasadas de la plata
uv run python -m predico50hz.features --start 2026-10-01 --end 2026-10-15  # solo esas fechas de cierre
uv run python -m predico50hz.features --validate-only                   # no construye, solo valida
uv run python scripts/f3_interp_check.py                                # comprobación empírica de la interpolación
```

`--start/--end` son fechas de cierre (inclusivas): solo reemplazan esas fechas (overwrite Delta con predicado); sin ellas se reescribe la tabla entera. Opciones de rutas: `--gold`, `--validation`, `--nwp`, `--generation`, `--cap-cluster`, `--cap-total`, `--centroids`, `--prices`. Código de salida 1 si la validación encuentra problemas (o si no hay pasadas en el rango). La tabla es **reconstruible desde la plata** e **idempotente**: si el contenido a escribir coincide con el que ya hay (en todo el rango o en el pedido) no se crea versión Delta nueva, igual que la plata de ENTSO-E.

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
| `price.py` | Bloque `price`: precio day-ahead de D (horas negativas, mínimo, media, rango) y su `available_at_price` |
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
| `price` | Primera hora vista en la plata (`first_seen_at`) si está medida; si no, cierre(D−1) + 6 h | Precio day-ahead **de D** (nunca el de D+1), publicado la víspera. Ver §9 |

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
| `gust_missing` | 1 si la plata NWP no trae racha para esa pasada: los 226 cierres del 2024-04-01 al 2024-11-12 (21 700 filas); 0 desde el 2024-11-13 |
| `lead_hours` | Horas desde el `init_time` hasta el punto medio del intervalo (22,125 a 46,875 en la tabla real) |

### Bloque `capacity` (4 columnas)

`cap_land_mw`, `cap_sea_mw`, `cap_all_mw`: capacidad instalada MaStR (MW) de la fecha D; `cap_sea_share`: cuota marina. Es la capacidad **de D**, no el denominador del objetivo.

### Bloque `calendar` (10 columnas)

Hora local de Europe/Berlin del inicio del intervalo: `hour_local` (fraccional, 0–23,75), `hour_sin/cos`, `doy_sin/cos` (día del año local sobre 365,25), `dow` (0 = lunes), `month`, `is_weekend`, `is_dst` y `step_of_day` (0 … n−1). **Sin festivos**: no hay dependencia que los aporte; queda para cuando el modelo lo pida.

### Bloque `price` (5 columnas)

Precio day-ahead de **D** (el día local del cierre, publicado la víspera), zona DE-LU, en EUR/MWh. Son iguales para todas las filas del día objetivo. Se promedia primero por hora (la plata es horaria hasta el 30 sep 2025 y de 15 min desde el 1 oct 2025) y las variables salen de esas 23, 24 o 25 horas: `price_neg_hours` (nº de horas con precio medio negativo), `price_min`, `price_mean`, `price_range` (máx − mín) y `price_missing` (1 si falta alguna hora del día; entonces las otras cuatro valen NaN). Detalle y decisión en §9.

### Meta (12 columnas)

`forecast_issue_date`, `target_date` (D+1 local), `ts_utc`, `closure_utc`, `nwp_init_time`, `available_at_nwp`, `available_at_capacity`, `available_at_calendar`, `available_at_price`, `n_steps_day` (92/96/100), `capacity_ffilled` y `split_v01`. **`split_v01` es meta, nunca feature**: por `target_date`, `train` 2024-04-01 – 2024-09-30 (los 6 meses que solo entrenan), `valid` 2024-10-01 – 2026-07-31 (los meses que se validan uno a uno) y `holdout` desde 2026-08-01 (ADR 0010; definición con el histórico completo en `docs/f4-backtest.md` §3). Con la tabla anterior de 410 cierres era `train` sep 2025 – may 2026, `valid` jun – jul 2026.

## 5. Objetivo (F3.3)

Columnas `y_*`: `y_gen_mw` (= `wind_mw` de ENTSO-E), `y_gen_land_mw`, `y_gen_sea_mw`, `y_cap_mw`, `y_cap_land_mw`, `y_cap_sea_mw` (capacidad MaStR de la **fecha UTC** de `ts_utc`), `y_cf` = generación / capacidad total, `y_cf_land`, `y_cf_sea` y `y_is_gap_filled`. Se conserva la generación en MW para evaluar como Predico. Más allá del último día de MaStR (2026-10-15) se arrastra el último valor y `capacity_ffilled` vale `true` (en la tabla real: 856 filas, las del 16 de octubre UTC del cierre 2026-10-15 y las de los cierres 2026-10-16 a 10-23). Nulos solo donde falta la generación, solo en los últimos cierres: 161 filas, 65 del cierre 2026-10-22 y las 96 del 2026-10-23 (la plata de ENTSO-E llega hasta el 2026-10-23). Nunca se lee el pronóstico del operador; del precio solo el de D, como variable (ver §9), nunca como objetivo.

## 6. Tabla oro

| Aspecto | Valor |
|---|---|
| Ruta | `data/gold/features` (Delta Lake), una fila por (`forecast_issue_date`, `ts_utc`), clave única |
| Filas / días | 89 856 / 936 cierres (2024-04-01 … 2026-10-23): 932 días de 96 pasos, 2 de 92 (cierres 2025-03-29 y 2026-03-28) y 2 de 100 (cierres 2024-10-26 y 2025-10-25) |
| Columnas | 266: 12 meta + 244 features (225 `nwp`, 4 `capacity`, 10 `calendar`, 5 `price`) + 10 objetivo |
| Tamaño en disco | 171 MiB activos con las 260 columnas anteriores (la carpeta conserva los ficheros de versiones anteriores, sin `VACUUM`; con el bloque `price` ocupa 419 MiB en total) |
| Construcción completa | 114 s con el bloque `price` (112 s antes) para los 936 cierres (≈ 0,12 s por cierre con 32 núcleos; lectura de los 936 Parquet incluida). Con la tabla de 410 cierres eran 76–84 s |
| Validación | 1,9 s |
| Idempotencia | Relanzada sin cambios: `written: false`, versión Delta 7 |
| Versión Delta | 8 con el bloque `price` (la 7 es la de las ejecuciones de `docs/f4-backtest.md` §8.4; sus predicciones se reproducen bit a bit con la 8, §8.5) |

## 7. Validación (`validate.py`, Pandera con backend Polars)

Esquema derivado del registro: tipos; clave única; malla contigua de 15 min por día; `n_steps_day` igual al nº de filas del día y a la ventana de Berlín (92/96/100); rango físico por familia de variable (`RANGES`, con margen); sin NaN ni nulos en features salvo las columnas de racha cuando `gust_missing` = 1; `y_cf*` en [0; 1,1]; `y_cap_*` nunca nulos y `y_cf` nulo si y solo si lo es `y_gen`; `available_at_* < closure_utc` (también `available_at_price`); columnas `price_*` en su rango físico o NaN con `price_missing` = 1; `nwp_init_time` = D 00:00 UTC; `target_date` = D+1.

Resultado sobre la tabla real (936 cierres): `schema_ok = true`, 0 problemas, `days_by_steps` = {92: 2, 96: 932, 100: 2}, margen mínimo NWP → cierre 270 min, margen mínimo precio → cierre 1 020 min (17 h), 0 días sin precio, 2 días con objetivo incompleto (cierres 2026-10-22 y 10-23: 65 y 96 filas sin generación). NaN solo en las columnas de racha y en 21 700 filas (los 226 cierres con `gust_missing` = 1, ver §3 y §11); 0 NaN en el resto de features. El informe incluye además NaN por columna, mínimos y máximos observados de cada feature y la media de `y_cf` por partición. Observados (resumen): `ws100_all_mean` 0,99–15,2 m/s, `shear_*` 0–0,45, `rho_*` 1,13–1,34, `stab_*` −9,8 a 14,0 °C, `lead_hours` 22,125–46,875.

### Comprobaciones manuales de sentido (sobre la tabla real)

| Comprobación | Resultado |
|---|---|
| Correlación de Pearson con `y_cf` (936 cierres) | `wshub_all_mean` **0,939**, `ws100_all_mean` 0,938, `ws100_all_ctrl` 0,935, `pdens_all` 0,894, `v3hub_all_ens` 0,893, `v3_all_ens` 0,888. Por tipo: tierra `wshub_land_mean` vs `y_cf_land` 0,936; mar 0,812. Con racha ausente (21 700 filas) la de `wshub_all_mean` es 0,932 y con racha 0,941: sin diferencia relevante. La correlación lineal de v³ es menor que la de la velocidad porque la curva de potencia satura (por encima de ~12 m/s el factor de capacidad deja de crecer con v³) |
| Media de `y_cf` por partición | train 0,151 (182 días, abr – sep 2024), valid 0,189 (669), holdout 0,184 (85 cierres, 8 160 filas, 7 999 con objetivo). Coherente con `wshub_all_mean`: 5,93, 6,50 y 6,57 m/s (train es primavera y verano, con poco viento) |
| Un valor a mano | Cierre 2026-03-28, clúster `bb_01`, intervalo 2026-03-29 10:00 UTC: punto medio a +34,125 h; silver `wind_speed_100m_mean` = 5,0786 (+33 h) y 5,9140 (+36 h) → lineal 5,39185 a mano; la tabla oro da 5,39185 (coincide) |

## 8. Tests anti look-ahead (`tests/leakage/test_leakage_features.py`)

Con datos sintéticos (fixture `synth` de `tests/conftest.py`: plata NWP mínima de 3 puntos, generación y capacidad en `tmp_path`, con los cierres de los dos cambios de hora; no dependen de `data/`):

1. Falla si algún `available_at_*` ≥ `closure_utc`, si `closure_utc` no es el cierre de Berlín, o si `nwp_init_time` ≠ D 00:00 UTC. Se aplica siempre a la tabla sintética y además a `data/gold/features` si existe (si no, `skip`). Un caso negativo comprueba que detecta la violación (también el esquema Pandera).
2. Todo feature del registro pertenece a un bloque con columna `available_at`; ninguna columna `y_*`, de generación, de capacidad del objetivo, `split_v01`, de precio ni del pronóstico del operador es feature.
3. Invariancia: al perturbar la generación (×3,7, o recortada) y escribir un «pronóstico del operador» cualquiera, las columnas feature y las `available_at` son idénticas bit a bit (el test comprueba también que el objetivo sí cambia).
4. La pasada de D+1 (o la de D−1) no se usa: modificarla o añadirla no cambia las features de D; sin la pasada de D no hay filas de D. El caso contrario (cambiar la de D sí cambia las features) comprueba que el test tiene dientes.
5. Estático: ningún módulo de `predico50hz.features` importa de `ingest.entsoe` ni contiene `entsoe/forecast` ni el dataset `forecast`; la ruta de la plata de precios solo aparece en `config.py`.
6. **Precio (#29).** Falla si `available_at_price` ≥ cierre (con caso negativo, también en el esquema Pandera). Alterar (+500 o −777 EUR/MWh) o borrar todos los precios desde el día local D+1 no cambia ninguna variable ni `available_at_price` de D, para los cuatro cierres sintéticos (96, 92 y 100 pasos). Cambiar los precios de D sí cambia el bloque `price` y solo ese (el test tiene dientes). Un precio medido que llegó tras el cierre deja el día en NaN con `price_missing` = 1. Margen mínimo de la cota ≥ 17 h con los cambios de hora, probado sobre 1 000 cierres seguidos (`tests/unit/test_features_price.py`).

Además, 40 tests unitarios nuevos y 10 de fuga (50 en total) (`tests/unit/test_features_*.py`; con el histórico completo se añadió `test_split_boundaries`, que fija los límites de `train`, `valid` y `holdout`): tiempos (cierre 13/14 UTC, ventanas, 92/96/100 con las fechas reales), interpolación y racha por tramos con casos a mano, v³ y E[v³], pesos con capacidad que cambia por fecha, densidad, cizalladura con recortes, objetivo y arrastre de capacidad, idempotencia de la escritura con Delta (incluidas columnas NaN y reemplazo por rango) y validación (detecta cf fuera de rango, NaN, duplicados, huecos y `available_at` tardío). La suite completa (`uv run pytest`: 162 tests), `ruff check`, `ruff format --check` y `pyright` (estricto en `src/`) pasan.

## 9. Precio day-ahead: el de D+1 fuera; el de D, probado y excluido (ADR 0031, #29)

El ADR 0009 exige usar el precio solo si su hora de publicación medida es anterior al cierre. Ya está medida (#10, `docs/f2-entsoe.md` §3): del 17 al 22 oct 2026 el precio de D+1 se vio completo antes del cierre solo en 3 de 6 días, y el 20 oct no apareció hasta las 19:00 UTC, 6 h después. **El precio de D+1 no entra.** El de D se publica la víspera y siempre está a tiempo: es una pista más débil (curtailment o precios negativos el día del cierre, no el día objetivo). Se ha construido como bloque `price` y se ha probado en validación.

### Cobertura de la plata de precios

`data/silver/entsoe/prices` (zona DE-LU, `10Y1001A1001A82H`, posición 1 de SDAC) cubre del 2024-03-30 al 2026-10-23 21:45 UTC **sin ningún hueco**: 13 198 horas (hasta el 30 sep 2025 21:00 UTC) y 37 248 cuartos de hora (desde el 30 sep 22:00 UTC, que es el 1 oct 00:00 de Berlín, con SDAC ya a 15 min). Cada uno de los 936 cierres tiene las 23, 24 o 25 horas de su día local D (`price_missing` = 0 en los 936). Para esto no hizo falta descargar nada. Sí se amplió la plata: ahora guarda `first_seen_at` (mínimo `fetched_at` del bronce de cada marca, igual que el pronóstico del operador) y se reconstruyó desde el bronce con `--no-fetch`.

### Variables

Todas con el precio de D, iguales para todas las filas del día objetivo. El precio se promedia por hora antes de calcular nada, de modo que la definición es la misma antes y después del 1 oct 2025 (con cuartos de hora, el mínimo y el rango serían más extremos solo desde esa fecha).

| Variable | Definición | En la tabla |
|---|---|---|
| `price_neg_hours` | Horas con precio medio < 0 | 1,6 (máx. 20); 271 de los 936 días tienen alguna |
| `price_min`, `price_mean` | Mínimo y media de las horas (EUR/MWh) | −499,0 a 161,4 y −16,3 a 395,3 (mín. y máx. de cada una) |
| `price_range` | Máx − mín | 136 (5,8 a 828,9) |
| `price_missing` | 1 si falta una hora o un cuarto de hora del día: las otras cuatro valen NaN (nativa de LightGBM) | 0 en todos los días |

**Precio en las horas de más viento previsto: omitida.** La pasada 00Z de D cubre de +18 h a +54 h, es decir, desde las 18:00 UTC de D. El viento previsto del día D no está en esa pasada; haría falta la de D−1, y el ADR 0009 y el test de la pasada de D−1 prohíben leerla para D. Con la de D (el día objetivo) se mezclarían los precios de D con el viento de D+1, que no tiene sentido.

### Disponibilidad (`available_at_price`)

- **Medido** (6 días, cierres del 18 al 23 oct 2026): la primera hora en que la plata vio el día completo (`first_seen_at`, en la tabla entre 12:00 y 19:00 UTC de la víspera; 15:21 y 16:48 los de las descargas manuales). Es una cota superior de la publicación real.
- **Censurado** (todo lo anterior, incluidos el 16 y el 17 oct, vistos ya en la primera descarga del 16 oct 12:56 UTC): la cota prudente `cierre(D−1) + 6 h` (`timing.price_bound`; 6 h es el peor retraso medido, ADR 0031).
- Si un precio medido llegara tras el cierre de D, no se usa: ese día sale con NaN y `price_missing` = 1.

**Margen frente al cierre.** Con la cota: 18 h (1 080 min) en 926 de los 936 cierres, 17 h (1 020 min) el 2025-03-30 y el 2026-03-29 (cierre de D−1 a las 14:00 UTC y de D a las 13:00, cambio a verano) y 19 h (1 140 min) en los dos cierres del cambio a invierno. Los 6 medidos tienen de 1 079 a 1 499 min (18 a 25 h). **Margen mínimo: 1 020 min** y `available_at_price < closure_utc` en todas las filas (validación y test anti look-ahead, §8).

### Resultado en validación (22 pliegues, oct 2024 – jul 2026, 64 224 filas)

Mismos modelos, hiperparámetros y tabla oro (versión 8); solo cambia si entra el bloque `price`. La referencia «sin precio» reproduce bit a bit las ejecuciones de §8.4 (RMSE 1 060,84 y Winkler 3 396,5 con CQR).

| Modelo | RMSE Q50 (MW) | Winkler | Cobertura | Pliegues con menor RMSE |
|---|---|---|---|---|
| LightGBM sin precio | 1 060,8 | 3 450,6 | 71,2 % | — |
| LightGBM con precio | 1 054,3 (−0,6 %) | 3 446,6 (−0,1 %) | 71,3 % | 13 de 22 |
| LightGBM + CQR sin precio | 1 060,8 | 3 396,5 | 79,5 % | — |
| LightGBM + CQR con precio | 1 054,3 (−0,6 %) | 3 386,1 (−0,3 %) | 79,5 % | 13 de 22 (Winkler 12 de 22) |

Por subconjunto de días (RMSE de LightGBM sin y con precio, MW):

| Días | Filas | Sin precio | Con precio |
|---|---|---|---|
| Todos | 64 224 | 1 060,8 | 1 054,3 (−0,6 %) |
| Precio de D con horas negativas | 17 284 | 1 176,5 | 1 166,9 (−0,8 %) |
| Día objetivo con horas negativas (solo análisis: el precio de D+1 no es variable) | 17 276 | 1 189,4 | 1 185,3 (−0,3 %) |
| Día objetivo sin horas negativas | 46 948 | 1 009,4 | 1 001,8 (−0,8 %) |

Los días con precio negativo son los de más error (1 177 a 1 189 MW frente a 1 009): son los de mucho viento y con curtailment. El bloque no ayuda más ahí que en el resto.

Por pliegue, el cambio de RMSE va de −4,6 % a +2,4 %; media −0,66 %, mediana −0,33 %. Mejoran 13 pliegues y empeoran 9. Con un bootstrap por día (2 000 remuestreos de los 669 días) el cambio es −0,61 % con intervalo del 95 % de −1,34 % a +0,11 %: incluye el cero. Ganan más mar 2026 (−4,6 %), nov 2025 (−3,8 %), jun 2026 (−3,7 %) y jul 2026 (−3,3 %); pierden más jul 2025 (+2,4 %) y ene 2025 (+1,1 %). En 2026 el signo es casi siempre favorable (6 de 7 pliegues); en 2024 – 2025 va mezclado.

El precio de D aporta poco al modelo: el 0,3 % de la ganancia total en Q50 y el 2,8 % y 1,8 % en Q10 y Q90 (repartida entre `price_min`, `price_range` y `price_mean`).

### Decisión

**El bloque se queda construido pero excluido por defecto del modelo.** La mejora (−0,6 % de RMSE, −0,1 a −0,3 % de Winkler) es menor que la variación entre pliegues y su intervalo incluye el cero; no se puede distinguir del ruido, y añade 5 columnas y una dependencia de ENTSO-E en producción (si falla la descarga del precio, `price_missing` = 1 y el modelo cambia de comportamiento). El modelo usa `exclude_prefixes=["price_"]` por defecto (`models/lgbm.py::DEFAULT_EXCLUDE`); se activa con `--param 'exclude_prefixes=[]'`. La tabla oro conserva el bloque, el test anti look-ahead y la validación. Se vuelve a medir tras Optuna y con más variables, por si la señal crece (en 2026 el signo es más favorable que antes).

Ejecuciones (`--mlflow`; ver `docs/f4-backtest.md` §8.5): sin precio `20261023T171003Z-lgbm-validation` y `20261023T171607Z-lgbm_cqr-validation`; con precio `20261023T171006Z-lgbm-validation` y `20261023T171611Z-lgbm_cqr-validation`.

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
- **Precio** (§9): el de D+1 llega tarde y no entra; el de D está construido pero excluido del modelo por defecto (mejora −0,6 %, dentro del ruido). La cota `cierre(D−1) + 6 h` de los 930 cierres censurados es la regla del ADR 0031, no una medida: solo hay 6 cierres con hora medida, todos de octubre de 2026, y `first_seen_at` de las descargas manuales (15:21 y 16:48 UTC) es peor que la publicación real. No se ha medido el efecto sin la agregación por hora. **Sin festivos.**
- **E[v³] asume gaussianidad** del ensemble (μ³ + 3μσ²); no la ha contrastado con los 51 miembros (la plata solo guarda estadísticos).
- **Altura de buje constante por tipo** (119 m y 104 m, ponderadas por potencia hoy); los parques recientes tienen buje más alto y la capacidad histórica tenía otro reparto.
- **Racha:** la plata NWP no la trae antes del 2024-11-13 (226 cierres, 21 700 filas, el 24 % de la tabla). El camino con racha ausente se ha comprobado con datos reales: esas filas tienen las columnas `gust_*`/`gustfac_*` en NaN y `gust_missing` = 1, y pasan el esquema Pandera. Decisión: **NaN nativa de LightGBM más `gust_missing`**, sin quitar la variable (con ella o sin ella el error cambia menos del 1 %; `docs/f4-backtest.md` §8.4). Los valores de racha agregados no se han contrastado a mano con la plata real (sí con casos sintéticos). `gust_missing` actúa de indicador de época; un modelo lo podría usar de atajo, aunque su ganancia es ≈ 0.
- **Interpolación:** el efecto de lineal frente a PCHIP sobre el error final del modelo no se ha medido; la comprobación empírica es 6 h → 3 h (§3).
- **Cobertura:** la tabla cubre del 2024-04-01 al 2026-10-23 sin huecos (936 cierres, #26). Las pasadas del histórico (etapas 2 y 3) se validaron en bronce y plata (#24, #25). Los dos cierres más recientes tienen objetivo incompleto (la generación de ENTSO-E aún no llega).
- **Capacidad MaStR del histórico largo:** la capacidad de 2024 también viene del export del 2026-10-15, con el mismo problema de altas registradas a posteriori (más años atrás, más probabilidad de que falten bajas o altas tardías); no se ha medido.
- Se usa ya con LightGBM v0.1 (`docs/f4-backtest.md` §8.4).
