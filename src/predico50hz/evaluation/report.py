"""Informe del backtest: `scorecard.json` y gráficos en `data/backtest/report/`."""

from pathlib import Path

from predico50hz.evaluation.scorecard import build_scorecard, write_scorecard


def build_report(
    root: str | Path, out: str | Path, gold: str | None = None, png: bool = True
) -> Path:
    """Escribe el cuadro de mando y, si `png`, los gráficos. Devuelve la carpeta de salida."""
    card = build_scorecard(root, gold)
    out = Path(out)
    write_scorecard(card, out)
    if png:
        from predico50hz.evaluation.plots import all_plots  # noqa: PLC0415

        all_plots(card, Path(root), out)
    return out
