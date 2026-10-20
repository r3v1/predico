# ADR 0026 — Configuración con pydantic-settings y secretos fuera de git

- **Estado:** aceptada
- **Fecha:** 2026-10-15

## Contexto

El código corre en el portátil, en Docker (Hetzner) y más adelante en el sobremesa y en TrueNAS, con tokens de APIs externas (ENTSO-E y, después, Predico). El repositorio es público.

## Decisión

- **Configuración:** `pydantic-settings` más ficheros YAML por entorno; nada de valores sueltos en el código.
- **Secretos:** `.env` fuera de git (solo se versiona `.env.example` sin valores), **cifrado con sops + age** para repartirlo entre máquinas.
- `gitleaks` en pre-commit contra secretos filtrados (ADR 0028).

## Consecuencias

- El cifrado con sops + age aún no está montado: es un issue del milestone `5 · Producción`.

## Referencias

- `STACK.md` §2, `src/predico50hz/settings.py`
