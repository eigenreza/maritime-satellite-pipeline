"""The research question: how reliably does free satellite radar see ships, and how do wind, waves and
ship size change that? All numbers are written to results/ by `msp evaluate` (rules: docs/evaluation.md).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression

from msp import db, paths
from msp.config import load_config

AIS_SQL = """
-- Every AIS ship the radar could have seen (inside the imaged sea area), with its length, the sea state
-- at its position, and whether a detection was matched to it
SELECT a.pass_id, p.start_time, a.vessel_hash, v.length_m, v.length_class, v.type_class,
       a.wind_speed, a.wave_hs, a.sog,
       (m.det_id IS NOT NULL) AS detected
FROM ais_at_pass a
JOIN passes p USING (pass_id)
LEFT JOIN vessels v USING (vessel_hash)
LEFT JOIN matches m ON m.pass_id = a.pass_id AND m.vessel_hash = a.vessel_hash
WHERE a.in_scene
"""

DET_SQL = """
-- Every radar detection at sea with its AIS match (if any) and the sea state at its position
SELECT d.det_id, d.pass_id, p.start_time, d.length_m, d.peak_vv_db, d.scr_db, d.wind_speed, d.wave_hs,
       d.ship_prob, (m.vessel_hash IS NOT NULL) AS matched
FROM detections d
JOIN passes p USING (pass_id)
LEFT JOIN matches m USING (det_id)
"""


def classify(values: pd.Series, edges: list[float], unit: str) -> pd.Series:
    labels = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        labels.append(f"{lo:g}-{hi:g} {unit}" if hi < edges[-1] else f"over {lo:g} {unit}")
    return pd.cut(values, bins=edges, labels=labels, right=False)


def splits(passes: pd.DataFrame) -> pd.DataFrame:
    from msp.pipeline.s1 import pass_splits

    return passes.merge(pass_splits(), on="pass_id", how="inner")


def _replicate(passes_by_stratum: list[np.ndarray], rng) -> list[np.ndarray]:
    """One bootstrap draw of passes: resampled within each stratum (season), so the mix stays fixed."""
    return [rng.integers(0, len(p), len(p)) for p in passes_by_stratum]


def _strata(df: pd.DataFrame) -> list[np.ndarray]:
    """Sorted pass ids per season (one stratum when there is one season)."""
    if "season" not in df or df["season"].nunique() <= 1:
        return [np.sort(df["pass_id"].unique())]
    return [np.sort(g.pass_id.unique()) for _, g in df.groupby("season", sort=True)]


def bootstrap_rate(df: pd.DataFrame, group: str, value: str, n: int, seed: int) -> pd.DataFrame:
    """Share of `value` == True per group, with a percentile interval from resampling passes (within
    each season when the data holds more than one)."""
    rng = np.random.default_rng(seed)
    strata = _strata(df)
    passes = np.concatenate(strata)
    point = df.groupby(group, observed=False)[value].agg(["mean", "size", "sum"])
    # counts per pass and group for fast resampling
    tab = df.groupby(["pass_id", group], observed=False)[value].agg(["sum", "size"]).unstack(fill_value=0)
    s = tab["sum"].reindex(passes).to_numpy(float)
    c = tab["size"].reindex(passes).to_numpy(float)
    offsets = np.cumsum([0] + [len(p) for p in strata[:-1]])
    reps = np.empty((n, s.shape[1]))
    for i in range(n):
        idx = np.concatenate([o + k for o, k in zip(offsets, _replicate(strata, rng), strict=True)])
        with np.errstate(invalid="ignore", divide="ignore"):
            reps[i] = s[idx].sum(0) / c[idx].sum(0)
    lo, hi = np.nanpercentile(reps, [2.5, 97.5], axis=0)
    out = pd.DataFrame({group: tab["sum"].columns, "n": point["size"].reindex(tab["sum"].columns).values,
                        "events": point["sum"].reindex(tab["sum"].columns).values,
                        "rate": point["mean"].reindex(tab["sum"].columns).values, "lo": lo, "hi": hi})
    return out


def overall_rate(df: pd.DataFrame, value: str, n: int, seed: int) -> dict:
    d = df.assign(_all="all")
    r = bootstrap_rate(d, "_all", value, n, seed).iloc[0]
    return {"n": int(r.n), "rate": float(r.rate), "lo": float(r.lo), "hi": float(r.hi)}


TERMS = ["length (per doubling)", "wind (per 1 m/s)", "wave height (per 1 m)", "winter (season)"]


def observed_range(known: pd.DataFrame) -> dict:
    """The weather and length range the data covers, for the demo's ship tool: the 97.5th percentile of
    wind and waves met by AIS ships (rounded down, so never past what was observed) and the central 95%
    of their lengths (rounded inward)."""
    down = lambda x: math.floor(float(x) * 10) / 10  # noqa: E731
    return {"wave_max_m": down(np.percentile(known.wave_hs, 97.5)),
            "wind_max_ms": down(np.percentile(known.wind_speed, 97.5)),
            "length_min_m": math.ceil(float(np.percentile(known.length_m, 2.5))),
            "length_max_m": math.floor(float(np.percentile(known.length_m, 97.5))),
            "ships_wind_over_15": int((known.wind_speed >= 15).sum()),
            "ships_waves_over_3": int((known.wave_hs >= 3).sum())}


def logistic(df: pd.DataFrame, n: int, seed: int, season_term: bool = False) -> tuple[pd.DataFrame, list[dict], dict]:
    """Detection ~ log2(length) + wind + wave height (+ a winter indicator); odds ratios with
    pass-bootstrap intervals (passes resampled within each season).

    Also returns the detection probability against wave height for a typical ship (median length and
    wind of the fitted ships, season indicator 0), and the model itself: point coefficients and
    bootstrap draws, so the demo can give an interval for any ship and weather inside the observed range.
    """
    d = df.dropna(subset=["length_m", "wind_speed", "wave_hs"]).copy()
    d = d[d.length_m > 0].reset_index(drop=True)
    cols = [np.log2(d.length_m), d.wind_speed, d.wave_hs]
    if season_term:
        cols.append((d.season == "winter").astype(float))
    x = np.column_stack(cols)
    y = d.detected.astype(int).to_numpy()
    names = TERMS[: x.shape[1]]
    hs_grid = np.round(np.arange(0.0, max(4.0, float(np.nanpercentile(d.wave_hs, 99))) + 0.01, 0.1), 2)
    typ_len, typ_wind = float(np.median(d.length_m)), float(np.median(d.wind_speed))
    extra = [np.zeros_like(hs_grid)] if season_term else []
    typical = np.column_stack([np.full_like(hs_grid, np.log2(typ_len)), np.full_like(hs_grid, typ_wind), hs_grid, *extra])

    def fit(xx, yy):
        m = LogisticRegression(C=1e6, max_iter=2000)
        m.fit(xx, yy)
        return m

    m0 = fit(x, y)
    coef = m0.coef_[0]
    curve0 = m0.predict_proba(typical)[:, 1]
    rng = np.random.default_rng(seed)
    strata = _strata(d)
    groups = {p: np.where(d.pass_id.to_numpy() == p)[0] for p in np.concatenate(strata)}
    reps, curves, draws = [], [], []
    for _ in range(n):
        picks = _replicate(strata, rng)
        idx = np.concatenate([groups[p] for st, k in zip(strata, picks, strict=True) for p in st[k]])
        if len(np.unique(y[idx])) < 2:
            continue
        m = fit(x[idx], y[idx])
        reps.append(m.coef_[0])
        curves.append(m.predict_proba(typical)[:, 1])
        draws.append([float(m.intercept_[0]), *map(float, m.coef_[0])])
    reps = np.array(reps)
    lo, hi = np.percentile(reps, [2.5, 97.5], axis=0)
    clo, chi = np.percentile(np.array(curves), [2.5, 97.5], axis=0)
    table = pd.DataFrame({"term": names, "odds_ratio": np.exp(coef), "lo": np.exp(lo), "hi": np.exp(hi),
                          "n": len(d)})
    curve = [{"hs": float(h), "rate": round(float(r), 4), "lo": round(float(a), 4), "hi": round(float(b), 4)}
             for h, r, a, b in zip(hs_grid, curve0, clo, chi, strict=True)]
    model = {"terms": names, "intercept": float(m0.intercept_[0]), "coef": [float(c) for c in coef],
             "n": int(len(d)), "typical": {"length_m": typ_len, "wind_ms": round(typ_wind, 1)},
             "draws": [[round(v, 5) for v in dr] for dr in draws[::2]]}  # every second draw is plenty for intervals
    return table, curve, model


def cloud_blindness(n: int, seed: int, threshold: float) -> dict:
    rows = [json.loads(line) for line in (paths.WORK / "s2_cloud.jsonl").read_text().splitlines() if line.strip()]
    df = pd.DataFrame(rows).drop_duplicates("day", keep="last")
    df = df[df.cloud_share.notna() & (df.sea_observed_share >= 0.2)]
    blind = (df.cloud_share > threshold).to_numpy()
    rng = np.random.default_rng(seed)
    reps = [blind[rng.integers(0, len(blind), len(blind))].mean() for _ in range(n)]
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return {"days": int(len(df)), "blind_days": int(blind.sum()), "share": float(blind.mean()),
            "lo": float(lo), "hi": float(hi), "threshold": threshold,
            "mean_cloud_share": float(df.cloud_share.mean())}


SEASON_NAMES = ("summer", "winter", "both")


def _missing(df: pd.DataFrame) -> dict:
    return {"rows": int(len(df)), "no_wave": int(df.wave_hs.isna().sum()), "no_wind": int(df.wind_speed.isna().sum())}


def run(out_dir: Path | None = None) -> dict:
    cfg = load_config()
    ev = cfg.evaluation
    n, seed = int(ev.bootstrap_resamples), int(ev.seed)
    out = out_dir or paths.RESULTS
    out.mkdir(parents=True, exist_ok=True)
    passes = db.query_df("SELECT pass_id, start_time FROM passes ORDER BY start_time")
    sp = splits(passes)
    ids = {"summer": set(sp.loc[sp.research_split == "eval", "pass_id"]),
           "winter": set(sp.loc[sp.research_split == "winter", "pass_id"])}
    ids["both"] = ids["summer"] | ids["winter"]
    season_of = dict(zip(sp.pass_id, sp.season, strict=True))
    # fixed row order: SQL returns rows in no set order, and the bootstrap must not depend on it
    ais = db.query_df(AIS_SQL).sort_values(["pass_id", "vessel_hash"], kind="stable").reset_index(drop=True)
    det = db.query_df(DET_SQL).sort_values(["pass_id", "det_id"], kind="stable").reset_index(drop=True)
    for df in (ais, det):
        df["season"] = df.pass_id.map(season_of)
        df["wind_cls"] = classify(df.wind_speed, list(ev.wind_classes_ms), "m/s")
        df["wave_cls"] = classify(df.wave_hs, list(ev.wave_classes_m), "m")
    ais["length_cls"] = classify(ais.length_m, list(ev.length_classes_m), "m")
    det["unmatched"] = ~det.matched

    keys = ("detection_by_length", "detection_by_wind", "detection_by_wave", "unmatched_by_wind", "unmatched_by_wave",
            "ship_size_by_wave", "ship_size_by_wind")
    tables: dict[str, list[pd.DataFrame]] = {k: [] for k in keys}
    seasons = {}
    for name in SEASON_NAMES:
        if not ids[name]:
            continue
        a = ais[ais.pass_id.isin(ids[name])].copy()
        d = det[det.pass_id.isin(ids[name])].copy()
        for key, frame, col, val in (("detection_by_length", a, "length_cls", "detected"),
                                     ("detection_by_wind", a, "wind_cls", "detected"),
                                     ("detection_by_wave", a, "wave_cls", "detected"),
                                     ("unmatched_by_wind", d, "wind_cls", "unmatched"),
                                     ("unmatched_by_wave", d, "wave_cls", "unmatched")):
            tables[key].append(bootstrap_rate(frame.dropna(subset=[col]), col, val, n, seed).assign(season=name))
        # which ships were out in each sea state: the raw rates by wind and waves mix ship size with sea state
        sized = a[a.length_m > 0]
        for key, col in (("ship_size_by_wave", "wave_cls"), ("ship_size_by_wind", "wind_cls")):
            g = sized.dropna(subset=[col]).groupby(col, observed=False).length_m
            tables[key].append(pd.DataFrame({col: g.median().index, "n": g.size().values,
                                             "median_length_m": g.median().values,
                                             "share_over_60m": g.apply(lambda x: (x >= 60).mean()).values,
                                             "season": name}))
        seasons[name] = {"passes": len(ids[name]), "ais_ships": int(len(a)), "detections": int(len(d)),
                         "detection_overall": overall_rate(a, "detected", n, seed),
                         "unmatched_overall": overall_rate(d, "unmatched", n, seed),
                         "missing_sea_state": {"ais": _missing(a), "detections": _missing(d)}}
    for key, parts in tables.items():
        df = pd.concat(parts, ignore_index=True)
        df.insert(0, "season", df.pop("season"))
        df.round(4).to_csv(out / f"{key}.csv", index=False)

    # regression on summer evaluation plus winter, with and without the season term
    both = ais[ais.pass_id.isin(ids["both"])]
    has_winter = bool(ids["winter"])
    lr_rows, models_out = [], {}
    for label, season_term in (("with_season", True), ("without_season", False)):
        if season_term and not has_winter:
            continue
        lr, curve, model = logistic(both, n, seed, season_term=season_term)
        lr_rows.append(lr.assign(model=label))
        models_out[label] = model
        if label == ("with_season" if has_winter else "without_season"):
            (out / "wave_curve.json").write_text(json.dumps(curve))
    lr_all = pd.concat(lr_rows, ignore_index=True)
    lr_all.insert(0, "model", lr_all.pop("model"))
    lr_all.round(4).to_csv(out / "logistic_regression.csv", index=False)
    (out / "logistic_models.json").write_text(json.dumps(models_out))

    # the weather range the data covers (the demo's ship tool stays inside it)
    known = both.dropna(subset=["wind_speed", "wave_hs"])
    known = known[known.length_m > 0]
    tool_range = observed_range(known)
    cloud = cloud_blindness(n, seed, float(cfg.sentinel2.cloud_threshold))
    main = models_out.get("with_season") or models_out.get("without_season") or {}
    summary = {
        "passes_total": int(len(passes)), "passes_dev": int((sp.research_split == "dev").sum()),
        "passes_eval": len(ids["summer"]), "passes_winter": len(ids["winter"]),
        "seasons": seasons,
        "cloud_blindness": cloud,  # optical blindness is a summer result (no optical data for winter)
        "typical_ship": main.get("typical"),
        "tool_range": tool_range,
    }
    (out / "research_summary.json").write_text(json.dumps(summary, indent=1))
    return summary
