# F4 — Marco de backtest y métricas

Cubre F4.1 a F4.3 y las referencias mínimas de F5 (climatología, curva de potencia, operador y persistencia). Código en `src/predico50hz/evaluation/` (marco) y `src/predico50hz/models/` (interfaz y referencias); tests en `tests/unit/test_backtest_*.py` y `tests/leakage/test_backtest_folds.py` y de extremo a extremo en `tests/integration/` (§10); salidas en `data/backtest/` (ignorado por git). Todo el tiempo es UTC salvo que se diga lo contrario.

## 1. Cómo ejecutarlo

```
uv run python -m predico50hz.evaluation run --model climatology        # validación walk-forward
uv run python -m predico50hz.evaluation run --model power_curve
uv run python -m predico50hz.evaluation run --model operator
uv run python -m predico50hz.evaluation run --model persistence       # repetir lo último conocido (§8.3)
uv run python -m predico50hz.evaluation run --model lgbm_cqr           # LightGBM + calibración conformal (§8.2)
uv run python -m predico50hz.evaluation run --model power_curve --holdout   # periodo reservado (queda registrado)
uv run python -m predico50hz.evaluation compare                         # clasificación de las últimas ejecuciones
uv run python -m predico50hz.evaluation report                          # scorecard.json + PNG
uv run python -m predico50hz.report                                     # informe ejecutivo (capítulo «¿Mejora el pronóstico?»)
```

Opciones de `run`: `--min-train-months N` (3 por defecto), `--param clave=valor` (repetible, JSON; parámetros del modelo), `--gold`, `--operator`, `--root`. `report` acepta `--no-png` y `--out`. Si el periodo reservado ya se evaluó con ese modelo, `run --holdout` avisa por stderr (no bloquea).

| Módulo | Función |
|---|---|
| `evaluation/metrics.py` | Métricas en MW y `summarize` (por pliegue, mes o total) |
| `evaluation/folds.py` | Pliegues walk-forward y separación del periodo reservado |
| `evaluation/backtest.py` | Ejecución, conversión a MW, validación, registro y clasificación |
| `evaluation/validate.py` | Esquema Pandera de las predicciones |
| `evaluation/store.py` | `RunRecord`, `FileRunStore`, registro del holdout y `leaderboard` |
| `evaluation/scorecard.py`, `plots.py`, `report.py` | Cuadro de mando y gráficos para negocio |
| `models/base.py` | Interfaz `Model` / `Prediction` |
| `models/baselines.py` | `climatology`, `power_curve`, `operator`, `persistence` |
| `models/cqr.py` | Calibración conformal (CQR) de Q10 y Q90; la usa `lgbm_cqr` (§8.2) |
| `report/backtest.py` | Capítulo del informe ejecutivo |

## 2. Métricas (todas en MW, sobre cuartos de hora)

`y` = generación real (`y_gen_mw`), `l`/`m`/`u` = Q10/Q50/Q90 en MW, α = 0,2, n = filas evaluadas.

| Métrica | Definición |
|---|---|
| RMSE del Q50 | `√( media((m − y)²) )` |
| MAE | `media(|m − y|)` |
| Sesgo | `media(m − y)`; positivo = sobreestima |
| Winkler (por punto) | `(u − l) + (2/α)·(l − y)·[y < l] + (2/α)·(y − u)·[y > u]`, con 2/α = 10 |
| Winkler medio | media de lo anterior; menos es mejor |
| Cobertura | `media([l ≤ y ≤ u])`; G1 exige 0,76–0,84 |
| Ancho medio | `media(u − l)` |
| Error de pico | por **día completo** (todos sus pasos con realidad): error = `max(m) − max(y)` (MW; se informan su valor absoluto medio `peak_abs_error_mw` y el signo `peak_bias_mw`); desfase = hora del `max(m)` menos hora del `max(y)` (`peak_shift_h`, media del valor absoluto, h). En empates se toma el primer cuarto de hora |
| Ranking diario | `days_better_than_operator_pct`: % de días completos en que `RMSE_día(modelo) < RMSE_día(operador)` (estricto; no se calcula para el propio operador) |

Si el modelo no da cuantiles (el operador) Winkler, cobertura y ancho son `null`; nunca se rellenan. El RMSE del operador se calcula siempre sobre las mismas filas (`rmse_operator`). El operador se recorta a [0, capacidad] igual que los modelos (en la ejecución real no cambió ningún valor: `n_values_clipped_or_reordered` = 0).

## 3. Pliegues y regla del periodo reservado

Walk-forward con ventana creciente, un pliegue por mes natural de `target_date` sobre `split_v01` ∈ {train, valid} (2025-09-02 … 2026-07-31). Entrenamiento del pliegue = todas las filas con `target_date` estrictamente anterior al primer día del mes. El primer pliegue empieza `min_train_months` (3) meses naturales después del primero con datos. Fechas reales:

| Pliegue | Validación | Entrenamiento hasta | Filas / días de entrenamiento | Filas evaluadas |
|---|---|---|---|---|
| 0 | 2025-12-01 → 12-31 | 2025-11-30 | 8 644 / 90 | 2 976 |
| 1 | 2026-01-01 → 01-31 | 2025-12-31 | 11 620 / 121 | 2 976 |
| 2 | 2026-02-01 → 02-28 | 2026-01-31 | 14 596 / 152 | 2 688 |
| 3 | 2026-03-01 → 03-31 | 2026-02-28 | 17 284 / 180 | 2 972 (día de 92 pasos) |
| 4 | 2026-04-01 → 04-30 | 2026-03-31 | 20 256 / 211 | 2 880 |
| 5 | 2026-05-01 → 05-31 | 2026-04-30 | 23 136 / 241 | 2 976 |
| 6 | 2026-06-01 → 06-30 | 2026-05-31 | 26 112 / 272 | 2 880 |
| 7 | 2026-07-01 → 07-31 | 2026-06-30 | 28 992 / 302 | 2 976 |

Total: 23 324 filas, 243 días. El periodo reservado (`holdout`, desde 2026-08-01) **nunca** entra en pliegues ni en entrenamiento de validación (`tests/leakage/test_backtest_folds.py`: entrenamiento < inicio del pliegue, holdout fuera, sin solapes, ventana creciente, mínimo de meses; también sobre la tabla real).

**Holdout (`--holdout`)**: modo explícito. Entrena con todo train + valid y evalúa 2026-08-01 → 2026-10-16 (7 354 filas con realidad, 76 días; se descartan las 38 filas sin generación del último cierre). Cada evaluación añade una línea a `data/backtest/holdout_log.jsonl` (`at`, `run_id`, `model`, `user` del sistema, `gold_version`, veces evaluado antes por modelo y en total); el scorecard y el informe muestran el contador. El aviso por consola es solo informativo: la disciplina (no optimizar hiperparámetros con él) es del equipo.

## 4. Salidas

`data/backtest/runs/<run_id>/` con `run_id = <AAAAMMDDThhmmssZ>-<modelo>-<validation|holdout>`:

| Fichero | Contenido |
|---|---|
| `predictions.parquet` | `forecast_issue_date`, `target_date`, `ts_utc`, `fold` (−1 = holdout), `n_steps_day`, `y_mw`, `cap_mw`, `q10_mw`, `q50_mw`, `q90_mw`, `op_q50_mw` (operador como referencia, solo en `evaluation/`) |
| `metrics.json` | `total`, `by_fold` (con fechas y filas de entrenamiento) y `by_month` |
| `run.json` | modelo, parámetros, modo, fecha, duración, tabla oro (ruta, versión Delta), `lockfile_sha256` de `uv.lock`, `git_commit` (`null` si no hay git/commits), pliegues, `n_values_clipped_or_reordered`, evaluaciones previas del holdout |

`data/backtest/leaderboard.parquet` y `.json`: una fila por ejecución (métricas totales, `rank_rmse` dentro de su modo, `is_latest` por modelo y modo). `data/backtest/report/`: `scorecard.json` y `ranking_modelos.png`, `rmse_por_mes.png`, `cobertura.png`, `historico_ejecuciones.png`, `dias_ejemplo.png`.

## 5. Cómo leer el scorecard (negocio)

- **Mejora frente al operador** = `(RMSE operador − RMSE modelo) / RMSE operador · 100`. Positivo = nos equivocamos menos. Mismas filas para ambos.
- **Veredicto**: «mejora» si > +1 %, «empeora» si < −1 %, «igual» en medio (`rules.verdict_threshold_pct`). `vs_best_previous_*` hace lo mismo frente a la mejor ejecución anterior de cualquier modelo (sin contar el operador); `trend` frente a la ejecución anterior del mismo modelo («primera ejecución» si no la hay).
- **G1**: RMSE del Q50 ≤ operador **y** cobertura del 80 % entre 76 y 84 %, con la razón en lenguaje llano (`g1.reasons`). Un modelo sin rango no cumple G1.
- **G2**: G1 cumplida también en el holdout y RMSE del holdout no más de un 10 % peor que en validación; «sin evaluar» si no se ha mirado el holdout.
- `headline.text` es la frase grande del informe: el mejor modelo (menor RMSE de validación) frente al operador.

## 6. Cómo enchufar un modelo nuevo (F5/F6)

1. Subclase de `predico50hz.models.base.Model` con `name`, `label` (nombre llano para negocio), `params()`, `fit(train)` y `predict(df) -> Prediction(q50, q10=None, q90=None)`. Por defecto `unit = "cf"` (factor de capacidad); con `unit = "mw"` el marco no multiplica.
2. `fit` recibe filas de la tabla oro (con `y_*`); `predict` recibe las filas **sin ninguna columna `y_*`** (lo fuerza y comprueba el marco). Las features salen de `predico50hz.features.registry.feature_columns`.
3. Registrarlo en `predico50hz.models.MODELS`; ya funciona `run --model <name>` y aparece en el informe.
4. El marco convierte a MW con `y_cap_mw` (capacidad MaStR de la fecha UTC del intervalo; la de producción sería `cap_all_mw`, prácticamente igual), baja Q10 / sube Q90 hasta Q50 si lo cruzan, recorta a [0, capacidad] y valida con Pandera: clave única, malla completa y contigua, sin NaN, Q10 ≤ Q50 ≤ Q90, 0 ≤ q ≤ capacidad.

## 7. MLflow

`MlflowRunSink` (`evaluation/mlflow_sink.py`) registra cada ejecución como un run de MLflow (`mlflow-skinny` 3.17.2; el paquete completo exige `pyarrow<26`). Un run por `run_id` (etiqueta `predico.run_id`; registrar dos veces no duplica). Guarda parámetros, métricas totales, métricas por pliegue (`fold_rmse`, `fold_mae`… con `step` = nº de pliegue) y como artefactos todo el directorio de la ejecución (predicciones, `run.json`, `metrics.json`, PNG si los hay).

**Almacén.** `file:data/mlflow` (carpeta ignorada por git). Skinny no trae SQLAlchemy, así que `sqlite:` no funciona; el almacén de ficheros está «en mantenimiento» en 3.17 y exige `MLFLOW_ALLOW_FILE_STORE=true`, que el sumidero fija solo.

**Versión de datos** (etiquetas de cada run): `gold_delta_version` (de `run.json`) y `nwp_icechunk_snapshot`: la última instantánea de `main` del Icechunk NWP escrita antes de `created_at` de la ejecución (el bronce solo crece); `unknown` si el repositorio no es accesible. También `git_commit` y `lockfile_sha256`.

```bash
uv run python -m predico50hz.evaluation run --model operator --mlflow   # registra al ejecutar
uv run python -m predico50hz.evaluation mlflow-sync                     # registra las ya existentes
MLFLOW_ALLOW_FILE_STORE=true uvx --from 'mlflow==3.17.2' mlflow ui \
    --backend-store-uri file:data/mlflow --port 5000                    # UI en http://127.0.0.1:5000
```

## 8. Resultados de las referencias (ejecución del 2026-10-16, tabla oro versión Delta 1)

Validación walk-forward (dic 2025 – jul 2026, 23 324 filas, 243 días):

| Modelo | RMSE Q50 (MW) | vs operador | Winkler | Cobertura 80 % | Ancho medio (MW) | Días mejores que el operador |
|---|---|---|---|---|---|---|
| Operador (ENTSO-E B18+B19) | **931,2** | — | — | — | — | — |
| Curva de potencia (`wshub_all_mean`, tramos de 0,5 m/s) | 1 196,6 | −28,5 % | 3 709,4 | 76,9 % | 2 195,5 | 23,9 % |
| Climatología (hora local × mes) | 3 406,4 | −265,8 % | 11 766,4 | 73,0 % | 7 928,7 | 3 % |
| Persistencia (último valor conocido, margen de 180 min; §8.3) | 3 746,1 | −302,3 % | 11 714,3 | 81,8 % | 8 297,7 | 4 % |

Periodo reservado (2026-08-01 → 10-16, 7 354 filas, 76 días; una evaluación por referencia):

| Modelo | RMSE Q50 (MW) | Winkler | Cobertura | Días mejores que el operador |
|---|---|---|---|---|
| Operador | 886,8 | — | — | — |
| Curva de potencia | 1 125,2 | 3 587,9 | 76,1 % | 21,1 % |
| Climatología | 3 546,3 | 11 784,9 | 82,5 % | 1,3 % |

Otras métricas de la curva de potencia en validación: MAE 778,7 MW (operador 609,4), sesgo +11,6 MW (operador +84,1), error de pico 1 171,6 MW en valor absoluto (operador 800,1) y desfase del pico 3,4 h (operador 3,4 h). Ninguna referencia supera al operador ni cumple G1 (la curva de potencia sí tiene cobertura en banda, 76,9 %, pero no el RMSE). Es lo esperado: son suelos que LightGBM debe batir. Por mes, el RMSE de la curva va de 734 MW (mayo) a 1 873 MW (abril); la cobertura de 69,5 % (enero) a 83,1 % (julio).

Comprobación independiente: el día 2026-03-10 de la curva de potencia, recalculado con Python puro sobre `predictions.parquet`, da RMSE 481,811 MW, Winkler 1 814,787 y cobertura 0,875, idénticos a `summarize`.

### 8.1 LightGBM v0.1 sin optimizar (ejecución del 2026-10-21, tabla oro versión Delta 3)

`models/lgbm.py` (#17): tres modelos LightGBM sobre `y_cf`, Q50 con pérdida L2 y Q10/Q90 con pérdida cuantílica (α 0,1 y 0,9). Variables: toda columna numérica que no sea meta (`features.registry.META`), objetivo (`y_*`) ni disponibilidad (`available_at_*`). **Parada temprana sin look-ahead:** con el último 15 % de los días del propio entrenamiento (mínimo 7, días completos, cronológico); nunca con el pliegue de validación, y el modelo no se reentrena con ese tramo. Hiperparámetros por defecto: `learning_rate` 0,03, `num_leaves` 15, `min_data_in_leaf` 200, `feature_fraction` 0,5, `bagging_fraction` 0,8, `lambda_l2` 1, hasta 1 500 rondas con paciencia 50, semilla 42 y `deterministic=True`. El modelo recorta el cf a [0, 1]; el orden de cuantiles y el paso a MW los hace el marco. Cada ejecución guarda por pliegue `feature_importance.parquet` (ganancia por cuantil) y `lgbm_early_stopping.parquet` (árboles usados), mediante el gancho opcional `Model.artifacts()`.

Las referencias se volvieron a evaluar con la tabla oro versión 3 y dan las mismas cifras que con la versión 1.

| Modelo | RMSE Q50 (MW) | MAE (MW) | Winkler | Cobertura 80 % | Ancho medio (MW) | Días mejores que el operador |
|---|---|---|---|---|---|---|
| Operador | **931,2** | 609,4 | — | — | — | — |
| Curva de potencia | 1 196,6 | 778,7 | **3 709,4** | **76,9 %** | 2 195,5 | 23,9 % |
| LightGBM v0.1 | 1 233,1 | 839,6 | 4 184,9 | 64,2 % | 1 991,0 | 21,0 % |

LightGBM funciona de punta a punta pero aún no bate a la curva de potencia (+3 % de RMSE) y su intervalo es demasiado estrecho (cobertura 64 % frente al 80 % objetivo). Sesgo +38,4 MW; error de pico 1 197,9 MW y desfase del pico 3,7 h. Variables con más ganancia en Q50: `ws100_all_ctrl`, `wshub_land_mean`, `wshub_all_mean`, `v3_land_ctrl` y `ws100_land_ctrl`. Los árboles usados varían mucho entre pliegues (Q50 de 43 a 285; Q10 de 77 a 500): con un tramo de parada temprana de pocos días la elección es ruidosa. Son los puntos de partida para Optuna (ADR 0015) y la calibración conformal (ADR 0014).

### 8.2 Calibración conformal (CQR) de Q10 y Q90 (ejecución del 2026-10-21, tabla oro versión Delta 3)

`models/cqr.py` (#27) y el modelo `lgbm_cqr` (`uv run python -m predico50hz.evaluation run --model lgbm_cqr`). Es el mismo LightGBM v0.1 (mismas predicciones brutas, mismo Q50) con los intervalos corregidos por una CQR propia (ADR 0014, sin MAPIE).

**Método.**

1. Puntuación de conformidad por cuarto de hora, en factor de capacidad: `E = max(q10 − y, y − q90) / capacidad`. Es positiva si la realidad cae fuera del intervalo y negativa si cae dentro.
2. `Q̂` = cuantil empírico de nivel `ceil((n + 1)(1 − α)) / n` de los `n` residuos de la ventana, con `α = 0,2` (`ALPHA` de `metrics.py`). Es la corrección de muestra finita; si el índice supera `n` no se calibra.
3. Ajuste simétrico `[q10 − Q̂·cap, q90 + Q̂·cap]`. `Q̂` **nunca se recorta**: puede ser negativo (el intervalo se estrecha). Tras ajustar se fuerza `q10 ≤ q50 ≤ q90` y `0 ≤ q ≤ capacidad` igual que el resto del marco (`finalize` y el esquema Pandera).

**Ventana y ausencia de look-ahead (ADR 0009).** Para el cierre de `D` (15:00 de Berlín, objetivo `D+1`) la ventana son los 60 cierres consecutivos que terminan en el último cuya realidad estaba publicada antes de ese cierre. La realidad del objetivo de un cierre `D'` (el día local `D'+1`) está completa a las 00:00 de Berlín de `D'+2` más el retraso de publicación de ENTSO-E (sin medir todavía; se asume 6 h, parámetro `cqr_publication_delay_hours`). Eso es anterior a las 15:00 de `D` solo si `D' ≤ D − 2`: el objetivo de `D − 1` es el propio día `D`, que a las 15:00 solo lleva medio día. **El último día objetivo utilizable es `D − 1`**; ni el día que se pronostica (`D+1`) ni el día `D` entran nunca. `last_usable_issue_date` lo calcula con `features.timing.closure_utc` y `target_window`, sin aritmética a mano. Si la ventana tiene menos de 14 días con residuos (`cqr_min_days`), el día **no se ajusta** y queda marcado (`calibrated = False` en `cqr_adjustment.parquet`).

**De dónde salen los residuos.** De las propias predicciones walk-forward ya hechas: cada día de validación se calibra con lo que el modelo de su pliegue (o el del pliegue anterior, para los primeros días del mes) predijo de días anteriores. Esas predicciones son fuera de muestra, porque ningún pliegue entrena con su mes de validación. Es lo más simple que es honesto: no hace falta validación cruzada dentro del entrenamiento ni reentrenar, y reproduce lo que haría producción (predicciones enviadas más la realidad publicada después). Coste: los primeros 14 días del primer pliegue (15 cierres, 1 440 filas de 23 324) no se calibran y el modelo de los primeros días del mes está algo menos entrenado que el del mes que lo calibra. La ejecución con `--holdout` no está permitida para `lgbm_cqr` (no tendría historial previo de predicciones). Cada ejecución guarda `cqr_adjustment.parquet` (por cierre: ventana, nº de días y residuos, `Q̂` en factor de capacidad) y `cqr_raw_intervals.parquet` (intervalos antes y después de calibrar).

Validación walk-forward (dic 2025 – jul 2026, 23 324 filas, 243 días), ejecuciones `20261021T184616Z-lgbm-validation` y `20261021T184758Z-lgbm_cqr-validation`:

| Modelo | RMSE Q50 (MW) | Winkler | Cobertura 80 % | Ancho medio (MW) |
|---|---|---|---|---|
| LightGBM v0.1 | 1 233,1 | 4 184,9 | 64,2 % | 1 991,0 |
| LightGBM v0.1 + CQR | 1 233,1 | **4 001,9** | **79,8 %** | 2 601,8 |

La cobertura pasa de 64,2 a 79,8 % (dentro de la banda 76–84 % de G1) y el Winkler baja un 4,4 %, aunque el ancho medio sube un 31 %: el exceso fuera del intervalo, que el Winkler multiplica por 10, pesa más que el ancho extra. El Q50 no cambia. `Q̂` osciló entre 0,004 y 0,026 de la capacidad (media 0,013).

Por mes (el pliegue 0 es diciembre; la cobertura mensual es una condición sobre ~2 900 filas muy autocorrelacionadas, no una garantía):

| Mes | Cobertura sin CQR | Cobertura con CQR | Winkler sin CQR | Winkler con CQR | Ancho sin CQR | Ancho con CQR |
|---|---|---|---|---|---|---|
| 2025-12 | 65,2 % | 73,9 % | 3 512,8 | 3 445,6 | 1 880,9 | 2 222,2 |
| 2026-01 | 46,6 % | 63,2 % | 6 531,1 | 5 532,1 | 2 033,2 | 2 771,7 |
| 2026-02 | 72,5 % | 90,8 % | 5 107,9 | 5 244,4 | 3 499,9 | 4 654,7 |
| 2026-03 | 62,0 % | 85,8 % | 3 892,3 | 3 767,1 | 1 884,4 | 2 836,1 |
| 2026-04 | 54,5 % | 74,5 % | 5 218,2 | 4 838,7 | 1 687,0 | 2 254,5 |
| 2026-05 | 72,1 % | 89,0 % | 2 558,7 | 2 635,0 | 1 497,2 | 2 075,4 |
| 2026-06 | 68,9 % | 82,1 % | 3 109,0 | 3 065,8 | 1 531,7 | 1 872,8 |
| 2026-07 | 72,7 % | 79,8 % | 3 637,0 | 3 603,2 | 2 035,1 | 2 290,8 |
| **Total** | **64,2 %** | **79,8 %** | **4 184,9** | **4 001,9** | **1 991,0** | **2 601,8** |

**Lectura.** El total cumple el criterio de G1 y el Winkler mejora, pero por mes la cobertura sigue entre 63 y 91 %: la CQR corrige el nivel medio con 60 días de atraso y no puede seguir a un modelo cuyo error cambia de un mes a otro. Enero (63,2 %) es el peor: es el mes con más error del modelo (RMSE 1 607 MW) y su ventana solo contiene diciembre (RMSE 819 MW), un mes mucho más fácil. Febrero y mayo se pasan de ancho (91 y 89 %) porque la ventana arrastra residuos de meses peores; el Winkler empeora un 2,7 % y un 3,0 % en esos dos meses. Con el modelo optimizado (Optuna, ADR 0015) los residuos serán más estables y habrá que repetir esta evaluación.

Pruebas: `tests/unit/test_cqr.py` (cuantil con corrección finita, ajuste y mínimo de días con casos a mano, orden y límites, integración en el marco) y `tests/leakage/test_cqr_leakage.py` (alterar la realidad del día objetivo o posterior no cambia el ajuste; de extremo a extremo con el backtest sintético).

### 8.3 Persistencia: repetir lo último conocido (ejecución del 2026-10-22, tabla oro versión Delta 3)

Validación walk-forward, las mismas 23 324 filas y 243 días, margen de 180 min y modo `last`: RMSE 3 746,1 MW (operador 931,2; −302,3 %), Winkler 11 714,3, cobertura 81,8 %, ancho medio 8 297,7 MW, mejor que el operador en el 4 % de los días (ejecución `20261022T184205Z-persistence-validation`). No cumple G1 (el rango sí está en banda, el error no). No se ha evaluado en el periodo reservado: no hace falta para una referencia que no se optimiza.

- **Corte.** Para el cierre D, `closure_utc(D) - lag_min` (15:00 de Berlín menos el margen: 10:00 UTC en verano y 11:00 UTC en invierno con 180 min). Solo se usan cuartos de hora cuyo `ts_utc` es ≤ al corte; el que empieza justo en el corte entra y el siguiente no. El corte se calcula con `closure_utc` de la fecha D, así que respeta el cambio de hora.
- **Margen de 180 min** (`lag_min`, registrado en `params()`). Sale de las mediciones del issue #11. Del 16 al 22 de octubre de 2026 (unas 40 observaciones) el retraso entre la hora de la consulta y el inicio del último cuarto de hora publicado fue normalmente de 30 a 62 min (peor caso, 62 min a las 13:17 UTC). Pero el 22 de octubre hubo un atasco de ENTSO-E alrededor del cierre: a las 13:01 UTC el último cuarto publicado era el de las 11:45 (76 min), a las 14:39 el de las 12:00 (160 min) y a las 15:01 el de las 12:15 (166 min). Con 90 min habría sobrado solo 15 min en el cierre de ese día; 180 min cubren un atasco de unas 2 h 45 min.
- **Coste del margen.** Con `lag_min=90` (ejecución de comparación `20261022T184200Z-persistence-validation`) el RMSE es 3 669,7 MW, Winkler 11 475,3 y cobertura 82,4 %; con 180 min sube a 3 746,1 MW (+2,1 %). Es poco porque el día objetivo está tan lejos del corte que 90 min más de antigüedad casi no cambian la referencia.
- **Modo.** Se evaluaron los dos con el margen de 90 min: `last` (último valor conocido, plano todo el día objetivo) RMSE 3 669,7 MW, `profile` (mismo cuarto de hora del día conocido más reciente, últimas 24 h alineadas por hora UTC) RMSE 3 983,5 MW, `20261022T183851Z-persistence-validation`. Por defecto `mode="last"`, el de menor RMSE. El día objetivo está entre 10,5 y 34,5 h (más con el margen) después del corte, así que el perfil de ayer no tiene más información que el último valor y, además, el viento cambia de un día a otro; se alinea por UTC (un desplazamiento fijo de 24 h; los dos días al año del cambio de hora el perfil se desplaza 1 h).
- **Unidad: MW.** El marco quita todas las columnas `y_*` antes de `predict`, incluida `y_cap_mw`, así que el modelo no puede pasar a factor de capacidad; el marco solo recorta a [0, capacidad].
- **Intervalo.** `fit(train)` calcula la persistencia sobre los cierres de entrenamiento y toma los cuantiles Q10 y Q90 del error (real − persistencia) por tramo de horas desde el corte (`horizon_edges_h` = 12, 18, 24 y 30 h: cinco tramos). Un tramo con menos de `min_count` = 200 filas usa los cuantiles de todo el entrenamiento. Q10 = Q50 + cuantil del error, y análogo para Q90. El Q50 es la persistencia pura, sin corregir el sesgo (−956 MW en validación): es una referencia, no un modelo, y corregirlo la convertiría en uno. Sin `fit` solo da Q50.
- **Huecos.** Se excluyen los puntos con `is_gap_filled` y los nulos: el relleno interpola con valores posteriores al hueco y cerca del corte podría usar datos que aún no existían. Si el valor `last` más reciente tiene más de `max_stale_min` = 360 min antes del corte, la fila sale NaN y la validación del marco la rechaza, igual que con el operador; en la tabla real no ocurre (la serie de plata no tiene puntos rellenados ni huecos en los cierres evaluados). En `profile`, si falta el punto alineado se usa el último valor.

Pruebas: `tests/unit/test_models_persistence.py` (corte inclusivo con horario de verano e invierno, alterar la generación posterior al corte no cambia ninguna predicción ni cuantil, modos con serie sintética conocida, exclusión de puntos rellenados y obsolescencia, cuantiles ordenados).

## 9. Limitaciones y no verificado

- **Persistencia:** el margen de 180 min sale de una sola semana de medidas de octubre de 2026 (~40 observaciones: retraso normal de 30 a 62 min y un atasco de hasta 166 min el 22 de octubre); en otras épocas o con incidencias más largas de ENTSO-E podría ser mayor. Además, la generación histórica de la plata es la **última revisión**, no la que estaba publicada en el momento del cierre, así que el valor «último conocido» puede ser algo mejor que el real. Con el mismo cuidado hay que leer su RMSE.
- **Operador:** solo hay su valor central (sin Winkler ni cobertura) y la serie de `silver` es la **última revisión** descargada (2026-10-16), no necesariamente la que había publicada el día antes: puede ser algo mejor que la real. Su hora de publicación sigue sin medirse.
- **Holdout de verano-otoño con poco viento** (`y_cf` medio 0,181 frente a 0,203 en train): no garantiza diciembre. La validación solo tiene un invierno, y ya forma parte del entrenamiento a partir de febrero: con 13 meses los modelos no han visto nunca el mismo mes natural previo (por eso la climatología usa los meses vecinos ±1 y, si no hay, todo el entrenamiento).
- **Cobertura en meses sueltos** oscila entre 69 y 83 %: con ~2 900 filas por mes y autocorrelación fuerte, la banda 76–84 % del total es una condición sobre 8 meses, no una garantía por mes. La CQR (§8.2) lleva el total al 79,8 %, pero por mes sigue entre 63 y 91 %.
- El **denominador** (`y_cap_mw`) usa MaStR con altas registradas a posteriori (`docs/f3-features.md` §11), igual para modelo y operador (el operador se compara en MW, sin conversión).
- Las clasificaciones se hacen con una única tabla oro (410 cierres); al ampliar el histórico (etapa 3) los pliegues cambiarán y habrá que repetir las ejecuciones.
- El contador del holdout detecta mirar demasiado, no lo impide; las ejecuciones se pueden borrar a mano de `data/backtest/`, pero el registro `holdout_log.jsonl` es de solo añadir.
- Pliegues de entrenamiento muy cortos al principio (90 días en el primero): el error de diciembre refleja eso.
- No se ha verificado el capítulo del informe en móvil; sí en escritorio claro con capturas de Chromium (el modo oscuro usa los mismos tokens de color).

## 10. Test de integración extremo a extremo

`tests/integration/` recorre la cadena entera con datos sintéticos y deterministas (semilla fija, todo en `tmp_path`, sin red, sin `data/` ni `/mnt/data`): ingesta → plata → oro → entrenamiento → métricas. Se lanza con `uv run pytest tests/integration -q --durations=5` (marcador `integration`, unos 40 s) y entra en la CI con `pytest -m "not data"`.

| Etapa | Qué se ejecuta de verdad | Qué se fabrica (frontera) |
|---|---|---|
| MaStR | `ingest.mastr.pipeline.run`: unidades, zona, k-means, capacidad diaria, plata y `centroids.yaml`, con `capacity_schema`/`units_schema` | ZIP del export con XML UTF-16 (catálogo, unidades, puntos de conexión) |
| NWP | `read_cube` → `BronzeStore` (Icechunk local) → `build_table`/`write_silver`, con `validate_silver` en una muestra de pasadas | `Source` en memoria por pasada (en lugar de `open_source`, que lee el Icechunk remoto) |
| ENTSO-E | `EntsoeClient` y `fetch_dataset` → bronce XML gzip → `build_silver` y `validate` (generación y pronóstico del operador; una descarga de julio errónea y otra posterior que la corrige) | `httpx.MockTransport` que contesta XML `GL_MarketDocument` |
| Oro | `python -m predico50hz.features` (construcción, escritura Delta y esquema Pandera) más `day_issues` y consistencia con las plata | — |
| Backtest | `run_backtest` con `operator`, `climatology`, `power_curve` y `lgbm` (más un holdout), `validate_predictions`, `summarize`, clasificación y `scorecard` | — |

Los datos salen todos de una misma función de viento (`tests/integration/chain_synth.py`): el NWP la lleva con sesgo por pasada y dispersión de miembros, la generación es capacidad MaStR por una curva cúbica con ruido y el «operador» es esa generación con error. Así LightGBM, que se entrena con pocos días, debe batir a la climatología (se exige RMSE < 0,6 × el de la climatología, y los intervalos y métricas deben ser finitos y completos). Cubre 65 cierres entre el 26-mar y el 31-jul-2026, incluye el día de 92 pasos (cambio de hora del 29-mar) y un día de holdout; usa `--min-train-months 2` para obtener tres pliegues (mayo, junio, julio).

No cubre: la ingesta IFS de ECMWF (`ingest/ifs`), la búsqueda automática de `k` en MaStR (necesita miles de unidades), las plata de precios y capacidad de ENTSO-E (no las consume la oro), la orquestación, el informe ejecutivo ni el sumidero MLflow. Las cifras de error no dicen nada del rendimiento real: solo comprueban que la cadena es coherente de punta a punta.
