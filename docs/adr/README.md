# Registro de decisiones de arquitectura (ADR)

Cada decisión del proyecto, con su contexto y sus consecuencias. Las 0001–0018 vienen del antiguo `PLAN.md`; las 0019–0029, de `STACK.md`. Una decisión no se edita: si cambia, se escribe un ADR nuevo que la sustituye y el anterior pasa a estado «sustituida por ADR NNNN».

Las tareas pendientes están en los [issues](https://github.com/r3v1/predico/issues) y [milestones](https://github.com/r3v1/predico/milestones) de GitHub; lo ya hecho, en [`docs/historial.md`](../historial.md).

| ADR | Decisión | Estado |
|---|---|---|
| [0001](0001-fuente-nwp-ifs-ens-dynamical.md) | Fuente NWP principal: ECMWF IFS ENS en dynamical.org, con respaldo GRIB `enfo` | aceptada |
| [0002](0002-pasada-00z-y-pasos.md) | Una sola pasada 00Z, pasos +18 a +54 h cada 3 h | aceptada |
| [0003](0003-variables-dominio-y-capas.md) | Variables NWP, dominio y capas bronce / plata | aceptada |
| [0004](0004-descarga-del-historico-por-etapas.md) | Descarga del histórico por etapas y v0.1 sobre 13 meses | aceptada |
| [0005](0005-calendario-puertas-y-reglas-de-corte.md) | Calendario, puertas de decisión y reglas de corte | aceptada |
| [0006](0006-orden-de-recorte.md) | Qué se recorta si el calendario aprieta | aceptada |
| [0007](0007-clusteres-mastr.md) | Aerogeneradores de MaStR agrupados en clústeres | aceptada |
| [0008](0008-objetivo-factor-de-capacidad.md) | Objetivo: factor de capacidad con la capacidad de MaStR | aceptada |
| [0009](0009-uniones-as-of-y-anti-look-ahead.md) | Uniones «as-of» y test automático anti look-ahead | aceptada |
| [0010](0010-validacion-walk-forward-y-periodo-reservado.md) | Validación walk-forward, partición de la v0.1 y periodo reservado | partición sustituida por 0032 |
| [0011](0011-metricas-identicas-a-predico.md) | Métricas idénticas a las de Predico | aceptada |
| [0012](0012-interpolacion-temporal.md) | Interpolación temporal de 3 h a 15 min | aceptada |
| [0013](0013-serie-objetivo-entsoe.md) | Serie objetivo: generación de ENTSO-E reconstruida como Predico | aceptada |
| [0014](0014-cuantiles-y-calibracion-conformal.md) | Cuantiles: Q50 con pérdida cuadrática, Q10/Q90 cuantílicos y calibración conformal | aceptada |
| [0015](0015-optimizacion-con-optuna.md) | Optimización de hiperparámetros con Optuna y control de sobreajuste | aceptada |
| [0016](0016-modelos-fundacionales.md) | Criterios para adoptar modelos fundacionales de series temporales | aceptada |
| [0017](0017-referencias-y-operador-como-benchmark.md) | Cuatro referencias de comparación; el operador solo como benchmark | aceptada |
| [0018](0018-archivo-diario-de-entsoe.md) | Archivo diario de las versiones publicadas por ENTSO-E | aceptada |
| [0019](0019-registro-de-decisiones-con-adr.md) | Registrar las decisiones con ADR y documentar con reglas fijas | aceptada |
| [0020](0020-python-314-y-uv.md) | Python 3.14 y uv | aceptada |
| [0021](0021-almacenamiento-y-formatos.md) | Almacenamiento: Zarr + Icechunk para campos, Delta + Parquet para tablas | aceptada |
| [0022](0022-cliente-entsoe-propio.md) | Cliente HTTP propio para ENTSO-E en lugar de entsoe-py | aceptada |
| [0023](0023-dagster-como-orquestador.md) | Dagster como único orquestador; Kedro aplazado | aceptada |
| [0024](0024-librerias-de-modelado.md) | Librerías de modelado: LightGBM, Optuna, PyTorch y MLflow | aceptada |
| [0025](0025-validacion-con-pandera.md) | Validación de datos con Pandera | aceptada |
| [0026](0026-configuracion-y-secretos.md) | Configuración con pydantic-settings y secretos fuera de git | aceptada |
| [0027](0027-sin-frontend-propio-en-fase-1.md) | Sin frontend propio en la Fase 1; stack de frontend para cuando toque | aceptada |
| [0028](0028-calidad-de-codigo-y-ci.md) | Calidad de código, hooks de pre-commit e integración continua | aceptada |
| [0029](0029-experimentos-y-versionado-del-modelo.md) | Trazabilidad de experimentos y versionado semántico del modelo | aceptada |
| [0030](0030-persistencia-con-margen-de-publicacion.md) | Persistencia con un margen de publicación de 180 min | aceptada |
| [0031](0031-precio-day-ahead-solo-del-dia-del-cierre.md) | Precio day-ahead: el de D+1 no entra; el de D, como candidato | aceptada |
| [0032](0032-particion-con-el-historico-completo.md) | Partición y pliegues con el histórico completo | aceptada |

## Plantilla

```markdown
# ADR NNNN — Título

- **Estado:** propuesta | aceptada | sustituida por ADR NNNN
- **Fecha:** AAAA-MM-DD

## Contexto
## Decisión
## Consecuencias
## Referencias
```
