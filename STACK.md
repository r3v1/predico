# Stack técnico y buenas prácticas — Predico 50Hertz

**Versión 1.0 · 15 de octubre de 2026**

Cada elección de este documento se registra como un ADR (Architecture Decision Record, registro de decisión de arquitectura) en `docs/adr/`. Si algo cambia, se crea un ADR nuevo que sustituye al anterior; nunca se edita uno aceptado.

---

## 1. Principio rector del stack

**Una herramienta por función.** Trabajas solo y con seis semanas: cada framework adicional es mantenimiento, depuración y una fuente más de fallos a las 13:30. Una herramienta entra solo si resuelve algo que las ya elegidas no resuelven.

---

## 2. Ciencia de datos y backend

| Función | Elección | Alternativa descartada y por qué |
|---|---|---|
| Lenguaje | **Python 3.14** | 3.12: más conservador, pero eccodes, cfgrib, zarr e icechunk ya funcionan en 3.14 con ruedas binarias (verificado el 16 oct 2026). LightGBM 4.7.0 y PyTorch 2.14.1 (cpu y cu130 con la RTX 4070 Laptop) también verificados en 3.14 con ruedas binarias (16 oct 2026). Si se usa `LGBMRegressor`, añadir scikit-learn al proyecto |
| Entornos y dependencias | **uv** (lockfile `uv.lock`, workspace) | pip/poetry: más lentos y sin gestión de versiones de Python integrada |
| Orquestación y pipelines | **Dagster** (assets) | Ver decisión sobre Kedro en la sección 2.1 |
| NWP principal | **dynamical.org IFS ENS** leído con **icechunk + xarray** (lazy, recorte en origen; `dynamical-catalog` o STAC + `icechunk.http_storage`) | GRIB de ECMWF Open Data como fuente principal: 100 m solo desde 2024-03-06, sin ensemble y con descarga de campos globales (`docs/stage0-index-check.md`) |
| NWP respaldo (GRIB) | **eccodes + cfgrib** con descargador propio por rangos de bytes (`src/predico50hz/ingest/ifs/`), stream `enfo` | ecmwf-opendata/Herbie: las rutas del bucket cambian con la fecha y la etapa 0 las resolvió listando; ver `docs/stage1-pipeline.md` |
| Campos meteorológicos | **xarray + Zarr v3 + Icechunk** | NetCDF: sin versionado ni escritura concurrente segura |
| Datos tabulares | **Polars + Parquet con Delta Lake (delta-rs)** y **DuckDB** para consultas | Iceberg: más pesado de operar sin un catálogo; Delta es suficiente para un solo escritor |
| Cliente ENTSO-E | **Cliente `httpx` propio** (sustituye a entsoe-py, [ADR 0022](docs/adr/0022-cliente-entsoe-propio.md)) | entsoe-py: `EntsoeRawClient` rompe los ZIP, no reintenta por código HTTP y deja el token en las excepciones |
| Gradient boosting | **LightGBM** | XGBoost: solo como comparación si el entrenamiento supera 2 h en CPU |
| Optimización de hiperparámetros | **Optuna** con almacenamiento en SQLite o PostgreSQL | Ray Tune: excesivo para una sola máquina |
| Calibración conformal | **Implementación propia de CQR** (unas 50 líneas, con tests) | MAPIE: válido, pero añade una dependencia para algo pequeño y crítico que conviene controlar línea a línea |
| Redes neuronales y modelos fundacionales | **PyTorch** | |
| Seguimiento de experimentos | **MLflow** | |
| Validación de datos | **Pandera** en el código; Great Expectations solo si se necesita su informe HTML | GE como primera opción: más pesado de configurar para un solo desarrollador |
| Configuración | **pydantic-settings** + ficheros YAML por entorno | Variables sueltas en el código |
| Secretos | **`.env` fuera de git, cifrado con sops + age** | Secretos en el repositorio |

### 2.1 Kedro: no en la v0.1

Kedro aporta catálogo de datos, plantilla de proyecto y pipelines de nodos. **Dagster ya da todo eso** con su grafo de assets, y además lo necesitarás en la Fase 2 para producción. Tener ambos significa dos grafos de dependencias que sincronizar.

**Umbral para reconsiderarlo:** si en la v0.2 los experimentos de modelado (muchas variantes de variables y modelos sobre los mismos datos) se vuelven incómodos en Dagster, se introduce Kedro solo para la capa de modelado, invocado desde Dagster con el plugin `kedro-dagster`. Se decide con un ADR.

---

## 3. Frontend: desacuerdo honesto

**Recomendación: ningún frontend propio en la Fase 1.**

Todo lo que pides ya existe sin escribir una línea:

| Necesidad | Herramienta ya disponible |
|---|---|
| Curvas de entrenamiento en tiempo real | **MLflow UI** |
| Progreso de la optimización, importancia de hiperparámetros, frentes de Pareto | **Optuna Dashboard** |
| Estado de descargas y pipelines | **Dagster UI** |
| Predicción frente a realidad, métricas diarias, alertas | **Grafana** (ya lo tienes en el homelab) sobre PostgreSQL o DuckDB |

Un frontend a medida con gráficos en tiempo real son 1–2 semanas de trabajo en un calendario de seis y media, y no mejora ni la precisión ni la fiabilidad. **Es una trampa de esfuerzo.**

**Cuándo sí tiene sentido:** a partir de diciembre, como panel propio de comparativas (tus envíos frente al operador, frente a la mediana de Predico, análisis de días malos) donde Grafana se quede corto.

### 3.1 Stack frontend para cuando toque

| Función | Elección | Por qué |
|---|---|---|
| Gestor de paquetes | **pnpm** | |
| Build | **Vite** | |
| Framework | **React + TypeScript** | 21st.dev es un catálogo de componentes React sobre shadcn/ui: elegirlo fija React |
| Estilos | **Tailwind CSS v4** | |
| Componentes | **shadcn/ui** + componentes de **21st.dev** | Copiados al repositorio, no como dependencia |
| Iconos | **lucide-react** | |
| Gráficos | **Apache ECharts** (vía `echarts-for-react`) | Ver comparativa |
| Datos del servidor | **TanStack Query** + eventos SSE (Server-Sent Events) desde un backend **FastAPI** | WebSockets: innecesarios para flujo unidireccional |
| Lint y formato | **Biome** | ESLint + Prettier: dos herramientas y más lentas |

**Comparativa de librerías de gráficos para este caso** (series de 15 minutos durante años, bandas de cuantiles, zoom, actualización en vivo):

| Librería | Veredicto |
|---|---|
| **ECharts** | **Ganadora.** Renderizado en canvas con muestreo para series grandes, zoom por rango integrado, bandas de área entre cuantiles, actualización incremental y licencia Apache-2.0 |
| uPlot | La más rápida para series temporales puras, pero pobre para paneles con varios tipos de gráfico |
| Chart.js | Sencilla, pero se degrada con cientos de miles de puntos |
| D3 | Es una librería para construir gráficos, no de gráficos: semanas de trabajo |
| Three.js | Es 3D; solo tendría sentido para visualizar campos de viento, no métricas |

---

## 4. Buenas prácticas y gestión del sistema de información

### 4.1 Estructura del repositorio

```
predico-50hertz/
├── README.md
├── CHANGELOG.md
├── pyproject.toml          # uv workspace, ruff, pyright, pytest
├── uv.lock
├── .pre-commit-config.yaml
├── .env.example
├── docs/
│   ├── adr/                # 0001-..., plantilla MADR
│   └── runbooks/           # qué hacer si falla X a las 13:30
├── src/predico50hz/
│   ├── ingest/             # IFS, ENTSO-E, MaStR
│   ├── features/
│   ├── models/             # lightgbm, cqr, baselines, foundation
│   ├── evaluation/         # métricas idénticas a Predico
│   ├── predico_client/     # Fase 2
│   └── orchestration/      # assets y schedules de Dagster
├── tests/
│   ├── unit/
│   ├── integration/
│   └── leakage/            # tests anti look-ahead
├── notebooks/              # exploración; nunca código de producción
└── docker/
```

### 4.2 Calidad de código

| Herramienta | Uso |
|---|---|
| **ruff** | Lint y formato, con reglas `E, F, I, B, UP, SIM, PL, NPY, PD, PT, RUF` |
| **pyright** (modo estricto en `src/`) | Tipado estático |
| **pytest** + **pytest-cov** | Cobertura ≥ 80 % en `evaluation/` y `features/`, que es donde un error cuesta dinero |
| **hypothesis** | Tests por propiedades de las métricas, por ejemplo que el Winkler nunca sea negativo |
| **nbstripout** | Notebooks sin salidas en git |

### 4.3 Hooks de pre-commit

- `ruff` (lint con corrección) y `ruff-format`
- `pyright`
- `uv lock --check`, para que el lockfile no quede desincronizado
- `nbstripout`
- `gitleaks`, contra secretos filtrados
- `check-added-large-files` (límite 1 MB: los datos nunca van a git)
- `commitizen`, para mensajes con Conventional Commits que generan el CHANGELOG

### 4.4 Documentación

- **README:** qué es, cómo arrancar en 5 comandos, arquitectura en un diagrama y enlaces a ADRs y runbooks.
- **ADRs** con plantilla MADR: contexto, opciones, decisión y consecuencias. Una página como máximo.
- **Runbooks:** uno por modo de fallo de producción (no llega IFS, no responde Predico, cae el equipo principal). Se escriben en la Fase 2, pero la carpeta existe desde el día 1.
- **Docstrings** en funciones públicas de `evaluation/` y `features/`, con unidades y hora de disponibilidad de cada variable.

### 4.5 Gestión de datos y experimentos

- **Cada experimento en MLflow** enlaza el commit de git, la versión del snapshot de Icechunk y el hash del lockfile.
- **Versionado semántico del modelo:** v0.1, v0.2… Un cambio de variables sube la versión menor y un cambio de fuente NWP, la mayor.
- **Nada de datos en git:** los datos viven en el NAS; el repositorio solo contiene código y configuración.

### 4.6 Integración continua

- **CI en cada push:** lint, tipos, tests unitarios y tests anti look-ahead.
- Con GitHub, GitHub Actions; si prefieres autoalojarlo, Forgejo con su runner en el homelab.

---

## 5. ADRs

Cada elección de este documento está registrada como ADR en [`docs/adr/`](docs/adr/README.md):

| Tema | ADR |
|---|---|
| Registrar decisiones con ADR (MADR) y reglas de documentación | [0019](docs/adr/0019-registro-de-decisiones-con-adr.md) |
| IFS ENS de dynamical.org como NWP principal; GRIB `enfo` como respaldo | [0001](docs/adr/0001-fuente-nwp-ifs-ens-dynamical.md) |
| Descarga del histórico por etapas | [0004](docs/adr/0004-descarga-del-historico-por-etapas.md) |
| Almacenamiento: Zarr + Icechunk para campos; Delta + Parquet para tablas | [0021](docs/adr/0021-almacenamiento-y-formatos.md) |
| Cliente propio de ENTSO-E | [0022](docs/adr/0022-cliente-entsoe-propio.md) |
| Dagster como único orquestador; Kedro aplazado | [0023](docs/adr/0023-dagster-como-orquestador.md) |
| LightGBM, Optuna, CQR propia, PyTorch y MLflow | [0024](docs/adr/0024-librerias-de-modelado.md) |
| Q50 con pérdida cuadrática; Q10 y Q90 cuantílicos con CQR | [0014](docs/adr/0014-cuantiles-y-calibracion-conformal.md) |
| Periodo reservado nunca visto por la optimización | [0010](docs/adr/0010-validacion-walk-forward-y-periodo-reservado.md) |
| Validación con Pandera | [0025](docs/adr/0025-validacion-con-pandera.md) |
| Configuración y secretos | [0026](docs/adr/0026-configuracion-y-secretos.md) |
| Sin frontend propio en la Fase 1; stack de frontend para después | [0027](docs/adr/0027-sin-frontend-propio-en-fase-1.md) |
| Calidad de código, pre-commit y CI | [0028](docs/adr/0028-calidad-de-codigo-y-ci.md) |
| Trazabilidad de experimentos y versionado del modelo | [0029](docs/adr/0029-experimentos-y-versionado-del-modelo.md) |
| Python 3.14 + uv | [0020](docs/adr/0020-python-314-y-uv.md) |
