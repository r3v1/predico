# ADR 0003 — Variables NWP, dominio y capas bronce / plata

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Hay que decidir qué variables de dynamical se descargan, sobre qué recorte geográfico y cómo se guardan, de forma que el histórico y la diaria sean idénticos y reprocesables.

## Decisión

**Variables:**

| Variable dynamical | Para qué |
|---|---|
| `wind_u_100m`, `wind_v_100m` | Variable principal: altura de buje aproximada |
| `wind_u_10m`, `wind_v_10m` | Cizalladura vertical frente a 100 m |
| `wind_gust_10m` | Picos y paradas por viento extremo (solo existe desde 2024-11-13) |
| `temperature_2m` | Densidad del aire y estabilidad |
| `pressure_surface` | Densidad del aire: la potencia es proporcional a ρ·v³ |
| `temperature_925hpa`, `temperature_850hpa` | Estabilidad atmosférica |
| `pressure_reduced_to_mean_sea_level`, `geopotential_height_850hpa` | Contexto sinóptico y gradientes de presión (opcional) |

**Dominio:** recorte 49,5–56,0° N y 8,5–16,0° E (27 × 31 puntos a 0,25°). Al leer por chunks de 8°, el volumen transferido es el de los chunks que tocan el dominio, no el del recorte. Almacenamiento estimado: decenas de GB como máximo para todo el histórico.

**Capas:**

- **Bronce** (Icechunk propio): valores originales recortados al dominio, los 51 miembros.
- **Plata:** extracción en los centroides de los clústeres (ADR 0007) con control, media, desviación típica y cuantiles del ensemble; se reconstruye siempre desde el bronce.
- La **máscara tierra-mar** no está en dynamical: se toma una vez de los GRIB abiertos (`lsm`, disponible desde 2023-03-30).

## Consecuencias

- La racha es toda NaN antes del 2024-11-13: el modelo y la validación deben tratarlo.
- Cambiar de variables o de dominio exige reingerir el bronce.
- La máscara tierra-mar sigue pendiente de aplicar en los puntos marinos.

## Referencias

- `docs/stage0-dynamical.md` §1–§2, `docs/stage1-dynamical.md`, `docs/f1.6-validation.md`
