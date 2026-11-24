"""Prüft die Ensemble-Prozente der App an den DWD-Messungen und vergleicht sie mit dem lokalen Modell.

Rechnet genau wie die App (app/ensemble.js): je Lauf Richtungskorrektur (ens_bias.json), am Bodden mal Wasserfaktor,
dann Anteil der Läufe mit mindestens 2 h am Stück über der Schwelle bei Tageslicht (7–19 Uhr). Modelle gleich gewichtet.
Gemessen wird auf Ebene der Station (ohne Richtungsfenster der Spots), wie beim lokalen Modell.

Eingaben
  data/ai_history/<modell>_<station>.csv.gz   aus download_ai_history.py --only ens (und optional aifs-ens, wn2)
  data/dwd/<station>_wind.csv                 aus download_training_data.py --only dwd
  results/7day_daily_oof_<station>_<kn>.csv   aus train_7day.py (lokales Modell, kreuzvalidiert)
Aufruf (aus training/):  python validate_ens.py ../data ../results ../app/model
Ergebnis
  results/ens_validation.csv        Brier, AUC, Trefferquote je Station, Schwelle, Vorlauftag und Quelle
  results/ens_reliability.csv       Zuverlässigkeit (vorhergesagt vs. beobachtet) in 10-%-Klassen
  <app/model>/ens_calib.json        isotone Eichung der Ensemble-Prozente je Station/Schwelle/Vorlauf (für die App)
  Konsole: ab welchem Tag das Ensemble das lokale Modell schlägt (Vorschlag für LOCAL_DAYS in app.js)
"""
import glob, json, os, sys
import numpy as np, pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score
from features import load_obs, MS2KN

DATA, RES, MODEL_DIR = (sys.argv + [None, None, None, None])[1:4]
DATA, RES, MODEL_DIR = DATA or "../data", RES or "../results", MODEL_DIR or "../app/model"
SITES = ("darss_ort", "barth")
THRS = (14, 18)
MIN_RUN = 2
WATER_FACTOR = {"darss_ort": 1.0, "barth": 1.22}     # wie train_7day.py und app/ensemble.js
BIAS_KEY = {"ifs_ens": "ecmwf", "aifs_ens": "ecmwf", "gefs": "gfs", "icon_eps": "icon", "wn2": None}
TZ = "Europe/Berlin"


def longest_run(ok):
    best = run = 0
    for v in ok:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def corrected(ws, wd, site_bias, key):
    sec = np.floor(((wd + 22.5) % 360) / 45).astype(int) % 8
    if key and key in site_bias:
        b = np.asarray(site_bias[key]["sector"])[sec]
    else:
        b = np.mean([site_bias[k]["sector"] for k in site_bias], axis=0)[sec]
    return np.maximum(0, ws + b)


def ens_daily(path, site, site_bias, key):
    """Je (Startzeit, Ortsdatum): Anteil der Läufe mit Lauf >= MIN_RUN h über Schwelle, je Schwelle."""
    d = pd.read_csv(path, parse_dates=["init_time"])
    d = d.dropna(subset=["u10", "v10"])
    out = []
    hours = np.arange(0, 361)
    for init, g in d.groupby("init_time"):
        init = pd.Timestamp(init).tz_localize("UTC") if pd.Timestamp(init).tzinfo is None else pd.Timestamp(init)
        times = (init + pd.to_timedelta(hours, "h")).tz_convert(TZ)
        loc_h, loc_d = times.hour.values, times.date
        lead_day = np.array([(x - init.tz_convert(TZ).date()).days for x in loc_d])
        day_mask = (loc_h >= 7) & (loc_h <= 19) & (lead_day >= 1) & (lead_day <= 14)
        per_member = {}
        for mem, m in g.groupby("member"):
            m = m.sort_values("lead_h")
            if len(m) < 10:
                continue
            u = np.interp(hours, m.lead_h.values, m.u10.values, left=np.nan, right=np.nan)
            v = np.interp(hours, m.lead_h.values, m.v10.values, left=np.nan, right=np.nan)
            ws = np.hypot(u, v) * MS2KN
            wd = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
            per_member[mem] = corrected(ws, np.nan_to_num(wd), site_bias, key) * WATER_FACTOR[site]
            per_member[mem][np.isnan(u)] = np.nan
        if not per_member:
            continue
        W = np.vstack(list(per_member.values()))          # Läufe x Stunden
        for ld in np.unique(lead_day[day_mask]):
            idx = np.where(day_mask & (lead_day == ld))[0]
            sub = W[:, idx]
            valid = (~np.isnan(sub)).sum(1) >= min(4, len(idx))
            if valid.sum() == 0:
                continue
            row = {"init": init.date(), "local_date": loc_d[idx[0]], "lead_day": int(ld), "n_members": int(valid.sum())}
            for thr in THRS:
                runs = [longest_run(r >= thr) for r in sub[valid]]
                row[f"p{thr}"] = float(np.mean(np.array(runs) >= MIN_RUN))
            out.append(row)
    return pd.DataFrame(out)


def obs_labels(site):
    o = load_obs(f"{DATA}/dwd", site) * WATER_FACTOR[site]
    o.index = o.index.tz_convert(TZ)
    o = o[(o.index.hour >= 7) & (o.index.hour <= 19)]
    rows = []
    for day, g in o.groupby(o.index.date):
        if g.notna().sum() < 10:
            continue
        r = {"local_date": day}
        for thr in THRS:
            r[f"y{thr}"] = int(longest_run((g >= thr).values) >= MIN_RUN)
        rows.append(r)
    return pd.DataFrame(rows)


def score(y, p):
    y, p = np.asarray(y), np.asarray(p)
    return dict(n=len(y), base=y.mean(), brier=np.mean((p - y) ** 2),
                auc=roc_auc_score(y, p) if 0 < y.sum() < len(y) else np.nan,
                hit=((p >= 0.5) & (y == 1)).sum() / max(1, y.sum()),
                fa=((p >= 0.5) & (y == 0)).sum() / max(1, (p >= 0.5).sum()))


def main():
    bias = json.load(open(f"{MODEL_DIR}/ens_bias.json"))
    rows, rel, calib = [], [], {}
    for site in SITES:
        y = obs_labels(site)
        src = {}
        for path in sorted(glob.glob(f"{DATA}/ai_history/*_{site}.csv.gz")):
            label = os.path.basename(path).replace(f"_{site}.csv.gz", "")
            if label not in BIAS_KEY:
                continue
            print("  ", site, label, flush=True)
            e = ens_daily(path, site, bias[site], BIAS_KEY[label])
            if len(e):
                src[label] = e
        if not src:
            print(f"{site}: keine Ensemble-Archive in {DATA}/ai_history – erst download_ai_history.py --only ens")
            continue
        # Mittel der Modelle (gleich gewichtet), nur wo alle vorhandenen Modelle des Tages da sind
        allm = pd.concat([e.assign(src=k) for k, e in src.items()])
        mean = allm.groupby(["local_date", "lead_day"])[[f"p{t}" for t in THRS]].mean().reset_index().assign(src="mittel")
        allm = pd.concat([allm, mean])
        allm["local_date"] = pd.to_datetime(allm.local_date).dt.date
        m = allm.merge(y, on="local_date")
        m["year"] = pd.to_datetime(m.local_date).dt.year
        calib[site] = {}
        for thr in THRS:
            oof_p = f"{RES}/7day_daily_oof_{site}_{thr}.csv"
            loc = pd.read_csv(oof_p, parse_dates=["local_date"]) if os.path.exists(oof_p) else None
            if loc is not None:
                loc["local_date"] = loc.local_date.dt.date
            calib[site][str(thr)] = {}
            for (s_, ld), g in m.groupby(["src", "lead_day"]):
                rows.append(dict(site=site, thr=thr, src=s_, lead_day=ld, **score(g[f"y{thr}"], g[f"p{thr}"])))
                if loc is not None and s_ == "mittel":   # direkter Vergleich auf denselben Tagen
                    both = g.merge(loc[loc.lead_day == ld][["local_date", "p_day"]], on="local_date")
                    if len(both) > 50:
                        rows.append(dict(site=site, thr=thr, src="lokal_gleiche_tage", lead_day=ld, **score(both[f"y{thr}"], both.p_day)))
                        rows.append(dict(site=site, thr=thr, src="mittel_gleiche_tage", lead_day=ld, **score(both[f"y{thr}"], both[f"p{thr}"])))
                if s_ == "mittel":
                    b = pd.cut(g[f"p{thr}"], np.linspace(0, 1, 11), include_lowest=True)
                    for k, gg in g.groupby(b, observed=True):
                        rel.append(dict(site=site, thr=thr, lead_day=ld, bin=str(k), n=len(gg),
                                        p_mean=gg[f"p{thr}"].mean(), obs=gg[f"y{thr}"].mean()))
                    iso = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(g[f"p{thr}"], g[f"y{thr}"])
                    xs = np.round(np.linspace(0, 1, 21), 2)
                    calib[site][str(thr)][str(int(ld))] = {"x": xs.tolist(), "y": np.round(iso.predict(xs), 3).tolist(), "n": len(g)}
    if not rows:
        return
    R = pd.DataFrame(rows)
    R.to_csv(f"{RES}/ens_validation.csv", index=False)
    pd.DataFrame(rel).to_csv(f"{RES}/ens_reliability.csv", index=False)
    json.dump(calib, open(f"{MODEL_DIR}/ens_calib.json", "w"))
    print("\nBrier (kleiner = besser) auf denselben Tagen: lokal vs. Ensemble-Mittel")
    for (site, thr), g in R[R.src.isin(["lokal_gleiche_tage", "mittel_gleiche_tage"])].groupby(["site", "thr"]):
        t = g.pivot(index="lead_day", columns="src", values="brier")
        print(f"\n{site} {thr} kn\n", t.round(3).to_string())
        better = t.index[t["mittel_gleiche_tage"] < t["lokal_gleiche_tage"]]
        print("  Ensemble besser ab Tag:", int(better.min()) if len(better) else "nie (bis Tag 7)")
    print("\nJe Quelle und Vorlauf (Ensemble-Mittel):")
    print(R[R.src == "mittel"].pivot_table(index="lead_day", columns=["site", "thr"], values="auc").round(2).to_string())


if __name__ == "__main__":
    main()
