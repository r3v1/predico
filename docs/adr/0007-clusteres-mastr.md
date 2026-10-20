# ADR 0007 — Aerogeneradores de MaStR agrupados en clústeres

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

La NWP se extrae en puntos representativos del parque eólico de la zona de 50Hertz, no en una malla completa. Hace falta una ubicación y una capacidad por grupo de aerogeneradores que reflejen dónde está la potencia instalada en cada fecha.

## Decisión

- Fuente: **Marktstammdatenregister (MaStR)** de la Bundesnetzagentur (licencia DL-DE-BY-2.0), con coordenadas y potencia de cada aerogenerador y su fecha de alta/baja.
- Objetivo del plan: 15–30 clústeres terrestres más los marinos, con capacidad por fecha de alta.
- Resultado: **16 clústeres terrestres** (k-means ponderado por potencia; k = 16 es el primero que cumple el criterio de resolución y coincide con el codo de la curva) y **5 marinos** (uno por parque, fusionando los que distan < 8,4 km). Sus 21 centroides están en `config/centroids.yaml`.
- Capacidad diaria por clúster en `data/silver/mastr/`, usada como pesos de agregación y como denominador del objetivo (ADR 0008).

## Consecuencias

- Los centroides quedan fijos: reagrupar exige reconstruir la plata y reentrenar.
- MaStR va por detrás de la realidad 1–2 días y en producción hay que actualizarlo de forma periódica.
- Sesgo de supervivencia: la capacidad de 2015 sale ≈ 3 % por debajo de ENTSO-E (bajas antiguas poco registradas).

## Referencias

- `docs/f2.6-mastr.md` §6 (clústeres) y §10
