# ADR 0018 — Archivo diario de las versiones publicadas por ENTSO-E

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

ENTSO-E revisa sus datos después de publicarlos y no guarda el historial de versiones. Sin archivo propio no se puede saber qué había publicado en un momento dado (la hora de publicación, el pronóstico del operador vigente en el cierre o la tasa de revisiones).

## Decisión

- Desde el 16 oct 2026 se guarda cada descarga en el bronce bajo su `fetched_at`, sin sobrescribir nada.
- Job `entsoe-daily` (06:00 UTC, últimos 3 días de todos los datasets) y `entsoe-hourly` (precio y pronóstico del operador cada hora, para medir su hora de publicación).
- La plata se reconstruye entera desde el bronce y guarda `first_seen_at` (primera vez que se vio cada valor).

## Consecuencias

- El archivo solo es útil si no tiene huecos: requiere una máquina encendida 24/7 (la comprobación G0 se hizo en una VM de Hetzner).
- Crece unos cientos de MB al día en la plata; `entsoe-daily` hace `vacuum` con retención de 24 h.

## Referencias

- `docs/f2-entsoe.md` §6, `docs/f1.5-f2.7-operations.md`
