# ADR 0031 — Precio day-ahead: el de D+1 no entra; el de D, como candidato

- **Estado:** aceptada
- **Fecha:** 2026-10-23

## Contexto

El precio day-ahead de D+1 es una pista de curtailment (con precios negativos los parques paran). El ADR 0009 solo deja usarlo si su hora de publicación medida es anterior al cierre (15:00 de Berlín). `entsoe-hourly` lo mide cada hora en la VM desde el 16 oct 2026. El análisis de `publication-times` (#10, `docs/f2-entsoe.md` §3) da, para 6 cierres medidos del 17 al 22 oct:

- Primera vez visto completo: 12:00 UTC en 3 días, 13:00 UTC en 2 (a las 12:00 aún no estaba) y 19:00 UTC el 20 oct.
- Antes del cierre (13:00 UTC en verano) solo en el 50 %; peor caso, 6 h tarde.

La subasta SDAC casa hacia las 12:45 CET, pero ENTSO-E lo publica con un retraso variable. Como la subasta y el cierre van en hora local, el cambio de hora no da más margen.

## Decisión

- **El precio de D+1 no entra como variable.** Con la regla del ADR 0009 (peor caso medido) no está disponible al cierre.
- **El precio de D sí puede entrar.** Se publica la víspera (en torno a las 12:00–13:00 UTC de D−1), más de 24 h antes del cierre de D. Es una pista más débil: dice si el día del cierre ya hay curtailment o precios negativos, no si los habrá mañana. Su `available_at_price` es la primera hora vista en el bronce y, para el histórico, `cierre(D−1)` + 6 h como cota prudente (el peor retraso medido).
- **Se prueba en #29** (milestone 3) como bloque `price` aparte y solo se queda si mejora la validación. Hasta entonces la v0.1 sigue sin precio.

## Consecuencias

- El modelo no puede anticipar con el precio un curtailment que el mercado solo revela tras el cierre.
- #29 cambia de alcance: precio de D, no de D+1. El test anti look-ahead debe comprobar que ningún precio de D+1 llega a la tabla de variables.
- La medición sigue en la VM. Si en unas semanas el precio de D+1 aparece siempre antes del cierre con margen, se escribe un ADR que sustituya a este.

## Referencias

- ADR 0009, ADR 0017, #10, #12, #29
- `docs/f2-entsoe.md` §3, `docs/f3-features.md` §9
