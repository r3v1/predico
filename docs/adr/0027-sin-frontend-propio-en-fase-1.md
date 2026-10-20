# ADR 0027 — Sin frontend propio en la Fase 1; stack de frontend para cuando toque

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

Se planteó un frontend propio con gráficos en tiempo real. Son 1–2 semanas de trabajo en un calendario de seis y media, y no mejoran ni la precisión ni la fiabilidad.

## Decisión

**Ningún frontend propio en la Fase 1.** Lo necesario ya existe:

| Necesidad | Herramienta |
|---|---|
| Curvas de entrenamiento | MLflow UI |
| Progreso e importancia de hiperparámetros | Optuna Dashboard |
| Estado de descargas y pipelines | Dagster UI |
| Predicción frente a realidad, métricas diarias, alertas | Grafana del homelab sobre PostgreSQL o DuckDB |

Para negocio basta el informe ejecutivo estático (`python -m predico50hz.report`).

**Cuándo sí:** a partir de diciembre, como panel de comparativas (envíos frente al operador y a la mediana de Predico, análisis de días malos) donde Grafana se quede corto. Stack elegido para entonces: pnpm, Vite, React + TypeScript, Tailwind CSS v4, shadcn/ui y componentes de 21st.dev copiados al repo, lucide-react, Apache ECharts, TanStack Query con eventos SSE desde FastAPI, y Biome.

**Gráficos: ECharts.** Canvas con muestreo para series grandes, zoom por rango, bandas entre cuantiles, actualización incremental y licencia Apache-2.0. Descartados: uPlot (rápida, pero pobre para paneles con varios tipos de gráfico), Chart.js (se degrada con cientos de miles de puntos), D3 (semanas de trabajo) y Three.js (3D, no métricas).

## Consecuencias

- El panel propio es un issue del milestone `9 · Después del 1 dic`.

## Referencias

- `STACK.md` §3 y §3.1
