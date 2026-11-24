"""Eicht die Tageswahrscheinlichkeit des lokalen Modells an der Wirklichkeit (isotonische Regression).
Grundlage: kreuzvalidierte Tagesvorhersagen results/7day_daily_oof_<station>_<kn>.csv (jedes Jahr mit dem Modell
des anderen Jahres vorhergesagt). Je Station, Schwelle und Vorhersagetag eine monotone Kurve:
roher XGBoost-Wert -> Anteil der Tage, die mit so einem Wert wirklich kitebar waren.
Ausgabe: app/model/calib.json  {station: {kn: {tag: [[x...], [y...]]}}}, results/calibration.csv
"""
import glob, json, sys, numpy as np, pandas as pd
from sklearn.isotonic import IsotonicRegression
RES, OUT = sys.argv[1], sys.argv[2]
cal, rows = {}, []
for f in sorted(glob.glob(f"{RES}/7day_daily_oof_*.csv")):
    D = pd.read_csv(f).dropna(subset=["p_day"]); site, thr = D.site[0], int(D.thr[0])
    cal.setdefault(site, {})[str(thr)] = {}
    for lead in range(1, 8):
        x = D[D.lead_day == lead]
        # je Jahr auf dem anderen Jahr eichen, um den Gewinn ehrlich zu messen
        pc = np.full(len(x), np.nan)
        for yr in x.year.unique():
            tr, te = x.year != yr, (x.year == yr).values
            ir = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip").fit(x.p_day[tr], x.y[tr])
            pc[te] = ir.predict(x.p_day[te])
        brier_raw = float(np.mean((x.p_day - x.y) ** 2)); brier_cal = float(np.mean((pc - x.y) ** 2))
        ir = IsotonicRegression(y_min=0.01, y_max=0.99, out_of_bounds="clip").fit(x.p_day, x.y)
        cal[site][str(thr)][str(lead)] = [[round(float(v), 4) for v in ir.X_thresholds_], [round(float(v), 4) for v in ir.y_thresholds_]]
        rows.append(dict(site=site, thr=thr, lead=lead, n=len(x), brier_roh=round(brier_raw, 4), brier_geeicht=round(brier_cal, 4)))
json.dump(cal, open(f"{OUT}/calib.json", "w"))
r = pd.DataFrame(rows); r.to_csv(f"{RES}/calibration.csv", index=False)
print(r.pivot_table(index=["site", "thr"], columns="lead", values=["brier_roh", "brier_geeicht"]).round(3).to_string())
