# ADR 0030 — Persistencia con un margen de publicación de 180 min

- **Estado:** aceptada
- **Fecha:** 2026-10-22

## Contexto

El ADR 0017 pide la persistencia como referencia de F5. Para no mirar al futuro hay que saber qué generación real estaba ya publicada en el cierre (15:00 de Berlín). La medición del 16 al 22 oct 2026 (#11, `docs/f2-entsoe.md` §3) da un retraso normal de 30 a 35 min entre la consulta y el inicio del último cuarto de hora publicado. El 22 oct hubo un atasco de ENTSO-E alrededor del cierre que llegó a 166 min.

## Decisión

- La persistencia (`models/baselines.py`, `Persistence`) solo usa los cuartos de hora que empiezan como muy tarde a `closure_utc(D) − 180 min` (`lag_min=180`). No usa los puntos rellenados (`is_gap_filled`).
- Modo por defecto `last`: repite el último valor conocido para todo D+1. En validación da menos RMSE que repetir el perfil de las últimas 24 h (3 670 frente a 3 984 MW, con 90 min).
- Intervalo Q10/Q90: cuantiles empíricos del error de entrenamiento por tramo de horizonte.

## Consecuencias

- El margen cubre un atasco como el del 22 oct y cuesta un 2,1 % de RMSE frente a 90 min (3 746 frente a 3 670 MW), porque el día objetivo está a 10,5–34,5 h del corte.
- El margen sale de una semana de octubre. La medición sigue en la VM (`docs/f1.5-f2.7-operations.md` §10); si aparece un retraso mayor, se sustituye este ADR.
- La generación histórica de la plata es la última revisión, no la publicada en el cierre: la persistencia puede salir algo mejor de lo que sería en tiempo real.

## Referencias

- ADR 0017, #11, #14, `docs/f4-backtest.md` §8.3
