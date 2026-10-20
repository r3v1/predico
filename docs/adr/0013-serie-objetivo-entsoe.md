# ADR 0013 — Serie objetivo: generación de ENTSO-E reconstruida como Predico

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Predico puntúa contra la generación eólica real publicada por ENTSO-E. Si nuestra serie objetivo difiere de la suya, el modelo aprende y se evalúa contra otra cosa.

## Decisión

- Generación real **B18 (marina) + B19 (terrestre)** en el área de control de 50Hertz (`10YDE-VE-------2`), cada 15 min, desde 2015.
- La malla se reconstruye **arrastrando el último valor**, igual que Predico: la API omite los valores repetidos (`curveType` A03) y se materializan marcados (`a03`).
- La serie objetivo debe quedar sin huecos tras la reconstrucción, o con los huecos documentados.
- Cuando lleguen las credenciales de Predico, se comprueba que coincide con los datos brutos que descarga Predico.

## Consecuencias

- Hay un único punto de verdad para el objetivo y para la evaluación.
- La comparación con los datos de Predico queda pendiente de las credenciales.

## Referencias

- `docs/f2-entsoe.md` §2, `data/validation/entsoe_generation.json`
