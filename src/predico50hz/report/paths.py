# pyright: basic, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
"""Rutas de entrada del informe. Todo cuelga de `root` para poder probar con datos sintéticos."""

from dataclasses import dataclass
from pathlib import Path

from predico50hz.ingest.dynamical.config import DEFAULT_BRONZE
from predico50hz.ingest.mastr.config import RAW_ROOT

PACKAGE = Path(__file__).parent
VENDOR = PACKAGE / "vendor"
LEAGUE_START = (2026, 12, 1)  # primer mes de liga


@dataclass(frozen=True)
class Paths:
    root: Path = Path()
    nwp_bronze: Path = Path(DEFAULT_BRONZE)
    mastr_raw: Path = Path(RAW_ROOT)

    @property
    def plan_cache(self) -> Path:
        """Última respuesta buena de GitHub (hitos e issues)."""
        return self.root / "data" / "report" / "github_plan.json"

    @property
    def silver(self) -> Path:
        return self.root / "data" / "silver"

    @property
    def bronze_entsoe(self) -> Path:
        return self.root / "data" / "bronze" / "entsoe"

    @property
    def validation(self) -> Path:
        return self.root / "data" / "validation"

    @property
    def stage0(self) -> Path:
        return self.root / "data" / "stage0" / "dynamical_check.json"

    @property
    def stage1(self) -> Path:
        return self.root / "data" / "stage1" / "dynamical_metrics.json"

    @property
    def backtest_scorecard(self) -> Path:
        """Cuadro de mando del backtest (`python -m predico50hz.evaluation report`)."""
        return self.root / "data" / "backtest" / "report" / "scorecard.json"

    @property
    def backtest_runs(self) -> Path:
        """Ejecuciones del backtest (`predictions.parquet`, `run.json`)."""
        return self.root / "data" / "backtest" / "runs"

    @property
    def centroids(self) -> Path:
        return self.root / "config" / "centroids.yaml"

    @property
    def content(self) -> Path:
        """Textos editados a mano (changelog, hallazgos, fuentes, glosario, etiquetas)."""
        return self.root / "report"
