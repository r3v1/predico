"""Configuración de la operación diaria, toda por variables de entorno `PREDICO_*` (o `.env`).

Los defectos son los del equipo de sobremesa actual; en Docker se sobrescriben con variables
(`PREDICO_NWP_BRONZE=/nwp/icechunk`, `PREDICO_DATA_DIR=/data`, ...). Las rutas derivadas
(`ops_dir`, `lock_dir`, plata, oro...) cuelgan de `PREDICO_DATA_DIR` salvo que se fijen una a una.
"""

import datetime as dt
import socket
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class OpsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PREDICO_", env_file=".env", extra="ignore")

    # -- rutas ------------------------------------------------------------------------
    data_dir: Path = Path("data")
    nwp_bronze: Path = Path("/mnt/data/nwp/IFS-ENS/icechunk")
    centroids: Path = Path("config/centroids.yaml")
    ops_dir: Path | None = None  # <data_dir>/ops
    lock_dir: Path | None = None  # <ops_dir>/locks (debe ser un disco local, no NFS)
    run_log: Path | None = None  # <ops_dir>/runs.jsonl
    nwp_silver: Path | None = None  # <data_dir>/silver/ifs_ens_clusters
    entsoe_bronze: Path | None = None  # <data_dir>/bronze/entsoe
    entsoe_silver: Path | None = None  # <data_dir>/silver/entsoe
    gold: Path | None = None  # <data_dir>/gold/features
    validation_dir: Path | None = None  # <data_dir>/validation
    mastr_silver: Path | None = None  # <data_dir>/silver/mastr

    # -- identidad y política del host ---------------------------------------------------------
    host_id: str | None = None  # por defecto, el hostname; distingue la VM del equipo local
    # ¿un día sin ninguna ejecución cuenta como fallo? Sí en un servidor 24/7; en un portátil que
    # se apaga, no (queda como `off`).
    missing_is_failure: bool = True
    catchup_days: int = 14  # `nwp-daily` ingiere también las pasadas que falten de estos días
    validate_days: int = 7
    log_file: Path | None = None  # <ops_dir>/ops.log

    # -- sondeo de dynamical ----------------------------------------------------------
    poll_interval_s: float = 300.0
    poll_deadline_utc: dt.time = dt.time(12, 0)
    enfo_base_url: str = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/"
    enfo_step_h: int = 54  # último paso necesario; sus ficheros llegan los últimos
    http_timeout_s: float = 20.0
    lock_timeout_s: float = 1800.0
    # la plata de ENTSO-E se reescribe en cada descarga horaria: se purgan las versiones viejas
    vacuum_retention_h: int = 24

    # -- derivadas --------------------------------------------------------------------
    @property
    def host(self) -> str:
        return self.host_id or socket.gethostname()

    @property
    def log_path(self) -> Path:
        return self.log_file or self.ops_path / "ops.log"

    @property
    def ops_path(self) -> Path:
        return self.ops_dir or self.data_dir / "ops"

    @property
    def lock_path(self) -> Path:
        return self.lock_dir or self.ops_path / "locks"

    @property
    def run_log_path(self) -> Path:
        return self.run_log or self.ops_path / "runs.jsonl"

    @property
    def availability_path(self) -> Path:
        return self.ops_path / "nwp_availability"

    @property
    def publication_path(self) -> Path:
        return self.ops_path / "publication_times.json"

    @property
    def generation_lag_path(self) -> Path:
        return self.ops_path / "generation_lag.jsonl"

    @property
    def nwp_silver_path(self) -> Path:
        return self.nwp_silver or self.data_dir / "silver" / "ifs_ens_clusters"

    @property
    def entsoe_bronze_path(self) -> Path:
        return self.entsoe_bronze or self.data_dir / "bronze" / "entsoe"

    @property
    def entsoe_silver_path(self) -> Path:
        return self.entsoe_silver or self.data_dir / "silver" / "entsoe"

    @property
    def gold_path(self) -> Path:
        return self.gold or self.data_dir / "gold" / "features"

    @property
    def validation_path(self) -> Path:
        return self.validation_dir or self.data_dir / "validation"

    @property
    def mastr_silver_path(self) -> Path:
        return self.mastr_silver or self.data_dir / "silver" / "mastr"


def get_ops_settings() -> OpsSettings:
    return OpsSettings()
