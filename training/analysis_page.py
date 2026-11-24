"""Zahlen für die Seite „Windanalyse“ der App (app/analyse.json).
Grundlage: Kreuzvalidierte Tagesvorhersagen (results/7day_daily_oof_*), DWD-Messungen 2016–2025."""
import sys, json, glob, numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score
from features import load_obs, water_factor
RES, DWD, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
SEAS = {12: "Winter", 1: "Winter", 2: "Winter", 3: "Frühling", 4: "Frühling", 5: "Frühling",
        6: "Sommer", 7: "Sommer", 8: "Sommer", 9: "Herbst", 10: "Herbst", 11: "Herbst"}
MODELS = {"icon": ("icon_only_run", "icon_only_max"), "gfs": ("gfs_only_run", "gfs_only_max"),
          "ecmwf": ("ecmwf_only_run", "ecmwf_only_max"), "mean3": ("mean3_run", "mean3_max")}
out = {"skill": [], "season": [], "climate": {}, "dirs": {}}

def auc(y, s):
    ok = s.notna()
    return float(roc_auc_score(y[ok], s[ok])) if ok.sum() > 30 and y[ok].nunique() == 2 else None

def hitfa(y, pred):
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum()); fn = int((~pred & (y == 1)).sum())
    return tp / max(tp + fn, 1), fp / max(tp + fp, 1)

for f in sorted(glob.glob(f"{RES}/7day_daily_oof_*.csv")):
    D = pd.read_csv(f); site, thr = D.site.iloc[0], int(D.thr.iloc[0])
    D["season"] = pd.to_datetime(D.local_date).dt.month.map(SEAS)
    for lead in range(1, 8):
        s = D[D.lead_day == lead]
        row = dict(site=site, thr=thr, lead=lead, days=len(s), rate=float(s.y.mean()))
        for k, (run, mx) in MODELS.items():
            row[f"auc_{k}"] = auc(s.y, s[run] + s[mx] / 100)
            if s[run].notna().sum() > 30:
                h, fa = hitfa(s.y, s[run] >= 2); row[f"hit_{k}"], row[f"fa_{k}"] = h, fa
        row["auc_local"] = auc(s.y, s.p_day)
        h, fa = hitfa(s.y, s.p_day >= 0.5); row["hit_local"], row["fa_local"] = h, fa
        out["skill"].append(row)
    for seas in ["Winter", "Frühling", "Sommer", "Herbst"]:
        for grp, leads in (("1–2", (1, 2)), ("3–4", (3, 4)), ("5–7", (5, 6, 7))):
            s = D[(D.season == seas) & D.lead_day.isin(leads)]
            row = dict(site=site, thr=thr, season=seas, leads=grp, days=len(s), rate=float(s.y.mean()))
            for k, (run, mx) in MODELS.items():
                row[f"auc_{k}"] = auc(s.y, s[run] + s[mx] / 100)
            row["auc_local"] = auc(s.y, s.p_day)
            out["season"].append(row)

# Klima aus Messungen ab 2016: Anteil kitebarer Tage je Monat (>= 2 h am Stück bei Tageslicht 7–19)
for site in ("darss_ort", "barth"):
    o = load_obs(DWD, site)
    o = o * water_factor(site, o.index)   # Wind auf dem Wasser, wie im Training
    o = o[o.index >= "2016-01-01"]   # bis gestern, laufendes Jahr mit Punkten in den Boxplots
    d = pd.read_csv(f"{DWD}/{site}_wind.csv", dtype={"MESS_DATUM": str})
    d["time"] = pd.to_datetime(d["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    wd = pd.to_numeric(d.set_index("time")["D"], errors="coerce").where(lambda x: x >= 0)
    df = pd.DataFrame({"kn": o.values, "dir": wd.reindex(o.index).values}, index=o.index.tz_convert("Europe/Berlin"))
    df = df[(df.index.hour >= 7) & (df.index.hour <= 19)]
    clim = {}
    for thr in (14, 18):
        def run(g):
            best = cur = 0; prev = None
            for t, v in g.kn.items():
                cur = cur + 1 if (v >= thr and prev is not None and (t - prev).seconds == 3600 and cur > 0) else (1 if v >= thr else 0)
                best = max(best, cur); prev = t
            return best
        days = df.groupby(df.index.date).apply(lambda g: run(g) if len(g) >= 10 else np.nan).dropna()
        days.index = pd.to_datetime(days.index)
        clim[thr] = [round(float((days[days.index.month == m] >= 2).mean()), 3) for m in range(1, 13)]
        # je Jahr und Monat (für die Boxplots), nur Monate mit mindestens 25 Messtagen
        byy = {}
        for (y, m), g in days.groupby([days.index.year, days.index.month]):
            if len(g) >= 25:
                byy.setdefault(str(y), [None] * 12)[m - 1] = round(float((g >= 2).mean()), 3)
        out.setdefault("climate_years", {}).setdefault(site, {})[str(thr)] = byy
    out["climate"][site] = clim
    # Richtungen in windigen Stunden (>= 12 kn) – 8 Sektoren
    w = df[(df.kn >= 14) & df.dir.notna()]
    sec = ((w.dir + 22.5) % 360 // 45).astype(int)
    out["dirs"][site] = [round(float(v), 3) for v in sec.value_counts(normalize=True).reindex(range(8)).fillna(0)]
# Trefferquote je Prozentbereich (wie oft war es bei dieser Anzeige wirklich kitebar)
BINS = [(0.9, 1.01, "90–100 %"), (0.7, 0.9, "70–90 %"), (0.5, 0.7, "50–70 %"), (0.3, 0.5, "30–50 %"), (0.1, 0.3, "10–30 %"), (0, 0.1, "unter 10 %")]
out["reliability"] = []
for f in sorted(glob.glob(f"{RES}/7day_daily_oof_*.csv")):
    Dd = pd.read_csv(f).dropna(subset=["p_day"]); site, t = Dd.site[0], int(Dd.thr[0])
    for g, leads in (("1–2", (1, 2)), ("3–4", (3, 4)), ("5–7", (5, 6, 7))):
        x = Dd[Dd.lead_day.isin(leads)]
        for lo, hi, lab in BINS:
            y = x[(x.p_day >= lo) & (x.p_day < hi)]
            out["reliability"].append(dict(site=site, thr=t, leads=g, bin=lab, n=int(len(y)),
                                           rate=round(float(y.y.mean()), 3) if len(y) else None))
# Faktoren Wasser/Modell je Spot (sar_spot_model.py) und Richtungen je Spot (spot_directions.py)
sf = pd.read_csv(f"{RES}/sar300_spot_factors.csv")
out["spotfactors"] = json.loads(sf.to_json(orient="records"))
out["bias"] = json.load(open(f"{OUT.rsplit('/', 1)[0]}/model/ens_bias.json"))
json.dump(out, open(OUT, "w"), ensure_ascii=False, indent=0)

sk = pd.DataFrame(out["skill"])
for c in ("auc_icon", "auc_gfs", "auc_ecmwf", "auc_mean3", "auc_local"): sk[c] = sk[c].astype(float)
print(sk[["site", "thr", "lead", "rate", "auc_icon", "auc_gfs", "auc_ecmwf", "auc_mean3", "auc_local", "hit_local", "fa_local", "hit_icon", "fa_icon"]].round(2).to_string())
se = pd.DataFrame(out["season"])
print(se[se.thr == 12].round(2).to_string())
print(json.dumps(out["climate"])); print(out["dirs"])
