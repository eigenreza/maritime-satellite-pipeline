"""Figures for the README and docs, drawn from results/ (PNG, no timestamps in the metadata)."""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from msp import paths  # noqa: E402
from msp.config import ROOT  # noqa: E402

FIG = ROOT / "docs" / "figures"
BLUE, TEAL, ORANGE, PURPLE, GRAY = "#4c6ef5", "#10a37f", "#f0803c", "#7c4dff", "#8a96a3"


def _bars(ax, df, col, color, title, min_n: int = 10):
    """Bars with 95% intervals; classes with fewer than min_n cases are labelled, not drawn."""
    df = df.reset_index(drop=True)
    ok = df.n >= min_n
    x = list(range(len(df)))
    xs = [i for i in x if ok[i]]
    d = df[ok]
    ax.bar(xs, 100 * d.rate, color=color, width=0.65)
    ax.errorbar(xs, 100 * d.rate, yerr=[100 * (d.rate - d.lo), 100 * (d.hi - d.rate)], fmt="none",
                ecolor="#17212b", capsize=3, lw=1)
    for i in x:
        if not ok[i]:
            ax.text(i, 3, "too few", ha="center", va="bottom", fontsize=7, color=GRAY, rotation=90)
    labels = [str(c).replace("over ", "").replace(" m", "+ m") if str(c).startswith("over") else str(c)
              for c in df[col]]
    ax.set_xticks(x, [f"{c}\nn={int(n)}" for c, n in zip(labels, df.n, strict=True)], fontsize=7.5)
    ax.set_ylim(0, 100)
    ax.set_title(title, fontsize=10)
    ax.grid(axis="y", color="#eef1f4")
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def _season(df: pd.DataFrame, season: str) -> pd.DataFrame:
    return df[df.season == season] if "season" in df else df


def main_figure() -> str:
    r = paths.RESULTS
    by_len = pd.read_csv(r / "detection_by_length.csv").query("n > 0")
    by_wave = pd.read_csv(r / "detection_by_wave.csv").query("n > 0")
    un = pd.read_csv(r / "unmatched_by_wave.csv").query("n > 0")
    seasons = [s for s in ("summer", "winter") if "season" not in by_len or s in set(by_len.season)]
    fig, axes = plt.subplots(len(seasons), 3, figsize=(11, 3.5 * len(seasons)), dpi=110, squeeze=False)
    for i, season in enumerate(seasons):
        name = "Summer evaluation" if season == "summer" else "Winter"
        _bars(axes[i][0], _season(by_len, season), "length_cls", BLUE, f"{name}: AIS ships found, by length")
        _bars(axes[i][1], _season(by_wave, season), "wave_cls", TEAL, f"{name}: AIS ships found, by wave height")
        _bars(axes[i][2], _season(un, season), "wave_cls", ORANGE, f"{name}: detections without AIS, by waves")
        axes[i][0].set_ylabel("%")
    fig.text(0.5, 0.005, "Test passes only; bars show 95% intervals from a bootstrap over passes.",
             ha="center", fontsize=8, color="#5a6875")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / "main_result.png"
    fig.savefig(out, metadata={"Software": None})
    plt.close(fig)
    return out.name


def ml_figure() -> str:
    ml = json.loads((paths.RESULTS / "ml_scores.json").read_text())
    sets = [("Summer test", ml["scores"])]
    if ml.get("seasons", {}).get("winter"):
        sets.append(("Winter, no retraining", ml["seasons"]["winter"]["scores"]))
    fig, axes = plt.subplots(1, len(sets) + 1, figsize=(4.4 * (len(sets) + 1), 3.4), dpi=110)
    names = ["Plain detector", "Simple model", "AI (CNN)"]
    keys = ["detector", "gradient_boosting", "cnn"]
    x = range(3)
    for ax, (title, s) in zip(axes, sets, strict=False):
        fa = [0.0] + [s[k]["false_alarms_removed"][0] for k in keys[1:]]
        sk = [1.0] + [s[k]["ships_kept"][0] for k in keys[1:]]
        ax.bar([i - 0.18 for i in x], [100 * v for v in fa], width=0.36, color=PURPLE, label="false alarms removed")
        ax.bar([i + 0.18 for i in x], [100 * v for v in sk], width=0.36, color=TEAL, label="real ships kept")
        ax.set_xticks(list(x), names, fontsize=8)
        ax.set_ylim(0, 105)
        ax.set_ylabel("%")
        ax.set_title(title, fontsize=10)
    axes[0].legend(fontsize=8, frameon=False, ncols=2, loc="upper center", bbox_to_anchor=(0.5, -0.1))
    cal = axes[-1]
    cal.plot([0, 1], [0, 1], ls="--", color=GRAY, lw=1)
    for (title, s), color in zip(sets, (PURPLE, BLUE), strict=False):
        cc = pd.DataFrame(s["cnn"]["calibration_curve"])
        cal.plot(cc.predicted, cc.observed, "o-", color=color, label=f"{title} (ECE {100 * s['cnn']['ece']:.1f}%)")
    cal.set_xlabel("AI ship probability")
    cal.set_ylabel("share that were ships")
    cal.set_title("Calibration", fontsize=10)
    cal.legend(fontsize=7.5, frameon=False)
    for ax in axes:
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
    fig.tight_layout()
    out = FIG / "ship_classifier.png"
    fig.savefig(out, metadata={"Software": None})
    plt.close(fig)
    return out.name


def run() -> list[str]:
    return [main_figure(), ml_figure()]
