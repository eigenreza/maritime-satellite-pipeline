"""README result tables, generated from results/ between markers. `check()` is used by CI.

Markers in README.md look like:  <!-- table:NAME -->  ...  <!-- /table:NAME -->
"""

from __future__ import annotations

import json
import re

import pandas as pd

from msp import paths
from msp.config import ROOT

README = ROOT / "README.md"


def _pct(x) -> str:
    return "n/a" if pd.isna(x) else f"{100 * float(x):.0f}%"


def _ci(lo, hi) -> str:
    return f"{_pct(lo)} to {_pct(hi)}"


MIN_N = 10  # below this many cases a class gets no rate or interval: too few to say anything


def _rate_ci(n, rate, lo, hi) -> tuple[str, str]:
    return ("n/a", "too few to tell") if n < MIN_N else (_pct(rate), _ci(lo, hi))


SEASONS = (("summer", "Summer"), ("winter", "Winter"), ("both", "Both"))


def _cell(row) -> str:
    """Rate with its interval and case count, or "too few to tell"."""
    if row is None or pd.isna(row.n) or row.n == 0:
        return "no cases"
    if row.n < MIN_N:
        return f"too few to tell ({int(row.n)})"
    return f"{_pct(row.rate)} ({_ci(row.lo, row.hi)}; {int(row.n)})"


def _by_season(df: pd.DataFrame, col: str, head: str) -> str:
    """One row per class, one column per season: share found, 95% interval and number of cases."""
    seasons = [(k, lab) for k, lab in SEASONS if k in set(df.season)]
    lines = [f"| {head} | " + " | ".join(lab for _, lab in seasons) + " |", "|---|" + "---:|" * len(seasons)]
    for cls in dict.fromkeys(df[col]):
        cells = []
        for k, _ in seasons:
            m = df[(df.season == k) & (df[col] == cls)]
            cells.append(_cell(next(m.itertuples(), None)))
        if all(c == "no cases" for c in cells):
            continue
        lines.append(f"| {cls} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def tables() -> dict[str, str]:
    r = paths.RESULTS
    rs = json.loads((r / "research_summary.json").read_text())
    ml = json.loads((r / "ml_scores.json").read_text())
    out = {}
    c = rs["cloud_blindness"]
    ss = rs["seasons"]

    out["by_length"] = _by_season(pd.read_csv(r / "detection_by_length.csv"), "length_cls", "Ship length")
    out["by_wave"] = _by_season(pd.read_csv(r / "detection_by_wave.csv"), "wave_cls", "Significant wave height")
    out["by_wind"] = _by_season(pd.read_csv(r / "detection_by_wind.csv"), "wind_cls", "Wind speed (10 m)")
    out["unmatched"] = _by_season(pd.read_csv(r / "unmatched_by_wave.csv"), "wave_cls", "Significant wave height")
    sz = pd.read_csv(r / "ship_size_by_wave.csv")
    seasons = [(k, lab) for k, lab in SEASONS if k in set(sz.season)]
    lines = ["| Significant wave height | " + " | ".join(f"{lab}: median length, share 60 m or longer" for _, lab in seasons) + " |",
             "|---|" + "---:|" * len(seasons)]
    for cls in dict.fromkeys(sz.wave_cls):
        cells = []
        for k, _ in seasons:
            row = next(sz[(sz.season == k) & (sz.wave_cls == cls)].itertuples(), None)
            cells.append("no cases" if row is None or row.n == 0 else f"too few to tell ({int(row.n)})" if row.n < MIN_N
                         else f"{row.median_length_m:.0f} m, {_pct(row.share_over_60m)} ({int(row.n)})")
        if not all(x == "no cases" for x in cells):
            lines.append(f"| {cls} | " + " | ".join(cells) + " |")
    out["size_by_wave"] = "\n".join(lines)

    lr = pd.read_csv(r / "logistic_regression.csv")
    models = [m for m in ("without_season", "with_season") if m in set(lr.model)]
    head = {"without_season": "No season term", "with_season": "With a season term"}
    lines = ["| Effect on the odds of detection | " + " | ".join(head[m] for m in models) + " |", "|---|" + "---:|" * len(models)]
    for term in dict.fromkeys(lr.term):
        cells = []
        for m in models:
            row = next(lr[(lr.model == m) & (lr.term == term)].itertuples(), None)
            cells.append("not in this model" if row is None else f"{row.odds_ratio:.2f} ({row.lo:.2f} to {row.hi:.2f})")
        lines.append(f"| {term} | " + " | ".join(cells) + " |")
    out["logistic"] = "\n".join(lines)

    rows = ["| Measure | " + " | ".join(lab for k, lab in SEASONS if k in ss) + " |", "|---|" + "---:|" * sum(k in ss for k, _ in SEASONS)]

    def line(name, f):
        return f"| {name} | " + " | ".join(f(ss[k]) for k, _ in SEASONS if k in ss) + " |"

    rows.append(line("Test passes", lambda x: str(x["passes"])))
    rows.append(line("AIS ships found by radar", lambda x: f"{_pct(x['detection_overall']['rate'])} ({_ci(x['detection_overall']['lo'], x['detection_overall']['hi'])})"))
    rows.append(line("Radar detections without an AIS match", lambda x: f"{_pct(x['unmatched_overall']['rate'])} ({_ci(x['unmatched_overall']['lo'], x['unmatched_overall']['hi'])})"))
    rows.append(line("AIS ships with no wave data at their spot", lambda x: f"{x['missing_sea_state']['ais']['no_wave']} of {x['missing_sea_state']['ais']['rows']}"))
    rows.append(line("Detections with no wave data at their spot", lambda x: f"{x['missing_sea_state']['detections']['no_wave']} of {x['missing_sea_state']['detections']['rows']}"))
    out["headline"] = "\n".join(rows + [
        f"| Optical days blind, summer (more than 50% cloud over sea) | {_pct(c['share'])} ({_ci(c['lo'], c['hi'])}) | | |",
        f"| Passes: development / summer evaluation / winter | {rs['passes_dev']} / {rs['passes_eval']} / {rs['passes_winter']} | | |",
    ])
    ms = ml.get("seasons", {})
    sw = ms.get("winter", {}).get("scores")
    s = ml["scores"]
    both = ss.get("both") or ss.get("summer")
    out["summary"] = "\n".join([
        "| Radar passes | AIS ships found by radar | False alarms removed by the AI (summer test, winter) | Real ships the AI keeps (summer test, winter) |",
        "|---:|---:|---:|---:|",
        f"| {rs['passes_total']} | {_pct(both['detection_overall']['rate'])} | {_pct(s['cnn']['false_alarms_removed'][0])}, "
        f"{_pct(sw['cnn']['false_alarms_removed'][0]) if sw else 'n/a'} | {_pct(s['cnn']['ships_kept'][0])}, "
        f"{_pct(sw['cnn']['ships_kept'][0]) if sw else 'n/a'} |"])

    def tri(t):
        return f"{_pct(t[0])} ({_pct(t[1])} to {_pct(t[2])})"

    def ml_table(sc: dict, title: str) -> str:
        rows = [f"| {title} | Plain detector | Simple model | AI (CNN) |", "|---|---:|---:|---:|"]
        rows.append(f"| Precision | {tri(sc['detector']['precision'])} | {tri(sc['gradient_boosting']['precision'])} | {tri(sc['cnn']['precision'])} |")
        rows.append(f"| Recall | {tri(sc['detector']['recall'])} | {tri(sc['gradient_boosting']['recall'])} | {tri(sc['cnn']['recall'])} |")
        rows.append(f"| False alarms removed | 0% | {tri(sc['gradient_boosting']['false_alarms_removed'])} | {tri(sc['cnn']['false_alarms_removed'])} |")
        rows.append(f"| Real ships kept | 100% | {tri(sc['gradient_boosting']['ships_kept'])} | {tri(sc['cnn']['ships_kept'])} |")
        rows.append(f"| ROC AUC | | {sc['gradient_boosting']['roc_auc'][0]:.3f} | {sc['cnn']['roc_auc'][0]:.3f} |")
        rows.append(f"| PR AUC | | {sc['gradient_boosting']['pr_auc'][0]:.3f} | {sc['cnn']['pr_auc'][0]:.3f} |")
        rows.append(f"| Expected calibration error | | {100 * sc['gradient_boosting']['ece']:.1f}% | {100 * sc['cnn']['ece']:.1f}% |")
        return "\n".join(rows)

    out["ml"] = ml_table(s, "Summer test passes")
    if sw:
        out["ml_winter"] = ml_table(sw, "Winter passes (no retraining)")
    le = pd.read_csv(r / "ml_length_error.csv")
    le = le[le.length_class == "all"]
    names = {"detector": "Size of the bright spot", "gradient_boosting": "Simple model", "cnn": "AI (CNN)"}
    out["length"] = "\n".join(["| Length estimate | Season | Median error | 95% interval | In percent |", "|---|---|---:|---|---:|"] +
                              [f"| {names[row.model]} | {row.season} | {row.mae_m:.0f} m | {row.lo:.0f} to {row.hi:.0f} m | {_pct(row.mape)} |"
                               for _, row in le.iterrows()])
    sea = pd.read_csv(r / "ml_by_sea_state.csv")
    sea = sea[sea.model == "cnn"]

    def cell(k, v, lo, hi):
        return "too few to tell" if k < MIN_N else f"{_pct(v)} ({_ci(lo, hi)})"

    out["ml_sea"] = "\n".join(
        ["| Season | Significant wave height | Detections (ships / false alarms) | False alarms removed | Real ships kept |",
         "|---|---|---:|---:|---:|"] +
        [f"| {row.season} | {row.wave_class} | {int(row.n)} ({int(row.ships)} / {int(row.false_alarms)}) | "
         f"{cell(row.false_alarms, row.false_alarms_removed, row.fa_lo, row.fa_hi)} | "
         f"{cell(row.ships, row.ships_kept, row.sk_lo, row.sk_hi)} |" for _, row in sea.iterrows()])
    pf = r / "pipeline_facts.json"
    cmp_ = r / "sar_engine_comparison.json"
    if pf.exists() and cmp_.exists():
        f, cm = json.loads(pf.read_text()), json.loads(cmp_.read_text())
        out["pipeline"] = "\n".join([
            "| Pipeline fact | Value |", "|---|---:|",
            f"| Sentinel-1 passes processed | {f['passes']} |",
            f"| Unique raw input data (GB) | {f['raw_gigabytes']} |",
            f"| Task runs in the replayed periods | {f['task_runs']} |",
            f"| Retries / failures recovered | {f['retries']} / {f['recovered']} |",
            f"| Quality checks run | {f['qc_checks']} |",
            f"| STAC items catalogued | {f['stac_items']} |",
            f"| SNAP vs Python fallback, median sigma0 difference over sea | {cm['median_difference_db']} dB |",
            f"| SNAP / Python fallback run time for one scene | {cm['snap_seconds']:.0f} s / {cm['python_seconds']:.0f} s |",
        ])
    return out


def _render(text: str, tabs: dict[str, str]) -> str:
    for name, body in tabs.items():
        pat = re.compile(rf"(<!-- table:{name} -->\n)(.*?)(<!-- /table:{name} -->)", re.S)
        text = pat.sub(lambda m, b=body: m.group(1) + b + "\n" + m.group(3), text)
    return text


def write() -> bool:
    text = README.read_text(encoding="utf-8")
    README.write_text(_render(text, tables()), encoding="utf-8")
    return True


def check() -> bool:
    text = README.read_text(encoding="utf-8")
    want = _render(text, tables())
    if want != text:
        print("README tables differ from results/. Run: msp readme-tables")
        return False
    names = re.findall(r"<!-- table:(\w+) -->", text)
    missing = [n for n in tables() if n not in names]
    if missing:
        print(f"README is missing tables: {missing}")
    print(f"README tables match results/ ({len(names)} tables)")
    return True
