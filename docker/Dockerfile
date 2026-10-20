# Imagen de la operación diaria (planificador `serve`). Sin secretos: el token y las rutas
# llegan en tiempo de ejecución por `env_file`/`environment` (ver docker/compose.yaml).
# Construir desde la raíz del repositorio:  docker build -f docker/Dockerfile -t predico50hz-ops .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    TZ=UTC \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# La operación no usa torch (solo los modelos): se omite junto con las ruedas CUDA que arrastra,
# que multiplicarían por 10 el tamaño de la imagen.
ARG SKIP_PACKAGES="torch triton cuda-bindings cuda-pathfinder cuda-toolkit nvidia-cublas \
nvidia-cuda-cupti nvidia-cuda-nvrtc nvidia-cuda-runtime nvidia-cudnn-cu13 nvidia-cufft \
nvidia-cufile nvidia-curand nvidia-cusolver nvidia-cusparse nvidia-cusparselt-cu13 \
nvidia-nccl-cu13 nvidia-nvjitlink nvidia-nvshmem-cu13 nvidia-nvtx"

# Dependencias primero (capa cacheable), después el código.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project \
    $(for p in $SKIP_PACKAGES; do printf -- '--no-install-package %s ' "$p"; done)
COPY src ./src
RUN uv sync --frozen --no-dev \
    $(for p in $SKIP_PACKAGES; do printf -- '--no-install-package %s ' "$p"; done)

RUN useradd --uid 1000 --create-home predico && mkdir -p /app/data && chown predico /app/data
USER predico

CMD ["python", "-m", "predico50hz.orchestration", "serve"]
