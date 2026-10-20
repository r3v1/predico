# ADR 0020 — Python 3.14 y uv

- **Estado:** aceptada
- **Fecha:** 2026-10-15 (verificación de ruedas binarias: 2026-10-16)

## Contexto

Hay que fijar la versión de Python y la herramienta de entornos para todo el proyecto, incluidas librerías con extensiones nativas (eccodes, cfgrib, zarr, icechunk, LightGBM, PyTorch).

## Decisión

- **Python 3.14** (`requires-python = ">=3.14"`). Se verificó el 16 oct 2026 que eccodes, cfgrib, zarr, icechunk, LightGBM 4.7.0 y PyTorch 2.14.1 (CPU y cu130 para la RTX 4070 Laptop) tienen ruedas binarias para 3.14.
- **uv** para entornos y dependencias, con `uv.lock` versionado y workspace.

Descartado: Python 3.12 (más conservador, pero sin necesidad tras la verificación); pip y poetry (más lentos y sin gestión integrada de versiones de Python).

## Consecuencias

- Si se usa `LGBMRegressor`, hay que añadir scikit-learn.
- Los servicios de systemd ejecutan `.venv/bin/python` directamente (sin `uv run`), para que un cambio de `pyproject.toml` no sincronice el entorno a mitad de un job.

## Referencias

- `STACK.md` §2, `pyproject.toml`
