"""Train, calibrate and evaluate the ship classifier; write results and the model files.

Splits are by pass (train, calib, test; docs/evaluation.md). Results go to results/ml_*.json|csv and
the model to models/. The error gallery cut-outs go to the demo data folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import average_precision_score, precision_score, recall_score, roc_auc_score

from msp import paths
from msp.config import ROOT, load_config
from msp.evaluate.research import classify
from msp.ml import models
from msp.ml.data import FEATURES, load_chips, load_table, normalize_chips

MODEL_DIR = ROOT / "models"
SCALARS = FEATURES  # the detector's own measurements and the sea state, next to the image


def _scalars(df: pd.DataFrame, stats: dict | None = None):
    x = df[SCALARS].astype(float).to_numpy()
    if stats is None:
        stats = {"mean": np.nanmean(x, 0).tolist(), "std": (np.nanstd(x, 0) + 1e-6).tolist()}
    x = (x - np.array(stats["mean"])) / np.array(stats["std"])
    return np.nan_to_num(x).astype(np.float32), stats


def _boot(df: pd.DataFrame, fn, n: int, seed: int) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    passes = df.pass_id.unique()
    groups = {p: df.index[df.pass_id == p] for p in passes}
    point = fn(df)
    reps = []
    for _ in range(n):
        idx = np.concatenate([groups[p] for p in rng.choice(passes, len(passes))])
        try:
            reps.append(fn(df.loc[idx]))
        except ValueError:
            continue
    reps = np.array([r for r in reps if np.isfinite(r)])
    lo, hi = np.percentile(reps, [2.5, 97.5]) if len(reps) else (np.nan, np.nan)
    return float(point), float(lo), float(hi)


def run(epochs: int = 60, log=print) -> dict:
    cfg = load_config()
    seed = int(cfg.ml.seed)
    nboot = int(cfg.evaluation.bootstrap_resamples)
    table = load_table()
    data = table[table.label != "ambiguous"].copy().reset_index(drop=True)
    data["y"] = (data.label == "ship").astype(int)
    chips = normalize_chips(load_chips(data))
    tr, ca, te = (data.ml_split == s for s in ("train", "calib", "test"))
    log(f"labelled detections: train {tr.sum()}, calib {ca.sum()}, test {te.sum()}; "
        f"ambiguous left out: {(table.label == 'ambiguous').sum()}")
    assert set(data[te].pass_id).isdisjoint(set(data[tr | ca].pass_id))

    # baseline: gradient boosting on detector features + sea state
    xf = data[FEATURES].astype(float).to_numpy()
    gb = models.fit_baseline(xf[tr], data.y[tr].to_numpy(), seed)
    has_len = (data.y == 1) & data.ais_length_m.gt(0)
    gbl = models.fit_baseline_length(xf[tr & has_len], data.ais_length_m[tr & has_len].to_numpy(), seed)
    data["p_gb"] = gb.predict_proba(xf)[:, 1]
    data["len_gb"] = np.exp(gbl.predict(xf))

    # CNN with sea state; temperature scaling on calibration passes
    sc, stats = _scalars(data[tr])
    sc_all, _ = _scalars(data, stats)
    ll = np.log(data.ais_length_m.clip(lower=5).fillna(50.0)).to_numpy()
    net = models.train_cnn(chips[tr], sc_all[tr], data.y[tr].to_numpy(), ll[tr], has_len[tr].to_numpy(),
                           chips[ca], sc_all[ca], data.y[ca].to_numpy(), seed, epochs=epochs, log=log)
    logits, length = models.predict(net, chips, sc_all)
    temp = models.fit_temperature(logits[ca], data.y[ca].to_numpy())
    data["p_cnn"] = 1 / (1 + np.exp(-logits / temp))
    data["len_cnn"] = length
    threshold = 0.5
    yc = data.y[ca]
    calib_check = {"roc_auc_gb": float(roc_auc_score(yc, data.p_gb[ca])),
                   "roc_auc_cnn": float(roc_auc_score(yc, data.p_cnn[ca]))}
    log(f"calibration passes only (model choice): {calib_check}")

    # ---------------------------------------------------------------- evaluation on test passes
    t = data[te].copy()
    res: dict = {"split": {"train_passes": int(data[tr].pass_id.nunique()),
                           "calib_passes": int(data[ca].pass_id.nunique()),
                           "test_passes": int(t.pass_id.nunique()),
                           "train_n": int(tr.sum()), "calib_n": int(ca.sum()), "test_n": int(te.sum()),
                           "test_ships": int(t.y.sum()), "test_not_ships": int((1 - t.y).sum()),
                           "ambiguous_excluded": int((table.label == "ambiguous").sum())},
                 "temperature": temp, "threshold": threshold, "calibration_check": calib_check}
    scores, by_sea, length_tbl = evaluate_set(t, threshold, nboot, seed)
    res["scores"] = scores

    # ---------------------------------------------------------------- write everything
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    (paths.RESULTS / "ml_scores.json").write_text(json.dumps(res, indent=1, default=float))
    by_sea.round(4).to_csv(paths.RESULTS / "ml_by_sea_state.csv", index=False)
    length_tbl.round(4).to_csv(paths.RESULTS / "ml_length_error.csv", index=False)
    MODEL_DIR.mkdir(exist_ok=True)
    torch.save({"state": net.state_dict(), "scalars": list(SCALARS), "stats": stats, "temperature": temp,
                "threshold": threshold}, MODEL_DIR / "shipnet.pt")
    joblib.dump({"clf": gb, "reg": gbl, "features": FEATURES}, MODEL_DIR / "gradient_boosting.joblib")
    predictions = data[["det_id", "pass_id", "det_idx", "ml_split", "label", "p_gb", "p_cnn", "len_gb", "len_cnn"]]
    predictions.to_parquet(paths.WORK / "ml_predictions.parquet")
    gallery(t, chips[te.to_numpy()], threshold)
    return res


def evaluate_set(t: pd.DataFrame, threshold: float, nboot: int, seed: int) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    """Scores of the plain detector, the baseline and the CNN on one labelled set, with pass-bootstrap
    intervals; plus the same by wave class and the length errors."""
    ev = load_config().evaluation
    t = t.copy()
    t["wave_cls"] = classify(t.wave_hs, list(ev.wave_classes_m), "m")
    t["length_cls"] = classify(t.ais_length_m, list(ev.length_classes_m), "m")
    scores = {}
    for name, col in (("detector", None), ("gradient_boosting", "p_gb"), ("cnn", "p_cnn")):
        def prec(d, c=col):
            return precision_score(d.y, (d[c] >= threshold) if c else np.ones(len(d)), zero_division=0)

        def rec(d, c=col):
            return recall_score(d.y, (d[c] >= threshold) if c else np.ones(len(d)), zero_division=0)

        s = {"precision": _boot(t, prec, nboot, seed), "recall": _boot(t, rec, nboot, seed)}
        if col:
            s["roc_auc"] = _boot(t, lambda d, c=col: roc_auc_score(d.y, d[c]), nboot, seed)
            s["pr_auc"] = _boot(t, lambda d, c=col: average_precision_score(d.y, d[c]), nboot, seed)
            s["false_alarms_removed"] = _boot(
                t, lambda d, c=col: ((d[c] < threshold) & (d.y == 0)).sum() / max((d.y == 0).sum(), 1), nboot, seed)
            s["ships_kept"] = _boot(
                t, lambda d, c=col: ((d[c] >= threshold) & (d.y == 1)).sum() / max((d.y == 1).sum(), 1), nboot, seed)
            ece, curve = models.expected_calibration_error(t[col].to_numpy(), t.y.to_numpy())
            s["ece"] = ece
            s["calibration_curve"] = curve
        scores[name] = s
    rows = []
    for cls, d in t.groupby("wave_cls", observed=True):
        if len(d) < 10:
            continue
        for name, col in (("cnn", "p_cnn"), ("gradient_boosting", "p_gb")):
            fr = _boot(d, lambda x, c=col: ((x[c] < threshold) & (x.y == 0)).sum() / max((x.y == 0).sum(), 1), nboot, seed)
            sk = _boot(d, lambda x, c=col: ((x[c] >= threshold) & (x.y == 1)).sum() / max((x.y == 1).sum(), 1), nboot, seed)
            rows.append({"wave_class": str(cls), "model": name, "n": len(d), "ships": int(d.y.sum()),
                         "false_alarms": int((1 - d.y).sum()), "false_alarms_removed": fr[0], "fa_lo": fr[1],
                         "fa_hi": fr[2], "ships_kept": sk[0], "sk_lo": sk[1], "sk_hi": sk[2]})
    by_sea = pd.DataFrame(rows)
    ships = t[(t.y == 1) & t.ais_length_m.gt(0)].copy()
    lrows = []
    for name, col in (("detector", "length_m"), ("gradient_boosting", "len_gb"), ("cnn", "len_cnn")):
        err = (ships[col] - ships.ais_length_m).abs()
        pct = err / ships.ais_length_m
        med = _boot(ships.assign(e=err), lambda d: d.e.median(), nboot, seed)
        medp = _boot(ships.assign(e=pct), lambda d: d.e.median(), nboot, seed)
        lrows.append({"model": name, "length_class": "all", "n": len(ships), "mae_m": med[0], "lo": med[1],
                      "hi": med[2], "mape": medp[0], "mape_lo": medp[1], "mape_hi": medp[2]})
        for cls, d in ships.groupby("length_cls", observed=True):
            e = (d[col] - d.ais_length_m).abs()
            lrows.append({"model": name, "length_class": str(cls), "n": len(d), "mae_m": float(e.median()),
                          "lo": np.nan, "hi": np.nan, "mape": float((e / d.ais_length_m).median()),
                          "mape_lo": np.nan, "mape_hi": np.nan})
    return scores, by_sea, pd.DataFrame(lrows)


def evaluate_frozen() -> dict:
    """Score the committed models, unchanged, on the summer test passes and on the winter passes.

    Nothing is trained or calibrated here: the CNN weights, its temperature and the 0.5 operating point
    are the ones fixed on summer data. Facts about the training run are kept from the earlier results.
    """
    cfg = load_config()
    seed = int(cfg.ml.seed)
    nboot = int(cfg.evaluation.bootstrap_resamples)
    old = json.loads((paths.RESULTS / "ml_scores.json").read_text()) if (paths.RESULTS / "ml_scores.json").exists() else {}
    ck = torch.load(MODEL_DIR / "shipnet.pt", weights_only=False)
    gbm = joblib.load(MODEL_DIR / "gradient_boosting.joblib")
    table = load_table()
    data = table[table.label != "ambiguous"].copy().reset_index(drop=True)
    data["y"] = (data.label == "ship").astype(int)
    chips = normalize_chips(load_chips(data))
    sc, _ = _scalars(data, ck["stats"])
    net = models.ShipNet(len(ck["scalars"]))
    net.load_state_dict(ck["state"])
    logits, length = models.predict(net, chips, sc)
    data["p_cnn"] = 1 / (1 + np.exp(-logits / ck["temperature"]))
    data["len_cnn"] = length
    xf = data[gbm["features"]].astype(float).to_numpy()
    data["p_gb"] = gbm["clf"].predict_proba(xf)[:, 1]
    data["len_gb"] = np.exp(gbm["reg"].predict(xf))
    threshold = float(ck.get("threshold", 0.5))
    res = {"split": dict(old.get("split", {})), "temperature": float(ck["temperature"]), "threshold": threshold,
           "calibration_check": old.get("calibration_check"), "frozen": True}
    seasons, sea_rows, len_rows = {}, [], []
    for name, mask in (("summer", data.ml_split == "test"), ("winter", data.ml_split == "winter")):
        t = data[mask]
        if t.empty:
            continue
        scores, by_sea, length_tbl = evaluate_set(t, threshold, nboot, seed)
        seasons[name] = {"passes": int(t.pass_id.nunique()), "ships": int(t.y.sum()), "not_ships": int((1 - t.y).sum()),
                         "ambiguous_excluded": int(((table.ml_split == ("test" if name == "summer" else "winter"))
                                                    & (table.label == "ambiguous")).sum()),
                         "scores": scores}
        sea_rows.append(by_sea.assign(season=name))
        len_rows.append(length_tbl.assign(season=name))
    res["scores"] = seasons["summer"]["scores"]  # the summer test set, as before
    res["split"].update({"test_passes": seasons["summer"]["passes"], "test_ships": seasons["summer"]["ships"],
                         "test_not_ships": seasons["summer"]["not_ships"], "test_n": seasons["summer"]["ships"] + seasons["summer"]["not_ships"]})
    res["seasons"] = seasons
    paths.RESULTS.mkdir(parents=True, exist_ok=True)
    (paths.RESULTS / "ml_scores.json").write_text(json.dumps(res, indent=1, default=float))
    by_sea = pd.concat(sea_rows, ignore_index=True)
    by_sea.insert(0, "season", by_sea.pop("season"))
    by_sea.round(4).to_csv(paths.RESULTS / "ml_by_sea_state.csv", index=False)
    lt = pd.concat(len_rows, ignore_index=True)
    lt.insert(0, "season", lt.pop("season"))
    lt.round(4).to_csv(paths.RESULTS / "ml_length_error.csv", index=False)
    preds = data[["det_id", "pass_id", "det_idx", "ml_split", "label", "p_gb", "p_cnn", "len_gb", "len_cnn"]]
    preds.to_parquet(paths.WORK / "ml_predictions.parquet")
    test_like = data.ml_split.isin(["test", "winter"]).to_numpy()
    gallery(data[test_like], chips[test_like], threshold)
    return {k: {"passes": v["passes"], "ships": v["ships"], "not_ships": v["not_ships"]} for k, v in seasons.items()}


def gallery(t: pd.DataFrame, chips: np.ndarray, threshold: float, k: int = 6) -> None:
    """Most confident mistakes in both directions, saved as images for the demo."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out = paths.DEMO_DATA / "gallery"
    out.mkdir(parents=True, exist_ok=True)
    t = t.reset_index(drop=True)
    # up to k in each direction and in each season (summer test, winter), so both test sets are shown
    season = np.where(t.get("ml_split", pd.Series("", index=t.index)) == "winter", "winter", "summer")
    groups = []
    for sn in ("summer", "winter"):
        ts = t[season == sn]
        groups.append(("false_alarm_called_ship", ts[(ts.y == 0) & (ts.p_cnn >= threshold)].sort_values("p_cnn", ascending=False).head(k)))
        groups.append(("ship_called_false_alarm", ts[(ts.y == 1) & (ts.p_cnn < threshold)].sort_values("p_cnn").head(k)))
    if out.exists():
        for f in out.glob("*.png"):
            f.unlink()
    items = []
    for kind, d in groups:
        for i, r in d.iterrows():
            img = chips[i, 0]
            fig = plt.figure(figsize=(2, 2), dpi=64)
            plt.imshow(img, cmap="gray", vmin=-1.5, vmax=2.5)
            plt.axis("off")
            name = f"{kind}_{len(items):02d}.png"
            fig.savefig(out / name, bbox_inches="tight", pad_inches=0, metadata={"Software": None})
            plt.close(fig)
            items.append({"image": f"gallery/{name}", "kind": kind, "ship_probability": round(float(r.p_cnn), 3),
                          "truth": "ship" if r.y == 1 else "not a ship", "wind_speed": None if pd.isna(r.wind_speed) else round(float(r.wind_speed), 1),
                          "wave_hs": None if pd.isna(r.wave_hs) else round(float(r.wave_hs), 2), "detector_length_m": round(float(r.length_m)),
                          "ais_length_class": r.ais_length_class if isinstance(r.ais_length_class, str) else None,
                          "ais_type_class": r.ais_type_class if isinstance(r.ais_type_class, str) else None,
                          "peak_vv_db": round(float(r.peak_vv_db), 1), "scr_db": round(float(r.scr_db), 1),
                          "season": "winter" if getattr(r, "ml_split", "") == "winter" else "summer"})
    (out / "gallery.json").write_text(json.dumps(items, indent=1))


def classify_all() -> dict:
    """ship_classify: write model outputs to PostGIS and to the detection GeoParquet files."""
    from msp import db

    pred_path = paths.WORK / "ml_predictions.parquet"
    full = apply_models()
    db.execute("UPDATE detections SET ship_prob = NULL, est_length_m = NULL")
    with db.connect() as conn, conn.cursor() as cur:
        cur.executemany("UPDATE detections SET ship_prob = %s, est_length_m = %s WHERE det_id = %s",
                        [(float(p), float(ln), int(i)) for i, p, ln in zip(full.det_id, full.ship_prob,
                                                                            full.est_length_m, strict=True)])
    import geopandas as gpd

    for pid, g in full.groupby("pass_id"):
        f = paths.PRODUCTS / "detections" / f"{pid}.parquet"
        gdf = gpd.read_parquet(f).drop(columns=["ship_prob", "est_length_m"], errors="ignore")
        gdf = gdf.merge(g[["det_idx", "ship_prob", "est_length_m"]], on="det_idx", how="left")
        gdf.to_parquet(f)
    return {"records": int(len(full)), "predictions_file": pred_path.name}


def apply_models() -> pd.DataFrame:
    """Run the trained CNN on every detection (including ambiguous ones)."""
    ck = torch.load(MODEL_DIR / "shipnet.pt", weights_only=False)
    table = load_table()
    chips = normalize_chips(load_chips(table))
    sc, _ = _scalars(table, ck["stats"])
    net = models.ShipNet(len(ck["scalars"]))
    net.load_state_dict(ck["state"])
    logits, length = models.predict(net, chips, sc)
    table["ship_prob"] = 1 / (1 + np.exp(-logits / ck["temperature"]))
    table["est_length_m"] = length
    return table[["det_id", "pass_id", "det_idx", "ship_prob", "est_length_m"]]


__all__ = ["run", "classify_all", "Path"]
