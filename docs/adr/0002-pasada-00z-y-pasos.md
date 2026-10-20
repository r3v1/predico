# ADR 0002 — Una sola pasada 00Z, pasos +18 a +54 h cada 3 h

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

El cierre de Predico es a las 15:00 de Madrid (13:00 UTC en verano, 14:00 UTC en invierno) y el día objetivo D+1 va de 22:00/23:00 UTC del día del cierre a la misma hora del día siguiente. La versión anterior del plan preveía también la pasada 06Z de HRES; IFS ENS en dynamical solo ofrece de forma útil la 00Z (ADR 0001).

## Decisión

- Pasada **00Z única** del día del cierre.
- Pasos **+18 a +54 h, cada 3 h (13 pasos)**: cubren el día objetivo completo con margen a ambos lados para interpolar a 15 minutos (ADR 0012).
- Se abandona la 06Z.

## Consecuencias

- La previsión usada tiene entre 18 y 54 h de antelación; no se aprovechan pasadas más recientes.
- Si la 00Z falla, no hay otra pasada del mismo día en dynamical: el plan B es `enfo` 00Z (ADR 0001).

## Referencias

- `docs/stage0-dynamical.md`, `docs/f3-features.md` §2–§3
