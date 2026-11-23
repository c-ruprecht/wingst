"""Exportiert die 7-Tage-Modelle in ein kompaktes JSON für die App und legt Prüfvektoren an.

Aufruf: python export_app_models.py ../data ../data/dwd ../models ../app/model
Format je Modell: {"base": Startwert (Logit), "trees": [[feat], [thr_or_leaf], [left], [right], [default_left]]}
Ein Blatt hat left = -1; dann steht in thr_or_leaf der Blattwert.
"""
import json, sys
import numpy as np
import pandas as pd
import xgboost as xgb

sys.argv = [sys.argv[0]] + sys.argv[1:3] + ["/tmp", sys.argv[3]] + sys.argv[4:]
import train_7day as T

DATA, DWD, MODELS, OUT = sys.argv[1], sys.argv[2], sys.argv[4], sys.argv[5]


def f32(x):
    return float(np.float32(x))


def compact(path):
    m = json.load(open(path))
    L = m["learner"]
    base = float(L["learner_model_param"]["base_score"].strip("[]"))
    trees = []
    for t in L["gradient_booster"]["model"]["trees"]:
        left = t["left_children"]
        trees.append([t["split_indices"], [f32(v) for v in t["split_conditions"]], left, t["right_children"],
                      t["default_left"]])
    return {"base": float(np.log(base / (1 - base))), "trees": trees}


def main():
    hourly_feats = json.load(open(f"{MODELS}/hourly7_features.json"))
    daily_feats = json.load(open(f"{MODELS}/daily7_features.json"))
    manifest = {"version": 1, "hourly_features": hourly_feats, "daily_features": daily_feats,
                "sites": {}, "thresholds": list(T.THRS), "min_run": T.MIN_RUN,
                "note": "Trainiert auf Open-Meteo Previous Runs 2024-2025 gegen DWD Darßer Ort / Barth"}
    sst = {p: T.read_sst(p) for p in ("darss_ort", "barth", "darss_schwelle")}
    raw = {p: {m: T.read_prev(p, m) for m in T.MOD} for p in ("darss_ort", "barth", "darss_schwelle")}
    tests = {}
    for site in T.SITES:
        manifest["sites"][site] = {}
        H = pd.concat([T.features(site, d, raw[site], raw["barth"], raw["darss_schwelle"], sst[site], sst["darss_schwelle"])
                       for d in T.LEADS])
        H = H.dropna(subset=["models_mean_ws10"])
        sample = H.sample(200, random_state=1)
        tests[site] = {"hourly_rows": sample[hourly_feats].astype(float).where(sample[hourly_feats].notna(), None).values.tolist()}
        for thr in T.THRS:
            for level, feats in (("hourly", hourly_feats), ("daily", daily_feats)):
                p = f"{MODELS}/{level}7_{site}_{thr}kn.json"
                c = compact(p)
                fn = f"{level}_{site}_{thr}.json"
                json.dump(c, open(f"{OUT}/{fn}", "w"), separators=(",", ":"))
                manifest["sites"][site].setdefault(level, {})[str(thr)] = fn
            m = xgb.XGBClassifier(); m.load_model(f"{MODELS}/hourly7_{site}_{thr}kn.json")
            tests[site][f"hourly_p{thr}"] = m.predict_proba(sample[hourly_feats])[:, 1].round(6).tolist()
    json.dump(manifest, open(f"{OUT}/manifest.json", "w"), indent=1)
    json.dump(tests, open(f"/tmp/model_parity.json", "w"))
    print("exportiert nach", OUT)


if __name__ == "__main__":
    main()
