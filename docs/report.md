# Informe ejecutivo interactivo

Un único HTML autocontenido (`report/index.html`, ~1,8 MB): librerías, tipografías, mapas y datos van incrustados y se abre con doble clic, sin red. Está pensado para negocio: sin jerga y con las cifras leídas siempre de los datos reales.

## Regenerarlo

```bash
uv run python -m predico50hz.report                 # escribe report/index.html y report/data.json
uv run python -m predico50hz.report --out /tmp/x/index.html --root .
```

`report/index.html` y `report/*.json` están en `.gitignore`; se versionan el generador, la plantilla, los YAML de `report/` y las librerías de `src/predico50hz/report/vendor/`. Si el NAS no está montado (bronce de la previsión, archivo de MaStR) el volumen se muestra como «no disponible»; si falta cualquier fuente, el capítulo muestra un estado vacío. Parámetros útiles de la URL: `?theme=dark|light`, `?noanim=1` (sin animaciones, para capturas).

## De dónde sale cada capítulo

| Capítulo | Módulo | Datos |
|---|---|---|
| Portada (cifras) | `build.py` | resumen del resto |
| 1 ¿Dónde estamos? | `plan.py`, `content.py` | milestones e issues de GitHub (`r3v1/predico`, vía `gh api`; ver «Fuente del capítulo 1»), `report/labels.yaml`, `report/changelog.yaml` |
| 2 El mapa | `zone.py` | Delta `data/silver/mastr/{units,clusters,capacity_cluster}`, dominio de `ingest/dynamical/config.py`, fronteras de `vendor/geo/` |
| 3 ¿De dónde salen los datos? | `sources.py` | `report/sources.yaml`, volúmenes de disco y de `data/stage1`, `data/validation` |
| 4 La previsión por dentro | `forecast.py` | lista de variables de `ingest/dynamical/config.py`, textos de `report/variables.yaml`, bronce de solo lectura (`/mnt/data/nwp/IFS-ENS/icechunk`: última pasada, 51 escenarios de un punto), plata `ifs_ens_clusters` y producción de ENTSO-E |
| 5 El viaje del dato | `sources.py` | tamaños del bronce en el NAS, de `data/bronze/entsoe` y de `data/silver`; texto en `sources.yaml` (`journey`) |
| 6 ¿Nos podemos fiar? | `quality.py` | `data/validation/*.json`, silver de ENTSO-E, MaStR y `ifs_ens_clusters` |
| 7 El viento y la producción | `production.py` | silver de ENTSO-E (generación), MaStR (capacidad) y `ifs_ens_clusters` (últimos 14 días) |
| 8 El reloj diario | `sources.py` | `data/stage0/dynamical_check.json` (latencia), `sources.yaml` (`clock`) |
| 9 Riesgos y decisiones | `content.py` | `report/insights.yaml` |
| 10 El modelo | `content.py`, `plan.py` | `report/model.yaml`, puertas G0–G2 (issues `tipo:puerta` de GitHub) |
| Pie | `build.py` | versiones: snapshot del bronce, export de MaStR, última descarga de ENTSO-E |

La volumetría descargada de dynamical es una estimación: solo las pasadas leídas en serie (`init_workers == 1`) son atribuibles (`docs/stage1-dynamical.md`); se multiplica la mediana por las pasadas ingeridas.

## Fuente del capítulo 1 (GitHub)

El estado del proyecto se lee de GitHub con `gh api` (solo GET, timeout de 20 s): los milestones dan el calendario (orden por el número del título, fecha `due_on`, avance cerrados/total; el 9, sin fecha, se sitúa en el inicio de la liga), los issues `tipo:puerta` con título `G<n>` dan las puertas, y las épicas `tipo:epic` con `sub_issues_summary` dan las barras de bloque. La etapa 3 de descarga toma su estado de la épica `backlog:f1.4`.

Orden de fuentes: GitHub → caché `data/report/github_plan.json` (se reescribe con cada respuesta buena; el informe indica la fecha de la copia) → capítulo vacío. Con `--offline` no se llama a GitHub y se usa la caché:

```bash
uv run python -m predico50hz.report --offline
```

## Textos que se editan a mano (en `report/`)

- **`changelog.yaml`**: en cada evolutivo, añada una entrada **arriba** (`version`, `date`, `title`, `items`). La primera se muestra completa; el resto, plegadas.
- **`insights.yaml`**: hallazgos (`title`, `level` = `risk|watch|ok|info`, `what`, `meaning`, `action`). Admiten cifras con llaves calculadas: `{n_groups}`, `{n_members}`, `{n_turbines}`, `{sea_jump_pct}`, `{sea_now_mw}`, `{sea_before_mw}`, `{coverage_pct}`, `{days_to_league}` (véase `_facts` en `build.py`).
- **`labels.yaml`**: lenguaje llano de los hitos (clave = número del título del milestone), hito previo ya cumplido, reglas de corte, etapas de descarga y títulos de las épicas (clave = marca `backlog:<id>`).
- **`sources.yaml`**: tarjetas de fuentes, lugares y rutas del mapamundi (la ubicación del NAS es orientativa: ajustar `lat`/`lon`), texto del recorrido del dato y notas del reloj.
- **`glossary.yaml`**: términos con explicación emergente; en la plantilla se marcan con `<span class="gl" data-gl="id">`.
- **`model.yaml`**: medidas y fecha de la v0.1 (capítulo 10).
- **`variables.yaml`**: texto, unidad e icono de cada variable meteorológica (capítulo 4). Las variables y su orden salen de `config.py`; una variable nueva sin texto aparece igualmente, sin descripción.

## Añadir un capítulo nuevo

1. Módulo `src/predico50hz/report/<nombre>.py` con `aggregate(paths) -> dict` pequeño y serializable (agregue diario o mensual, coordenadas redondeadas; objetivo: HTML < 5 MB). Devuelva `None` si falta la fuente.
2. Enganche el resultado en `build_data` (`build.py`) bajo una clave nueva.
3. En `template.html` añada `<section class="chapter" id="c10" data-nav="Nombre">` copiando la estructura de otro capítulo (cabecera `ch-head`, idea clave `#idea10`, contenedores). La navegación se genera sola desde `data-nav`.
4. En `report.js` añada la función `render…` (use `mount(id, buildOption)` para gráficos, que cambian de tema solos) y su frase en el bloque «ideas clave». Muestre `empty(el, '…')` si el dato falta.
5. Pruebe la agregación con datos sintéticos en `tests/unit/test_report.py`.

## Mapas y proyección

Los tres mapas (zona, malla y mapamundi con su ampliación) usan una proyección Mercator, que conserva las formas: a 52° N una celda de 0,25° es más alta que ancha. El contenedor toma su alto de la proporción del encuadre (`fitHeight`), nunca al revés, y el GeoJSON se recorta a cada encuadre al vuelo (`regMap`). Los nombres de los estados se colocan en huecos libres (`placeStateLabels`). Para cambiar un encuadre, edite `ZONE_VIEW`, `GRID_VIEW`, `WORLD_VIEW` o `EU_VIEW` en `report.js`.

## Librerías vendorizadas

`src/predico50hz/report/vendor/VENDOR.md` anota versión, origen, licencia y sha256 de ECharts 5.6.0, las tipografías (Fraunces e Inter, OFL, `@fontsource` 5.2.5) y las fronteras (Natural Earth 5.1.2, dominio público, simplificadas con `scripts/build_report_geo.py`). El build no necesita red.

## Verificación visual

```bash
chromium --headless=new --hide-scrollbars --window-size=1440,16500 \
  --screenshot=/tmp/informe.png "file://$PWD/report/index.html?noanim=1"         # escritorio
chromium ... "…/index.html?noanim=1&theme=dark"                                     # oscuro
```

Para el móvil (el ancho mínimo de la ventana headless es ~485 px) se carga el informe en un `<iframe>` de 390 px con `--allow-file-access-from-files`. `?probe=1` deja en `body[data-probe]` el ancho de documento y los elementos que desbordan.
