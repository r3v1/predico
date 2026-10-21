# F4 — Marco de backtest y métricas

Cubre F4.1 a F4.3 y las referencias mínimas de F5 (sin persistencia). Código en `src/predico50hz/evaluation/` (marco) y `src/predico50hz/models/` (interfaz y referencias); tests en `tests/unit/test_backtest_*.py` y `tests/leakage/test_backtest_folds.py`; salidas en `data/backtest/` (ignorado por git). Todo el tiempo es UTC salvo que se diga lo contrario.

## 1. Cómo ejecutarlo

```
uv run python -m predico50hz.evaluation run --model climatology        # validación walk-forward
uv run python -m predico50hz.evaluation run --model power_curve
uv run python -m predico50hz.evaluation run --model operator
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
| `models/baselines.py` | `climatology`, `power_curve`, `operator` |
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

## 9. Limitaciones y no verificado

- **Persistencia fuera:** exige conocer hasta qué hora hay generación publicada antes del cierre, que no está medida (F2.5/F2.7). El ADR 0017 la pide; queda pendiente.
- **Operador:** solo hay su valor central (sin Winkler ni cobertura) y la serie de `silver` es la **última revisión** descargada (2026-10-16), no necesariamente la que había publicada el día antes: puede ser algo mejor que la real. Su hora de publicación sigue sin medirse.
- **Holdout de verano-otoño con poco viento** (`y_cf` medio 0,181 frente a 0,203 en train): no garantiza diciembre. La validación solo tiene un invierno, y ya forma parte del entrenamiento a partir de febrero: con 13 meses los modelos no han visto nunca el mismo mes natural previo (por eso la climatología usa los meses vecinos ±1 y, si no hay, todo el entrenamiento).
- **Cobertura en meses sueltos** oscila entre 69 y 83 %: con ~2 900 filas por mes y autocorrelación fuerte, la banda 76–84 % del total es una condición sobre 8 meses, no una garantía por mes. La calibración conformal (F6) queda para el modelo definitivo.
- El **denominador** (`y_cap_mw`) usa MaStR con altas registradas a posteriori (`docs/f3-features.md` §11), igual para modelo y operador (el operador se compara en MW, sin conversión).
- Las clasificaciones se hacen con una única tabla oro (410 cierres); al ampliar el histórico (etapa 3) los pliegues cambiarán y habrá que repetir las ejecuciones.
- El contador del holdout detecta mirar demasiado, no lo impide; las ejecuciones se pueden borrar a mano de `data/backtest/`, pero el registro `holdout_log.jsonl` es de solo añadir.
- Pliegues de entrenamiento muy cortos al principio (90 días en el primero): el error de diciembre refleja eso.
- No se ha verificado el capítulo del informe en móvil; sí en escritorio claro con capturas de Chromium (el modo oscuro usa los mismos tokens de color).
