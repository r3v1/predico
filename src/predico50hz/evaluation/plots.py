# pyright: basic, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeStubs=false
# ruff: noqa: RUF001, RUF002, E501
"""Gráficos PNG para negocio (matplotlib): títulos que se entienden solos, unidades en MW.

Todos leen el `scorecard.json` ya construido (y, el de días de ejemplo, las predicciones de la
ejecución indicada en él). Estilo limpio: fondo blanco, el operador siempre en gris oscuro
discontinuo, y cada modelo con su color fijo.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.dates as mdates

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import polars as pl
from matplotlib.axes import Axes
from matplotlib.ticker import FuncFormatter, MaxNLocator

INK = "#1b2733"
MUTED = "#6b7785"
GRID = "#e4e8ec"
OP_COLOR = "#3a4450"
BAND_OK = "#cfe9d9"
PALETTE = ["#1f7a8c", "#e08e2b", "#7b5ea7", "#c0504d", "#4e9f5d", "#2f5d9e"]
MONTHS_ES = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]
MON3 = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
DPI = 110
SIZE = (11.5, 6.2)
MIN_POINTS_LINE = 2


def _es(x: float, d: int = 0) -> str:
    return f"{x:,.{d}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def _setup() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": GRID,
            "axes.labelcolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "text.color": INK,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "font.size": 11,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.8,
            "axes.axisbelow": True,
            "legend.frameon": False,
        }
    )


def _mw_axis(ax: Axes, axis: str = "y") -> None:
    fmt = FuncFormatter(lambda v, _p: _es(v))
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _title(fig: Any, title: str, subtitle: str) -> None:
    fig.suptitle(title, x=0.02, y=0.985, ha="left", fontsize=16, fontweight="bold", color=INK)
    fig.text(0.02, 0.918, subtitle, ha="left", fontsize=11, color=MUTED)


def _colors(models: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    for m in sorted(models, key=lambda m: m["model"]):
        if m["kind"] == "operador":
            out[m["model"]] = OP_COLOR
        else:
            out[m["model"]] = PALETTE[i % len(PALETTE)]
            i += 1
    return out


def _save(fig: Any, path: Path) -> Path:
    fig.savefig(path, dpi=DPI, facecolor="white")
    plt.close(fig)
    return path


def ranking(card: dict[str, Any], out: Path) -> Path:
    """Clasificación: error (RMSE) y puntuación de rango (Winkler) por modelo."""
    _setup()
    models = card["models"]
    col = _colors(models)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=SIZE, gridspec_kw={"wspace": 0.55})
    fig.subplots_adjust(top=0.78, bottom=0.12, left=0.25, right=0.97)
    order = sorted(models, key=lambda m: -m["validation"]["rmse"])
    names = [m["label"] for m in order]
    y = np.arange(len(order))
    a1.barh(
        y,
        [m["validation"]["rmse"] for m in order],
        color=[col[m["model"]] for m in order],
        height=0.6,
    )
    op = next((m for m in models if m["kind"] == "operador"), None)
    if op:
        a1.axvline(op["validation"]["rmse"], color=OP_COLOR, ls="--", lw=1.6)
        a1.text(
            op["validation"]["rmse"],
            -0.62,
            "  operador",
            color=OP_COLOR,
            fontsize=10,
            va="top",
        )
    for yi, m in zip(y, order, strict=True):
        a1.text(
            m["validation"]["rmse"],
            yi,
            f"  {_es(m['validation']['rmse'])} MW",
            va="center",
            fontsize=10,
        )
    a1.set_yticks(y, [n.replace(" sobre ", "\nsobre ").replace(" por ", "\npor ") for n in names])
    a1.set_ylim(-0.7, len(order) - 0.4)
    a1.set_xlim(0, max(m["validation"]["rmse"] for m in order) * 1.28)
    a1.set_title("Error del valor central\n(MW; menos es mejor)", loc="left", fontsize=12)
    a1.grid(axis="y", visible=False)
    _mw_axis(a1, "x")
    for yi, m in zip(y, order, strict=True):
        w = m["validation"]["winkler"]
        if w is None:
            a2.text(
                0,
                yi,
                "  sin rango: el operador\n  solo publica un valor",
                va="center",
                fontsize=9.5,
                color=MUTED,
            )
            continue
        a2.barh(yi, w, color=col[m["model"]], height=0.6)
        a2.text(w, yi, f"  {_es(w)}", va="center", fontsize=10)
    a2.set_yticks(y, [""] * len(order))
    a2.set_ylim(-0.7, len(order) - 0.4)
    wmax = max((m["validation"]["winkler"] or 0.0) for m in order)
    a2.set_xlim(0, wmax * 1.25)
    a2.xaxis.set_major_locator(MaxNLocator(4))
    a2.set_title("Puntuación del rango del 80 %\n(menos es mejor)", loc="left", fontsize=12)
    a2.grid(axis="y", visible=False)
    _mw_axis(a2, "x")
    p = card["period"]
    _title(
        fig,
        "¿Qué modelo se equivoca menos?",
        f"Backtest día a día, {p['validation_start']} a {p['validation_end']} ({p['n_folds']} meses de prueba). "
        "Cada mes se predice sin haberlo visto.",
    )
    return _save(fig, out / "ranking_modelos.png")


def monthly(card: dict[str, Any], out: Path) -> Path:
    """RMSE por mes de cada modelo y del operador."""
    _setup()
    models = card["models"]
    col = _colors(models)
    fig, ax = plt.subplots(figsize=SIZE)
    fig.subplots_adjust(top=0.84, bottom=0.2, left=0.1, right=0.97)
    months = sorted({r["month"] for m in models for r in m["monthly"]})
    x = np.arange(len(months))
    for m in models:
        by = {r["month"]: r["rmse"] for r in m["monthly"]}
        vals = [by.get(mo, np.nan) for mo in months]
        op = m["kind"] == "operador"
        ax.plot(
            x,
            vals,
            marker="o",
            lw=2.4 if op else 2,
            ls="--" if op else "-",
            color=col[m["model"]],
            label=m["label"],
        )
    ax.set_xticks(x, [f"{MON3[int(mo[5:]) - 1]}\n{mo[:4]}" for mo in months])
    ax.set_ylabel("Error del valor central (MW)")
    ax.set_ylim(bottom=0)
    _mw_axis(ax)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=3, fontsize=10)
    _title(
        fig,
        "¿Cómo cambia el error de un mes a otro?",
        "Error típico (RMSE) de cada mes de prueba, en MW. Menos es mejor.",
    )
    return _save(fig, out / "rmse_por_mes.png")


def coverage(card: dict[str, Any], out: Path) -> Path:
    """Cobertura del rango del 80 % frente a la banda 76–84 %."""
    _setup()
    lo, hi = card["rules"]["coverage_band"]
    models = [m for m in card["models"] if m["kind"] != "operador"]
    col = _colors(card["models"])
    fig, ax = plt.subplots(figsize=SIZE)
    fig.subplots_adjust(top=0.84, bottom=0.14, left=0.1, right=0.97)
    ax.set_xlim(-0.5, len(models) - 0.5)
    ax.axhspan(lo * 100, hi * 100, color=BAND_OK, alpha=0.9, zorder=0)
    ax.axhline(80, color="#2f8f5b", lw=1.2, ls=":")
    ax.text(
        -0.48,
        hi * 100 + 0.8,
        "Banda aceptable (76–84 %)",
        ha="left",
        color="#1f6d43",
        fontsize=10,
    )
    for i, m in enumerate(models):
        c = m["validation"]["coverage"]
        if c is None:
            ax.text(i, 60, "sin rango", ha="center", color=MUTED)
            continue
        ax.bar(i, c * 100, width=0.5, color=col[m["model"]], zorder=3)
        ok = lo <= c <= hi
        ax.text(
            i,
            c * 100 + 1,
            f"{_es(c * 100, 1)} %" + ("" if ok else "\nfuera de banda"),
            ha="center",
            va="bottom",
            fontsize=10.5,
            fontweight="bold",
            zorder=5,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 2},
        )
    ax.set_xticks(
        range(len(models)),
        [m["label"].replace(" sobre ", "\nsobre ").replace(" por ", "\npor ") for m in models],
    )
    ax.set_ylim(0, 100)
    ax.set_ylabel("Veces que la realidad cae dentro del rango (%)")
    ax.grid(axis="x", visible=False)
    _title(
        fig,
        "¿Es fiable el rango del 80 %?",
        "Con un rango bien calibrado, la producción real cae dentro del 80 % de los cuartos de hora.",
    )
    return _save(fig, out / "cobertura.png")


def history(card: dict[str, Any], out: Path) -> Path:
    """Histórico de ejecuciones de validación: ¿vamos a mejor?"""
    _setup()
    runs = [h for h in card["history"] if h["mode"] == "validation"]
    runs.sort(key=lambda h: (h["created_at"], h["run_id"]))
    col = _colors(card["models"])
    fig, ax = plt.subplots(figsize=SIZE)
    fig.subplots_adjust(top=0.84, bottom=0.27, left=0.1, right=0.97)
    x = np.arange(len(runs))
    ops = [h["rmse_operator"] for h in runs]
    ax.plot(x, ops, color=OP_COLOR, ls="--", lw=1.8, label="Previsión del operador", zorder=2)
    seen: set[str] = set()
    for i, h in enumerate(runs):
        if h["model"] == "operator":
            continue
        ax.scatter(
            i,
            h["rmse"],
            s=90,
            color=col[h["model"]],
            zorder=3,
            label=h["label"] if h["model"] not in seen else None,
        )
        seen.add(h["model"])
        ax.text(i, h["rmse"], f"  {_es(h['rmse'])}", va="center", fontsize=9)
    best = [
        min([h["rmse"] for h in runs[: i + 1] if h["model"] != "operator"] or [np.nan])
        for i in range(len(runs))
    ]
    ax.step(x, best, where="post", color=INK, lw=1.2, alpha=0.5, label="Mejor hasta la fecha")
    ax.set_xticks(
        x,
        [
            f"nº {i + 1}\n{h['created_at'][8:10]}/{h['created_at'][5:7]} {h['created_at'][11:19]}"
            for i, h in enumerate(runs)
        ],
        fontsize=9,
    )
    ax.set_ylim(bottom=0)
    ax.set_xlim(-0.5, len(runs) - 0.5 + 0.8)
    ax.set_ylabel("Error del valor central (MW)")
    ax.set_xlabel("Número de ejecución, día/mes y hora (UTC)")
    _mw_axis(ax)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=4, fontsize=10)
    _title(
        fig,
        "¿Vamos a mejor? Historial de ejecuciones",
        "Cada punto es una ejecución del backtest. Queremos que la línea de «mejor hasta la fecha» baje y cruce la del operador.",
    )
    return _save(fig, out / "historico_ejecuciones.png")


def _pick_days(preds: pl.DataFrame) -> list[tuple[str, dt.date]]:
    days = (
        preds.group_by("target_date")
        .agg(
            pl.len().alias("n"),
            pl.col("n_steps_day").first().alias("nd"),
            pl.col("y_mw").mean().alias("mean"),
        )
        .filter(pl.col("n") == pl.col("nd"))
        .sort("mean")
    )
    if days.height == 0:
        return []
    mean_all = days["mean"].median()
    typical = (
        days.with_columns((pl.col("mean") - mean_all).abs().alias("d")).sort("d").row(0, named=True)
    )
    return [
        ("Día de mucho viento", days.row(days.height - 1, named=True)["target_date"]),
        ("Día de poco viento", days.row(0, named=True)["target_date"]),
        ("Día típico", typical["target_date"]),
    ]


def _date_es(d: dt.date) -> str:
    return f"{d.day} de {MONTHS_ES[d.month - 1]} de {d.year}"


def example_days(card: dict[str, Any], root: Path, out: Path) -> Path | None:
    """Tres días de ejemplo (mucho viento, poco, típico) del mejor modelo con rango."""
    cands = [
        m
        for m in card["models"]
        if m["kind"] != "operador" and m["validation"]["coverage"] is not None
    ]
    if not cands:
        return None
    best = min(cands, key=lambda m: m["validation"]["rmse"])
    preds = pl.read_parquet(root / "runs" / best["validation"]["run_id"] / "predictions.parquet")
    picks = _pick_days(preds)
    if not picks:
        return None
    _setup()
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 6.0), sharey=True)
    fig.subplots_adjust(top=0.74, bottom=0.2, left=0.07, right=0.985, wspace=0.08)
    for ax, (title, day) in zip(axes, picks, strict=True):
        d = preds.filter(pl.col("target_date") == day).sort("ts_utc")
        t = d["ts_utc"].dt.convert_time_zone("Europe/Berlin").dt.replace_time_zone(None).to_numpy()
        ax.fill_between(
            t,
            d["q10_mw"].to_numpy(),
            d["q90_mw"].to_numpy(),
            color=PALETTE[0],
            alpha=0.2,
            lw=0,
            label="Rango del 80 % (Q10–Q90)",
        )
        ax.plot(t, d["q50_mw"].to_numpy(), color=PALETTE[0], lw=2, label="Valor central previsto")
        ax.plot(
            t,
            d["op_q50_mw"].to_numpy(),
            color=OP_COLOR,
            lw=1.6,
            ls="--",
            label="Previsión del operador",
        )
        ax.plot(t, d["y_mw"].to_numpy(), color="#d1495b", lw=2, label="Lo que se produjo")
        ax.set_title(f"{title}\n{_date_es(day)}", loc="left", fontsize=11.5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H"))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))
        ax.set_xlabel("Hora local (Berlín)")
        _mw_axis(ax)
    axes[0].set_ylabel("Generación eólica (MW)")
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.0), fontsize=10)
    _title(
        fig,
        "Tres días de ejemplo: lo previsto frente a lo que pasó",
        f"Modelo: {best['label'].lower()}. Días reales del backtest, elegidos por su viento: el más ventoso, el más calmado y uno típico.",
    )
    return _save(fig, out / "dias_ejemplo.png")


def all_plots(card: dict[str, Any], root: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths = [ranking(card, out), monthly(card, out), coverage(card, out), history(card, out)]
    ex = example_days(card, root, out)
    if ex:
        paths.append(ex)
    return paths
