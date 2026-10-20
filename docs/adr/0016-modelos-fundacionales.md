# ADR 0016 — Criterios para adoptar modelos fundacionales de series temporales

- **Estado:** aceptada
- **Fecha:** 2026-10-16

## Contexto

Los modelos fundacionales de series temporales podrían aportar al ensemble, pero cuestan tiempo de integración y algunos tienen licencias que impiden su uso en producción.

## Decisión

- **Arnés común:** mismas variables NWP como covariables futuras conocidas, mismos pliegues y mismas métricas que LightGBM.
- **Candidatos:** «Timestral» (modelo exacto por identificar), Chronos-2 (verificar licencia), TimesFM 2.5 (apto para uso comercial) y TimesFM 3 (**solo investigación**: su licencia prohíbe la producción).
- Primero **sin ajuste (zero-shot)**; ajuste fino solo si queda a menos de un 10 % de LightGBM.
- **Criterios de adopción:**
  - licencia compatible con uso comercial y en producción;
  - mejora de al menos un 5 % en RMSE o en Winkler sobre el periodo reservado frente a LightGBM;
  - si se adopta, entra como **componente del ensemble**, no como sustituto.

## Consecuencias

- Es lo primero que se recorta si el calendario aprieta (ADR 0006).

## Referencias

- `STACK.md` §2 (PyTorch)
