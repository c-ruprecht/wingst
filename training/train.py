"""XGBoost-Korrektur mit Jahr-Auslassung, Weglass-Test und SHAP-Faktorenanalyse."""
import json, sys
import numpy as np
import pandas as pd
import xgboost as xgb
import shap
from sklearn.metrics import roc_auc_score, brier_score_loss
from features import build, GROUPS, YEARS

# Aufruf: python train.py data data/dwd results
OM_ROOT, DWD_ROOT, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
PARAMS = dict(n_estimators=500, max_depth=5, learning_rate=0.04, subsample=0.8, colsample_bytree=0.8,
              min_child_weight=5, reg_lambda=1.0, tree_method="hist", eval_metric="logloss", n_jobs=8)
THRESHOLDS = (14, 18)   # 14 kn = kitebar, 18 kn = stark (Wind auf dem Wasser, wie train_7day.py und App)

df = build(OM_ROOT, DWD_ROOT, water=True)
FEATS = [c for c in df.columns if c not in ("obs_kn", "site", "year", "daylight")]
print("Zeilen je Standort/Jahr:\n", df.groupby(["site", "year"]).size().unstack())


def longest_run(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def loyo(d, feats, thr):
    """Gibt pro Testjahr Vorhersagen zurück (nur Tageslicht ausgewertet)."""
    preds = []
    for y in YEARS:
        tr, te = d[d.year != y], d[d.year == y]
        m = xgb.XGBClassifier(**PARAMS)
        m.fit(tr[feats], (tr.obs_kn >= thr).astype(int))
        p = pd.Series(m.predict_proba(te[feats])[:, 1], index=te.index)
        preds.append(p)
    return pd.concat(preds)


results, ablation, shap_rows, runs = [], [], [], []
for site in df.site.unique():
    d = df[df.site == site]
    for thr in THRESHOLDS:
        p = loyo(d, FEATS, thr)
        dd = d.assign(p=p)
        day = dd[dd.daylight]
        y = (day.obs_kn >= thr).astype(int)
        for yr in YEARS + ["alle"]:
            s = day if yr == "alle" else day[day.year == yr]
            ys = (s.obs_kn >= thr).astype(int)
            results.append(dict(site=site, thr=thr, year=yr, n=len(s), rate=ys.mean(),
                                auc_icon=roc_auc_score(ys, s.icon_ws10),
                                auc_mean3=roc_auc_score(ys, s.models_mean_ws10.fillna(s.icon_ws10)),
                                auc_local=roc_auc_score(ys, s.p),
                                brier_local=brier_score_loss(ys, s.p)))
        # Stunden am Stück: Tageslicht-Tage
        g = day.assign(local_date=day.index.tz_convert("Europe/Berlin").date)
        for (yr, dt), s in g.groupby(["year", "local_date"]):
            if len(s) < 10:
                continue
            runs.append(dict(site=site, thr=thr, year=yr,
                             obs=longest_run(s.obs_kn >= thr),
                             icon=longest_run(s.icon_ws10 >= thr),
                             local=longest_run(s.p >= 0.5)))
        # Weglass-Test je Merkmalsgruppe
        base = np.mean([r["auc_local"] for r in results if r["site"] == site and r["thr"] == thr and r["year"] != "alle"])
        for gname, fn in GROUPS.items():
            fs = [f for f in FEATS if not fn(f)]
            pp = loyo(d, fs, thr)
            dd2 = d.assign(p=pp)[d.daylight]
            aucs = [roc_auc_score((s.obs_kn >= thr).astype(int), s.p) for _, s in dd2.groupby("year")]
            ablation.append(dict(site=site, thr=thr, group=gname, auc_without=np.mean(aucs), delta=np.mean(aucs) - base))
        # SHAP auf Modell mit allen Jahren
        m = xgb.XGBClassifier(**PARAMS).fit(d[FEATS], (d.obs_kn >= thr).astype(int))
        sample = d[d.daylight].sample(min(4000, d.daylight.sum()), random_state=0)
        sv = shap.TreeExplainer(m).shap_values(sample[FEATS])
        imp = np.abs(sv).mean(axis=0)
        for f, v in zip(FEATS, imp):
            shap_rows.append(dict(site=site, thr=thr, feature=f, mean_abs_shap=float(v)))
        m.save_model(f"{OUT}/model_{site}_{thr}kn.json")
        print("fertig", site, thr)

R = pd.DataFrame(results); A = pd.DataFrame(ablation); S = pd.DataFrame(shap_rows); RU = pd.DataFrame(runs)
R.to_csv(f"{OUT}/auc_by_year.csv", index=False)
A.to_csv(f"{OUT}/ablation.csv", index=False)
S.to_csv(f"{OUT}/shap.csv", index=False)
RU.to_csv(f"{OUT}/runs.csv", index=False)
json.dump(FEATS, open(f"{OUT}/features.json", "w"))
pd.set_option("display.width", 200)
print(R.round(3).to_string(index=False))
print(A.sort_values(["site", "thr", "delta"]).round(4).to_string(index=False))
rr = RU.groupby(["site", "thr"]).apply(lambda s: pd.Series(dict(
    mae_icon=(s.icon - s.obs).abs().mean(), mae_local=(s.local - s.obs).abs().mean(),
    bias_icon=(s.icon - s.obs).mean(), bias_local=(s.local - s.obs).mean())))
print(rr.round(2))
