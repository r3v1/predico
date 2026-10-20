# ADR 0022 — Cliente HTTP propio para ENTSO-E en lugar de entsoe-py

- **Estado:** aceptada (sustituye la elección de `entsoe-py` de `STACK.md` §2)
- **Fecha:** 2026-10-16

## Contexto

`STACK.md` eligió `entsoe-py` como cliente de ENTSO-E y dejaba las llamadas REST propias para el caso de que el cliente fallase. Al implementar F2 falló: `EntsoeRawClient` devuelve `response.text`, que rompe los ZIP que la API usa cuando una consulta abarca varios documentos; no reintenta por código HTTP; y deja `securityToken=` en las excepciones.

## Decisión

- Cliente **`httpx` mínimo y propio** (`src/predico50hz/ingest/entsoe/client.py`) que devuelve bytes crudos (XML o ZIP), con la misma API REST y los mismos parámetros.
- Ritmo ≤ 300 peticiones/min, backoff exponencial en 429/5xx y errores de red, «sin datos» distinguido de error y reintento acotado de los HTTP 400 transitorios.
- **Redacción del token** en textos, excepciones y logs (`redact.py`); ningún error encadena la excepción original.
- Parseo sobre el XML propio. `entsoe-py` solo se usó para contrastar resultados.

## Consecuencias

- Más código propio que mantener, pero bajo control y con tests (redacción, reintentos, ZIP).
- `entsoe-py` sigue declarada en `pyproject.toml` aunque `src/` no la importa; se puede retirar.

## Referencias

- `docs/f2-entsoe.md` §1, ADR 0018
