# F4 — Marco de backtest y métricas

Cubre F4.1 a F4.3 y las referencias mínimas de F5 (climatología, curva de potencia, operador y persistencia). Código en `src/predico50hz/evaluation/` (marco) y `src/predico50hz/models/` (interfaz y referencias); tests en `tests/unit/test_backtest_*.py` y `tests/leakage/test_backtest_folds.py` y de extremo a extremo en `tests/integration/` (§10); salidas en `data/backtest/` (ignorado por git). Todo el tiempo es UTC salvo que se diga lo contrario.

## 1. Cómo ejecutarlo

```
uv run python -m predico50hz.evaluation run --model climatology        # validación walk-forward
uv run python -m predico50hz.evaluation run --model power_curve
uv run python -m predico50hz.evaluation run --model operator
uv run python -m predico50hz.evaluation run --model persistence       # repetir lo último conocido (§8.3)
uv run python -m predico50hz.evaluation run --model lgbm_cqr           # LightGBM + calibración conformal (§8.2)
uv run python -m predico50hz.evaluation run --model lgbm_tuned_cqr    # LightGBM optimizado con Optuna + CQR (§8.6)
uv run python -m predico50hz.evaluation tune --quantile q50 --trials 150 --workers 24 --folds even --mlflow   # estudio de Optuna (§8.6)
uv run python -m predico50hz.evaluation tune-export                     # mejores intentos → config/lgbm_tuned.yaml  (ojo: reescribe el Q50 con el estudio `lgbm-q50`; el adoptado es `lgbm-wide-q50`, ADR 0033)
uv run python -m predico50hz.evaluation run --model power_curve --holdout   # periodo reservado (queda registrado)
uv run python -m predico50hz.evaluation compare                         # clasificación de las últimas ejecuciones
uv run python -m predico50hz.evaluation report                          # scorecard.json + PNG
uv run python -m predico50hz.report                                     # informe ejecutivo (capítulo «¿Mejora el pronóstico?»)
```

Opciones de `run`: `--min-train-months N` (6 por defecto), `--param clave=valor` (repetible, JSON; parámetros del modelo), `--gold`, `--operator`, `--root`. `report` acepta `--no-png` y `--out`. Si el periodo reservado ya se evaluó con ese modelo, `run --holdout` avisa por stderr (no bloquea).

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
| `models/tuning.py` | Estudios de Optuna por cuantil, poda y exportación (§8.6); `config/lgbm_tuned.yaml` guarda los mejores parámetros |
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

Walk-forward con ventana creciente, un pliegue por mes natural de `target_date` sobre `split_v01` ∈ {train, valid} (2024-04-02 … 2026-07-31: 28 meses de desarrollo, 1 abr 2024 – 31 jul 2026). Entrenamiento del pliegue = todas las filas con `target_date` estrictamente anterior al primer día del mes. El primer pliegue empieza `min_train_months` (**6**) meses naturales después del primero con datos.

**Por qué 6 meses.** Con 3 (el valor de la v0.1 con 13 meses) el primer pliegue entrenaba con 90 días y los tres primeros con 90 a 152: el error de diciembre reflejaba el entrenamiento corto, no el modelo. Con 6, el primer pliegue valida octubre de 2024 con 182 días de entrenamiento, los de diciembre de 2024 y enero de 2025 entrenan con 243 y 274 días y quedan 22 pliegues. El invierno 2024/25 (dic, ene, feb) entra entero en validación; sería así con cualquier valor hasta 8, que es el máximo (con 9 se perdería el diciembre de 2024). Se eligió 6 y no 8 porque medio año es el mínimo con el que el modelo ha visto dos estaciones distintas (primavera-verano) y porque deja octubre y noviembre de 2024 en validación: es el único tramo evaluado con la racha ausente en entrenamiento y validación (§8.4). Coincide con el fin de `train` en `split_v01` (un test lo comprueba).

**`split_v01` (ADR 0010, redefinido con el histórico completo).** `train` = 2024-04-01 → 2024-09-30 (los 6 meses que solo entrenan), `valid` = 2024-10-01 → 2026-07-31 (los meses que se validan uno a uno, que además entrenan los pliegues siguientes) y `holdout` = desde 2026-08-01, sin cambios. Las 3 etiquetas siguen siendo meta, nunca feature.

| Pliegue | Validación | Entrenamiento hasta | Filas / días de entrenamiento |
|---|---|---|---|
| 0 | 2024-10-01 → 2024-10-31 | 2024-09-30 | 17 472 / 182 |
| 1 | 2024-11-01 → 2024-11-30 | 2024-10-31 | 20 452 / 213 |
| 2 | 2024-12-01 → 2024-12-31 | 2024-11-30 | 23 332 / 243 |
| 3 | 2025-01-01 → 2025-01-31 | 2024-12-31 | 26 308 / 274 |
| 4 | 2025-02-01 → 2025-02-28 | 2025-01-31 | 29 284 / 305 |
| 5 | 2025-03-01 → 2025-03-31 | 2025-02-28 | 31 972 / 333 |
| 6 | 2025-04-01 → 2025-04-30 | 2025-03-31 | 34 944 / 364 |
| 7 | 2025-05-01 → 2025-05-31 | 2025-04-30 | 37 824 / 394 |
| 8 | 2025-06-01 → 2025-06-30 | 2025-05-31 | 40 800 / 425 |
| 9 | 2025-07-01 → 2025-07-31 | 2025-06-30 | 43 680 / 455 |
| 10 | 2025-08-01 → 2025-08-31 | 2025-07-31 | 46 656 / 486 |
| 11 | 2025-09-01 → 2025-09-30 | 2025-08-31 | 49 632 / 517 |
| 12 | 2025-10-01 → 2025-10-31 | 2025-09-30 | 52 512 / 547 |
| 13 | 2025-11-01 → 2025-11-30 | 2025-10-31 | 55 492 / 578 |
| 14 | 2025-12-01 → 2025-12-31 | 2025-11-30 | 58 372 / 608 |
| 15 | 2026-01-01 → 2026-01-31 | 2025-12-31 | 61 348 / 639 |
| 16 | 2026-02-01 → 2026-02-28 | 2026-01-31 | 64 324 / 670 |
| 17 | 2026-03-01 → 2026-03-31 | 2026-02-28 | 67 012 / 698 |
| 18 | 2026-04-01 → 2026-04-30 | 2026-03-31 | 69 984 / 729 |
| 19 | 2026-05-01 → 2026-05-31 | 2026-04-30 | 72 864 / 759 |
| 20 | 2026-06-01 → 2026-06-30 | 2026-05-31 | 75 840 / 790 |
| 21 | 2026-07-01 → 2026-07-31 | 2026-06-30 | 78 720 / 820 |

Total: 64 224 filas evaluadas, 669 días (92 y 100 pasos en 4 días de cambio de hora). Con la tabla anterior (410 cierres) eran 8 pliegues, de dic 2025 a jul 2026 (23 324 filas, 243 días); esos 8 meses siguen siendo parte de los nuevos pliegues y se reportan aparte en §8.4 (con modelos entrenados con más historia, así que no son idénticos a los de antes).

El periodo reservado (`holdout`, desde 2026-08-01) **nunca** entra en pliegues ni en entrenamiento de validación (`tests/leakage/test_backtest_folds.py`: entrenamiento < inicio del pliegue, holdout fuera, sin solapes, ventana creciente, mínimo de meses; también sobre la tabla real).

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

> §8 a §8.3 son las cifras con **410 cierres** (sep 2025 – oct 2026, 8 pliegues). Quedan como historial; las vigentes con los 30 meses están en §8.4.

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

### 8.4 Histórico completo: 30 meses, 22 pliegues (ejecución del 2026-10-23, tabla oro versión Delta 7)

Tabla oro regenerada desde 2024-04-01 (936 cierres, 89 856 filas; `docs/f3-features.md` §6). Validación walk-forward con los 22 pliegues de §3: **64 224 filas, 669 días, oct 2024 – jul 2026**. Ejecuciones (`--mlflow`): `20261023T163349Z-operator-validation`, `20261023T163351Z-climatology-validation`, `20261023T163354Z-power_curve-validation`, `20261023T163357Z-persistence-validation`, `20261023T163749Z-lgbm-validation`, `20261023T164140Z-lgbm_cqr-validation`. El periodo reservado no se ha evaluado.

**Validación completa (oct 2024 – jul 2026):**

| Modelo | RMSE Q50 (MW) | vs operador | Winkler | Cobertura 80 % | Ancho medio (MW) | Días mejores que el operador |
|---|---|---|---|---|---|---|
| Operador (ENTSO-E B18+B19) | **876,6** | — | — | — | — | — |
| Curva de potencia | 1 103,9 | −25,9 % | 3 514,6 | 81,4 % | 2 431,4 | 22,4 % |
| LightGBM v0.1 | **1 060,8** | −21,0 % | 3 450,6 | 71,2 % | 2 022,3 | 25,6 % |
| LightGBM v0.1 + CQR | 1 060,8 | −21,0 % | **3 396,5** | 79,5 % | 2 310,1 | 25,6 % |
| Climatología | 3 561,1 | −306,3 % | 11 806,2 | 79,1 % | 8 993,9 | 2,2 % |
| Persistencia | 3 881,3 | −342,8 % | 12 464,8 | 79,1 % | 7 689,8 | 3,3 % |

Otras métricas en validación (operador / curva de potencia / LightGBM): MAE 589,9 / 765,1 / 741,1 MW; sesgo +43,9 / −30,9 / −19,3 MW; error de pico 758,5 / 1 059,6 / 1 013,2 MW y desfase del pico 3,0 / 3,4 / 3,3 h.

**Periodo antiguo (dic 2025 – jul 2026, 23 324 filas, 243 días; los 8 pliegues de antes), para comparar con §8 a §8.3.** Mismas predicciones de la ejecución completa, restringidas a esas fechas; los modelos de cada mes ahora entrenan con 608 a 820 días (antes 90 a 302).

| Modelo | RMSE antes (410 cierres) | RMSE ahora | Winkler ahora | Cobertura ahora | Días mejores que el operador |
|---|---|---|---|---|---|
| Operador | 931,2 | 931,2 | — | — | — |
| Curva de potencia | 1 196,6 | 1 197,8 | 3 697,0 | 82,5 % | 22,6 % |
| LightGBM v0.1 | 1 233,1 | **1 128,7** | 3 529,2 | 70,9 % | 23,9 % |
| LightGBM v0.1 + CQR | 1 233,1 | 1 128,7 | **3 490,9** | 79,0 % | 23,9 % |
| Climatología | 3 406,4 | 3 300,6 | 11 346,0 | 81,9 % | 3,3 % |
| Persistencia | 3 746,1 | 3 746,1 | 11 783,9 | 79,6 % | 4,1 % |

El operador y la persistencia no cambian porque no entrenan (la persistencia solo calibra su intervalo). La curva de potencia apenas se mueve: es una función simple de una sola variable y ya estaba estable con 90 días. **LightGBM sí mejora con más historia**: −8,5 % de RMSE (de 1 233,1 a 1 128,7 MW) y pasa a ganar a la curva de potencia (−5,8 %) en este mismo periodo, donde antes perdía (+3 %). Sigue un 21,2 % por detrás del operador.

**Invierno 2024/25 (dic 2024 – feb 2025, 8 640 filas)**, el de más viento, que antes no estaba en validación: operador 901,5 MW, curva de potencia 1 149,4, LightGBM 1 187,8 (cobertura 67,8 %; con CQR 80,7 %, Winkler 4 008,5). **En invierno LightGBM no gana a la curva de potencia** (+3,3 % de RMSE), igual que en la v0.1: con 8 a 10 meses de entrenamiento previo no ha visto nunca un invierno. Es el periodo donde más cuesta la cobertura del intervalo sin CQR.

Por mes (RMSE en MW; cobertura del 80 % de LightGBM sin y con CQR):

| Mes | Operador | Curva de potencia | LightGBM | Cobertura LightGBM | Cobertura con CQR |
|---|---|---|---|---|---|
| 2024-10 | 899 | 955 | 994 | 71,8 % | 74,2 % |
| 2024-11 | 857 | 1 174 | 1 229 | 58,1 % | 78,6 % |
| 2024-12 | 903 | 1 300 | 1 338 | 63,9 % | 78,7 % |
| 2025-01 | 1 071 | 1 220 | 1 304 | 69,3 % | 80,8 % |
| 2025-02 | 663 | 852 | 819 | 70,5 % | 82,8 % |
| 2025-03 | 795 | 995 | 945 | 64,7 % | 78,6 % |
| 2025-04 | 787 | 994 | 980 | 64,8 % | 78,9 % |
| 2025-05 | 765 | 1 002 | 931 | 77,3 % | 86,8 % |
| 2025-06 | 959 | 1 256 | 1 131 | 77,2 % | 81,2 % |
| 2025-07 | 746 | 984 | 953 | 80,4 % | 83,6 % |
| 2025-08 | 616 | 762 | 657 | 79,8 % | 80,6 % |
| 2025-09 | 941 | 1 009 | 898 | 73,1 % | 74,3 % |
| 2025-10 | 860 | 1 055 | 952 | 71,7 % | 78,0 % |
| 2025-11 | 830 | 934 | 909 | 75,8 % | 81,0 % |
| 2025-12 | 849 | 1 009 | 875 | 76,0 % | 80,4 % |
| 2026-01 | 992 | 1 342 | 1 416 | 57,4 % | 64,1 % |
| 2026-02 | 919 | 1 156 | 1 240 | 62,0 % | 79,5 % |
| 2026-03 | 805 | 871 | 770 | 80,4 % | 92,9 % |
| 2026-04 | 1 476 | 1 868 | 1 647 | 66,4 % | 73,9 % |
| 2026-05 | 587 | 728 | 725 | 80,1 % | 85,3 % |
| 2026-06 | 675 | 1 131 | 966 | 69,1 % | 76,5 % |
| 2026-07 | 888 | 1 147 | 1 087 | 74,6 % | 79,1 % |

**Lectura.**

- Con el histórico completo LightGBM v0.1 mejora a la curva de potencia (−3,9 % de RMSE en todo el periodo) pero el operador sigue por delante (+21 % de error). No cumple G1 (el error pesa) aunque con CQR la cobertura (79,5 %) y el Winkler (el mejor de todos) sí lo hacen. En el periodo de antes (dic 2025 – jul 2026) la brecha con el operador baja de +32 % a +21 %.
- Los meses fríos con más viento (nov 2024 – ene 2025 y ene – feb 2026) son los peores del modelo respecto a la curva de potencia y a su propia cobertura: enero de 2026 sigue siendo el peor (cobertura 57,4 % sin CQR y 64,1 % con CQR; el intervalo del mes anterior no sirve para un mes mucho más difícil, igual que en §8.2).
- La cobertura de la CQR por mes va del 64 al 93 %: el total (79,5 %) está en banda, pero el error cambia mucho de un mes a otro y la ventana de 60 días va con retraso (`models/cqr.py`). Los 14 primeros días del primer pliegue no se calibran.

**Racha antes del 2024-11-13 (decisión: NaN nativa de LightGBM + `gust_missing`).** 226 cierres (21 700 filas, 24 % de la tabla) no tienen racha; las columnas `gust_*` y `gustfac_*` están en NaN y `gust_missing` = 1 (comprobado con datos reales y Pandera: coincide exactamente con los cierres anteriores al 2024-11-13; el 2024-11-12 es el último sin racha y el 11-13 el primero con ella). Para decidir entre NaN y quitar la variable se repitió LightGBM sin ninguna columna que empiece por `gust` (parámetro `exclude_prefixes=["gust"]`, ejecución de prueba aparte, no entra en el scorecard): RMSE 1 054,3 MW frente a 1 060,8 con ella (−0,6 %; Winkler 3 441,2 frente a 3 450,6; cobertura 71,3 % frente a 71,2 %). Por tramos: oct 24 – 12 nov 24 idéntico (la racha es constante en NaN en entrenamiento y validación), 13 nov 24 – nov 25 −0,9 %, dic 25 – jul 26 −0,4 %, invierno 24/25 +0,7 %: todo dentro del ruido. La racha aporta el 0,2 % de la ganancia en Q50 y el 1 % en Q10/Q90 (sobre todo `gust_sea_p90`, `gust_sea_mean`, `gust_land_mean`). Conclusión: la NaN nativa funciona (no rompe nada y da prácticamente lo mismo que quitar la variable), así que se mantiene el camino existente: es el que usará la producción cuando falle una racha y no obliga a descartar los 7 primeros meses. No se encontró que la racha ayude al error central; el Q10/Q90 podría aprovecharla algo más, y se vuelve a medir tras Optuna. Riesgo menor: `gust_missing` es un indicador de época (solo vale 1 antes de nov 2024) y los árboles podrían usarlo como atajo temporal en el entrenamiento; su ganancia es ≈ 0, y nunca vale 1 en los pliegues posteriores a noviembre de 2024, así que no puede afectar a esos meses.

Pruebas: `tests/leakage/test_backtest_folds.py` (también sobre la tabla real: primer pliegue 2024-10, último 2026-07, 22 pliegues, coherencia con `split_v01`), `tests/unit/test_features_gold.py::test_split_boundaries` y `tests/leakage/test_leakage_features.py` sobre la tabla nueva.

### 8.5 Precio day-ahead de D: con y sin el bloque `price` (ejecución del 2026-10-23, tabla oro versión Delta 8)

Prueba de #29 (ADR 0031) con la misma validación de 22 pliegues (64 224 filas, 669 días, oct 2024 – jul 2026); el periodo reservado no se ha evaluado. La tabla oro versión 8 añade 5 columnas `price_*` y `available_at_price` (`docs/f3-features.md` §9). Se repitió LightGBM y LightGBM + CQR con y sin ellas (`--param 'exclude_prefixes=["price_"]'`), sin tocar nada más.

| Ejecución | RMSE Q50 (MW) | Winkler | Cobertura 80 % |
|---|---|---|---|
| `20261023T171003Z-lgbm-validation` (sin precio) | 1 060,8 | 3 450,6 | 71,2 % |
| `20261023T171006Z-lgbm-validation` (con precio) | 1 054,3 | 3 446,6 | 71,3 % |
| `20261023T171607Z-lgbm_cqr-validation` (sin precio) | 1 060,8 | 3 396,5 | 79,5 % |
| `20261023T171611Z-lgbm_cqr-validation` (con precio) | 1 054,3 | 3 386,1 | 79,5 % |

Las dos de «sin precio» reproducen bit a bit las de §8.4 (`20261023T163749Z-lgbm-validation`, `20261023T164140Z-lgbm_cqr-validation`), así que la tabla nueva no cambia nada si el bloque no entra.

- **Total:** RMSE −0,6 %, Winkler −0,1 % (LightGBM) y −0,3 % (con CQR), cobertura igual. Mejoran 13 de los 22 pliegues en RMSE (12 en Winkler con CQR); la variación por pliegue va de −4,6 % a +2,4 %. Bootstrap por día: −0,61 %, intervalo del 95 % de −1,34 % a +0,11 %.
- **Días con precio negativo.** Precio de D con horas negativas (17 284 filas): 1 176,5 → 1 166,9 MW (−0,8 %). Día objetivo con horas negativas (solo análisis; el precio de D+1 no es variable; 17 276 filas): 1 189,4 → 1 185,3 MW (−0,3 %). Sin horas negativas: 1 009,4 → 1 001,8 MW (−0,8 %). El bloque no ayuda más en los días de curtailment que en los demás; esos días son los de más error en los dos casos.
- **Decisión:** no mejora de forma clara (dentro del ruido) → excluido del modelo por defecto (`models/lgbm.py::DEFAULT_EXCLUDE`). Las ejecuciones por defecto son las de siempre (`20261023T172307Z-lgbm-validation`, `20261023T172311Z-lgbm_cqr-validation`, mismas cifras que sin precio) y el scorecard las refleja; su `trend` compara con la ejecución con precio y dice «igual» (−0,6 %). Se activa con `--param 'exclude_prefixes=[]'`.

### 8.6 Optimización con Optuna: hiperparámetros por cuantil (ejecución del 2026-10-23, tabla oro versión Delta 8)

Tarea #32 (ADR 0015). Se optimizó LightGBM con Optuna y se evaluó con la misma validación de 22 pliegues (64 224 filas, 669 días, oct 2024 – jul 2026). El periodo reservado no se ha evaluado ni ha entrado en la optimización (`tests/leakage/test_tuning_leakage.py`).

**Resultado en una línea.** Optimizar los hiperparámetros apenas mejora el modelo: el RMSE del Q50 baja de 1 060,8 a 1 054,6 MW (−0,6 %) y el operador sigue en 876,6 MW (+20,3 % frente a +21,0 % antes). En los pliegues que Optuna no vio la mejora es nula (+0,1 %). Los valores por defecto de la v0.1 ya estaban cerca del óptimo; la brecha con el operador no se cierra por esta vía.

**Método.** Código en `models/tuning.py`, órdenes en §1.

- **Un estudio por cuantil** (`lgbm-q50`, `lgbm-q10`, `lgbm-q90`), en un solo SQLite (`data/optuna/predico.db`, ignorado por git). Muestreador TPE multivariante con semilla 42 (+ nº de proceso) y `constant_liar` (varios procesos a la vez), 20 intentos aleatorios iniciales, poda por mediana (`MedianPruner`, sin podar antes de 20 intentos completos ni de 2 pliegues).
- **Espacio exacto del ADR 0015:** `num_leaves` 15–255, `learning_rate` 0,01–0,1 (log), `min_data_in_leaf` 20–500, `feature_fraction` y `bagging_fraction` 0,5–1, `lambda_l1` y `lambda_l2` 10⁻³–10 (log). Hasta 2 000 rondas con paciencia 50.
- **Número de árboles** por la parada temprana que ya usaba el modelo (último 15 % de los días del propio entrenamiento, nunca el pliegue de validación): sin mirar al futuro.
- **Objetivo:** media, sobre los pliegues de optimización, de una métrica en MW. Q50: RMSE (la métrica de G1). Q10 y Q90: pérdida cuantílica del propio cuantil. Se eligió ésta y no el Winkler tras CQR porque deja cada estudio independiente (el Winkler necesita Q10 y Q90 a la vez y la CQR encadena pliegues) y es la pérdida con la que se entrena cada cuantil; la CQR corrige la cobertura después. Tras cada pliegue se informa la media acumulada, y la poda corta los intentos que van peor que la mediana.
- **Intento 0 de cada estudio = la v0.1** (hojas 15, tasa 0,03, mínimo 200, variables 0,5, filas 0,8, L1 0,001, L2 1), para medirla con el mismo criterio. `lambda_l1` de la v0.1 es 0 y en el espacio es ≥ 0,001; la diferencia no se nota.
- **Registro:** cada estudio es un run padre de MLflow (experimento `predico50hz-optuna`, `file:data/mlflow`) con los mejores parámetros, la importancia y las cuentas de intentos, y **cada intento un run hijo anidado** (hiperparámetros, `fold_value_mw` y `fold_trees` con `step` = nº de pliegue, `running_mean_mw`, etiqueta `trial_state` completado/podado). Un callback de Optuna lo escribe al terminar cada intento, en directo, sin frenar el estudio. Los estudios de esta sección se lanzaron antes y se volcaron después con `tuning.log_mlflow` (run padre con etiqueta `backfill`); los runs planos anteriores se quedan. El SQLite sirve para Optuna Dashboard (`uvx optuna-dashboard sqlite:///data/optuna/predico.db`; no probado). Ids de los runs: q50 `09df1800272d483286ba72bd5581d586`, q10 `253d864e5f18410b9f2c730104ff81b5`, q90 `9ba728541eb440e9984f4ae17f9489f5`.
- **Mejores parámetros** en `config/lgbm_tuned.yaml` (versionado). Los modelos `lgbm_tuned` y `lgbm_tuned_cqr` lo cargan (un conjunto por cuantil, `quantile_params`); `lgbm` y `lgbm_cqr` no cambian (reproducen bit a bit las cifras de §8.4: 1 060,8 MW).

**Presupuesto y tiempos.** Los 150 intentos × 22 pliegues completos (~10 h en serie) no cabían. Se hizo así:

1. Cada intento entrena un solo cuantil con **1 hilo**, y se lanzan **24 procesos** sobre el mismo SQLite (con ~90 000 filas, un hilo por proceso rinde mucho más que 32 hilos en un modelo; en un pliegue grande 1 hilo tarda 6,8 s y 32 hilos 2,7 s). El SQLite va en modo WAL con espera larga.
2. **Pliegues alternos:** se optimiza solo con los 11 pliegues pares (oct 2024, dic, feb, abr, jun, ago, oct 2025, dic, feb 2026, abr, jun) y los 11 impares (nov 2024, ene 2025, mar, may, jul, sep, nov, ene 2026, mar, may, jul) quedan sin usar. Cada mitad contiene un invierno entero de cada año.
3. Poda por pliegue.

Con esto cada estudio de 150 intentos tardó de 16 a 29 minutos de reloj y **todo el trabajo (3 estudios, estudio exploratorio, 4 evaluaciones completas) unas 2 h**, frente a las 4–5 h previstas, así que **no hubo que recortar a 50 intentos** (ADR 0006). Intentos por estudio:

| Estudio | Intentos | Completos | Podados | Tiempo | Intento 0 (v0.1) | Mejor | Cambio |
|---|---|---|---|---|---|---|---|
| Q50 (RMSE, MW) | 150 | 130 | 20 | 23 min | 1 050,6 | 1 040,7 (nº 123) | −0,9 % |
| Q10 (pinball, MW) | 150 | 88 | 62 | 25 + 4 min | 172,2 | 167,5 (nº 87) | −2,8 % |
| Q90 (pinball, MW) | 150 | 83 | 67 | 16 min | 178,3 | 176,7 (nº 89) | −0,9 % |

Incidente: en Q10 dos procesos se pararon con «Cannot tell a COMPLETE trial». La causa: dos procesos sacaron a la vez el intento encolado (la v0.1), los dos lo entrenaron y el segundo falló al cerrarlo. Se lanzaron 12 intentos más para llegar a 150 y el código se corrigió (solo el proceso 0 toma la v0.1 y los demás esperan a que empiece; lo cubre un test); en el estudio exploratorio, lanzado con una versión intermedia que ignoraba el error, se perdió un intento (149). Los estudios Q50 y Q90 salieron con 150 y el intento 0 está completo en los cuatro. Las cifras de la tabla son de la media por pliegue en los 11 pliegues de optimización (media de RMSE por pliegue, no RMSE conjunto).

**Mejores parámetros** (`config/lgbm_tuned.yaml`):

| Parámetro | Q10 | Q50 | Q90 | v0.1 |
|---|---|---|---|---|
| `num_leaves` | 242 | 21 | 15 | 15 |
| `learning_rate` | 0,0156 | 0,0353 | 0,0761 | 0,03 |
| `min_data_in_leaf` | 410 | 212 | 184 | 200 |
| `feature_fraction` | 0,536 | 0,522 | 0,547 | 0,5 |
| `bagging_fraction` | 0,780 | 0,818 | 0,944 | 0,8 |
| `lambda_l1` | 0,0027 | 0,0015 | 0,0011 | 0 |
| `lambda_l2` | 0,0079 | 0,0326 | 6,55 | 1 |

**Importancia de hiperparámetros** (PED-ANOVA de `optuna.importance`, que no necesita scikit-learn; no se usó Optuna Dashboard). Suma 1 por estudio:

| Parámetro | Q50 | Q10 | Q90 |
|---|---|---|---|
| `num_leaves` | 0,30 | 0,09 | 0,24 |
| `feature_fraction` | 0,30 | 0,22 | 0,07 |
| `learning_rate` | 0,10 | 0,24 | 0,35 |
| `lambda_l1` | 0,13 | 0,03 | 0,12 |
| `bagging_fraction` | 0,08 | 0,12 | 0,18 |
| `lambda_l2` | 0,06 | 0,07 | 0,02 |
| `min_data_in_leaf` | 0,02 | 0,24 | 0,03 |

En el Q50 pesan el tamaño de los árboles y la fracción de variables; en los extremos, la tasa de aprendizaje y el mínimo de filas por hoja. Los mejores del Q50 y el Q90 quedan en el borde del espacio (hojas 21 y 15, variables ≈ 0,52–0,55): el óptimo parece estar fuera, hacia árboles más pequeños y menos variables por árbol. Además la mediana de los intentos completos del Q50 (1 051,1 MW) es igual que la v0.1 (1 050,6): el terreno es plano. En el Q90 la mediana (180,9) es peor que la v0.1 (178,3).

**Resultados (validación completa, 22 pliegues).** Ejecuciones (`--mlflow`): v0.1 `20261023T193004Z-lgbm-validation` y `20261023T172311Z-lgbm_cqr-validation`; optimizado `20261023T191340Z-lgbm_tuned-validation` y `20261023T191954Z-lgbm_tuned_cqr-validation`.

| Conjunto de filas | Modelo | RMSE Q50 (MW) | Winkler sin CQR | Cobertura sin CQR | Winkler con CQR | Cobertura con CQR |
|---|---|---|---|---|---|---|
| **Todo (22 pliegues, 64 224 filas)** | v0.1 | 1 060,8 | 3 450,6 | 71,2 % | 3 396,5 | 79,5 % |
| | optimizado | **1 054,6** (−0,6 %) | **3 444,2** | 70,6 % | **3 387,5** | 79,4 % |
| | operador | 876,6 | — | — | — | — |
| **Pliegues usados para optimizar (pares, 31 784 filas)** | v0.1 | 1 085,1 | 3 502,2 | 70,4 % | 3 428,6 | 78,6 % |
| | optimizado | **1 071,6** (−1,2 %) | **3 438,5** | 70,2 % | **3 377,3** | 79,2 % |
| | operador | 900,6 | — | — | — | — |
| **Pliegues no usados (impares, 32 440 filas)** | v0.1 | **1 036,5** | **3 400,1** | 72,0 % | **3 365,0** | 80,5 % |
| | optimizado | 1 037,7 (+0,1 %) | 3 449,8 | 71,0 % | 3 397,5 | 79,6 % |
| | operador | 852,3 | — | — | — | — |
| **Invierno 2024/25 (dic–feb, 8 640 filas)** | v0.1 | **1 187,8** | 4 130,1 | 67,8 % | 4 008,5 | 80,7 % |
| | optimizado | 1 194,7 (+0,6 %) | **3 976,7** | 69,1 % | **3 898,7** | 80,5 % |
| | operador | 901,5 | — | — | — | — |
| **Invierno 2025/26 (dic–feb, 8 640 filas)** | v0.1 | 1 196,7 | **4 111,5** | 65,2 % | **3 932,6** | 74,5 % |
| | optimizado | **1 188,5** (−0,7 %) | 4 127,8 | 65,7 % | 3 936,0 | 77,1 % |
| | operador | 922,1 | — | — | — | — |

Otras cifras del total: el RMSE del optimizado mejora en 15 de los 22 pliegues (9 de 11 pares, 6 de 11 impares); días mejores que el operador 26,8 % (v0.1: 25,6 %); sesgo −22,9 MW (v0.1: −19,3). Pérdida cuantílica en los 22 pliegues: Q10 165,2 frente a 167,0 MW (−1,1 %) y **Q90 179,2 frente a 178,0 (+0,7 %: empeora fuera de los pliegues de optimización)**. Cuantiles con CQR: ancho medio 2 331 MW (v0.1: 2 310).

**Sesgo de selección (importante).** Las cifras de los pliegues pares son optimistas: se eligieron los parámetros que mejor les iban. La mejora de «−1,2 %» en ellos se reduce a «+0,1 %» en los impares: **la mejora real en validación es del orden de cero y la cifra de −0,6 % del total mezcla ambas cosas**. Aun así los impares no son una comprobación independiente: sus meses están dentro del entrenamiento de los pliegues pares posteriores y al revés, y los parámetros salen de los mismos 30 meses. La comprobación de verdad es el periodo reservado (#33 y #34), que no se ha mirado. El Winkler con CQR también mejora en los pares (−1,5 %) y empeora en los impares (+1,0 %). Para el criterio de aceptación de #32 («mejora sobre la v0.1 en validación»): se cumple en el total (−0,6 % de RMSE, −0,3 % de Winkler con CQR) pero no en los pliegues sin usar.

**Estudio exploratorio con espacio ampliado (Q50, no es el modelo registrado).** Como el óptimo estaba en el borde, se repitió el estudio del Q50 con `num_leaves` 4–255 y `feature_fraction` 0,2–1 (el resto igual; estudio `lgbm-wide-q50`, 149 intentos: 123 completos y 26 podados, 18 min, run MLflow `214612e314d4489c9030f35b9f62ec0c`). Mejor intento (nº 115): hojas 46, tasa 0,0110, mínimo 86, variables 0,247, filas 0,690, L1 0,0018, L2 0,080; media de 11 pliegues pares 1 031,6 MW frente a 1 050,6 (−1,8 %). Importancia: `feature_fraction` 0,41, `learning_rate` 0,16, `lambda_l1` 0,16, `min_data_in_leaf` 0,12, `num_leaves` 0,08, `bagging_fraction` 0,04, `lambda_l2` 0,03. Se evaluó con los 22 pliegues sustituyendo solo los parámetros del Q50 (el resto como v0.1; ejecución `20261023T192606Z-lgbm-validation`, lanzada con `--param 'quantile_params={"q50":{...}}'`): **RMSE total 1 041,0 MW (−1,9 %; operador +18,8 %)**, pares 1 060,5 (−2,3 %), **impares 1 021,5 (−1,5 %)**, invierno 2024/25 1 180,6 (−0,6 %), invierno 2025/26 1 182,8 (−1,2 %). Aquí la mejora sí se mantiene en los pliegues sin usar, pero sigue siendo pequeña. Como esa ejecución quedaba como la última de `lgbm` en la clasificación, se repitió después `lgbm` con los valores por defecto (`20261023T193004Z-lgbm-validation`) para que el scorecard siga hablando de la v0.1. Ampliar el espacio exige enmendar el ADR 0015 (no se ha tocado); si se acepta, convendría repetir los tres cuantiles con él.

**Adoptado (ADR 0033).** El Q50 de `config/lgbm_tuned.yaml` pasa a ser el del espacio ampliado (intento 115 de `lgbm-wide-q50`); Q10 y Q90 siguen con los estudios del espacio del ADR 0015. Validación de los 22 pliegues con esa configuración: `lgbm_tuned` RMSE 1 041,0 MW (−1,9 % frente a la v0.1; operador 876,6, +18,8 %), Winkler 3 443,9 y cobertura 70,6 % (`20261023T195003Z-lgbm_tuned-validation`); `lgbm_tuned_cqr` Winkler 3 387,0 y cobertura 79,4 % (`20261023T195834Z-lgbm_tuned_cqr-validation`). Las cifras de la tabla anterior con «optimizado» son las de la primera configuración (Q50 del espacio del ADR 0015).

**Lectura.**

- Con el espacio del ADR 0015 la optimización no acerca al operador: el error del Q50 pasa de +21,0 % a +20,3 % por encima del suyo. G1 (RMSE ≤ operador) no se alcanza con hiperparámetros; hay que buscarlo en variables, datos o modelo (#29 y siguientes).
- El ajuste no es lo que limita el modelo: con 150 intentos la mediana de los intentos del Q50 coincide con la v0.1 y el mejor se aparta unos pocos MW. Por qué el error central no baja más no se ha investigado aquí.
- La cobertura con CQR queda igual (79,4 %, en banda) y el Winkler apenas cambia.
- Control de sobreajuste del ADR 0015 (periodo reservado > 10 % peor que en validación): pendiente, el periodo reservado no se ha evaluado.

**Pruebas:** `tests/unit/test_tuning.py` (espacio del ADR, métricas, objetivo, poda, estudio SQLite con dos procesos, exportación y carga de parámetros, modelos registrados) y `tests/leakage/test_tuning_leakage.py` (el periodo reservado no está en ningún pliegue ni en el entrenamiento de la optimización ni en la parada temprana; también sobre la tabla real si existe).

## 9. Limitaciones y no verificado

- **Persistencia:** el margen de 180 min sale de una sola semana de medidas de octubre de 2026 (~40 observaciones: retraso normal de 30 a 62 min y un atasco de hasta 166 min el 22 de octubre); en otras épocas o con incidencias más largas de ENTSO-E podría ser mayor. Además, la generación histórica de la plata es la **última revisión**, no la que estaba publicada en el momento del cierre, así que el valor «último conocido» puede ser algo mejor que el real. Con el mismo cuidado hay que leer su RMSE.
- **Operador:** solo hay su valor central (sin Winkler ni cobertura). La referencia `operator` lleva el parámetro `version` (registrado en `params()`): `latest` (por defecto) lee la plata, que es la última descarga; `first_published` lee el bronce y toma, para cada cuarto de hora, la primera descarga que lo trae (`load_operator_first_published_mw`). Con el histórico (dic 2025 a jul 2026) solo se puede usar `latest`: no hay descargas por `fetched_at` antes del 16 oct 2026 y no se inventan versiones. Dos hechos acotan el sesgo.
  - **No existe una versión «vista antes del cierre».** El pronóstico de D+1 aparece hacia las 17:00 UTC de D, unas 4 h después del cierre (`docs/f2-entsoe.md` §3). Por eso la versión con sentido es la primera publicada, que es la del día de publicación y la que habría visto un competidor justo después del cierre.
  - **Esa versión no cambia.** En la copia de la VM (173 descargas, del 16 al 23 oct 2026) se compararon todas las versiones de cada día objetivo del 17 al 23 oct (7 días, 96 cuartos cada uno, B18 + B19): entre la primera y la última, y también entre la última descarga anterior al inicio del día y la última, la diferencia es **0 MW** (RMSE y MAE nulos) y no hay ninguna descarga posterior con un valor distinto. El pronóstico se publica una vez y no se revisa.
  - **Sesgo estimado por versión: 0 MW de RMSE** (7 días). El sesgo que queda es otro y no se puede medir con los datos: el operador publica con información más reciente que el cierre (unas 4 h), de modo que su error puede ser algo menor que el de cualquier modelo que solo vea lo anterior al cierre. Comparar con él sigue siendo exigente y sirve de cota, no de igualdad de condiciones.
  - **No verificado:** una sola semana de octubre; no se sabe si en otras épocas el operador revisa su pronóstico. Hay que repetir la comparación (`first_published` frente a `latest`) cuando haya más semanas y, si aparecen revisiones, medir su efecto sobre el RMSE.
- **Holdout de verano-otoño con poco viento** (`y_cf` medio 0,184 frente a 0,189 en validación): no garantiza diciembre. La validación ahora tiene dos inviernos (2024/25 y 2025/26), pero los primeros pliegues siguen sin haber visto el mismo mes natural previo (por eso la climatología usa los meses vecinos ±1 y, si no hay, todo el entrenamiento) y el invierno 2024/25 se valida con solo 8 a 10 meses de entrenamiento: es donde LightGBM no gana a la curva de potencia (§8.4).
- **Cobertura en meses sueltos** oscila entre 69 y 83 %: con ~2 900 filas por mes y autocorrelación fuerte, la banda 76–84 % del total es una condición sobre 8 meses, no una garantía por mes. La CQR (§8.2) lleva el total al 79,8 %, pero por mes sigue entre 63 y 91 %.
- El **denominador** (`y_cap_mw`) usa MaStR con altas registradas a posteriori (`docs/f3-features.md` §11), igual para modelo y operador (el operador se compara en MW, sin conversión).
- Las clasificaciones vigentes son las de §8.4 (tabla oro versión Delta 7; con la 8 de §8.5 las cifras del modelo por defecto son idénticas, 936 cierres, 22 pliegues). Las de §8 a §8.3 (410 cierres) quedan como historial. Cada vez que cambie el histórico, los pliegues cambian y hay que repetir las ejecuciones. El periodo reservado ahora tiene más días con realidad (la tabla llega al cierre del 2026-10-23) que cuando se evaluó por última vez la v0.1: si se evalúa, el número de filas no será el de §3.
- El contador del holdout detecta mirar demasiado, no lo impide; las ejecuciones se pueden borrar a mano de `data/backtest/`, pero el registro `holdout_log.jsonl` es de solo añadir.
- Pliegues de entrenamiento cortos al principio (182 días en el primero, 213 en el segundo): el error de oct y nov de 2024 es el de un modelo que solo ha visto primavera y verano, y esos dos meses no sirven para comparar con los siguientes. La media de todo el periodo los incluye (4 132 filas de las 64 224 en la racha ausente, 6,4 %).
- **Racha ausente en los primeros 7 meses** (§8.4): la NaN nativa se ha comprobado con datos reales, pero el efecto de la racha sobre el error es menor que el ruido (±1 %), así que no se ha podido medir si ayuda; no se repitió tras Optuna (§8.6): sigue sin medirse.
- **Optuna** (§8.6): los parámetros optimizados se eligieron con 11 de los 22 pliegues; la mejora en los otros 11 es nula y la comprobación independiente es el periodo reservado, aún sin evaluar. El estudio con espacio ampliado es exploratorio y solo cubre el Q50.
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
