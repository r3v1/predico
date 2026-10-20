# ADR 0005 — Calendario, puertas de decisión y reglas de corte

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

El objetivo es entrar en la liga de Predico el 1 de diciembre de 2026 con un modelo que mejore al operador. El calendario es corto (unas seis semanas), así que cada hito necesita una puerta medible y unas reglas que digan qué pasa si no se cumple.

## Decisión

| Hito | Fecha | Puerta |
|---|---|---|
| Etapas 0 y 1 | 17 oct | Variables y cobertura verificadas; cadena validada con 7 días; latencia medida |
| **G0** | 22 oct | 7 días seguidos de archivo diario sin fallos (`python -m predico50hz.orchestration status`) |
| **v0.1** | 29 oct | LightGBM sin optimizar sobre 13 meses, con métricas registradas frente a las cuatro referencias |
| **G1** | 5 nov | Etapa 3 completa; RMSE del Q50 ≤ pronóstico del operador; cobertura del 80 % entre 76 y 84 % |
| **G2** | 12 nov | v0.2 optimizada; mejora sobre G1 confirmada en el periodo reservado |
| Producción | 13–24 nov | Definición y construcción del sistema de producción |
| Rodaje | 25–30 nov | Cero días perdidos; margen p95 ≥ 60 min antes del cierre |
| Liga | 1 dic | Primer mes de competición |

**Reglas de corte:**

- Si G1 no se supera el 5 de noviembre, el inicio pasa al 1 de enero.
- Si el 15 de noviembre no hay credenciales de Predico, el inicio pasa al 1 de enero: sin rodaje no se entra en liga.

**Cierre en UTC:** 15:00 CET equivale a 14:00 UTC en invierno y 13:00 UTC en verano. El día objetivo abarca de 23:00 UTC del día del cierre a 23:00 UTC del día siguiente en invierno, y de 22:00 a 22:00 UTC en verano.

## Consecuencias

- Cada hito es un milestone numerado en GitHub (`1 · G0` … `6 · Rodaje y liga`) y cada puerta, un issue con la etiqueta `tipo:puerta`.
- La decisión final (1 dic o 1 ene) se toma en el cierre de la Fase 1 con los resultados de las puertas.

## Referencias

- ADR 0006 (orden de recorte), milestones de GitHub
