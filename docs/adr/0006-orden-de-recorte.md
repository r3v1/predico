# ADR 0006 — Qué se recorta si el calendario aprieta

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Con un calendario de seis semanas es probable que algo no llegue. Conviene decidir de antemano qué se sacrifica y qué no, para no improvisar bajo presión.

## Decisión

Orden de recorte, del primero al último:

1. El benchmark de modelos fundacionales (ADR 0016) se mueve a después del 1 de diciembre.
2. La comparación detallada principal–respaldo de la NWP se reduce a lo imprescindible para la diaria.
3. Optuna baja a 50 intentos por modelo (ADR 0015).
4. **Nunca se recortan** el test anti look-ahead (ADR 0009), el periodo reservado (ADR 0010) ni la calibración conformal (ADR 0014).

## Consecuencias

- Los issues afectados llevan la etiqueta `recortable` en GitHub; recortar es moverlos al milestone `9 · Después del 1 dic`.

## Referencias

- ADR 0005
