"""Windstärke-Modell: schätzt je Stunde den Wind in Knoten an Darßer Ort und auf dem Bodden (Barth × Wasserfaktor).

Ergänzt die Ja/Nein-Modelle (P ≥ 14 / 18 kn, train_7day.py) um eine Zahl, damit die Windanimation der App zum
lokalen Modell passt: Die App vergleicht diese Schätzung mit ICON an der Station und verschiebt das Windfeld um
das Verhältnis („heute weht es 15 % mehr, als ICON sonst bedeutet“). Die Tageschancen bleiben unverändert.

Gleiche Merkmale und Daten wie das stündliche 7-Tage-Modell (frühere Modellläufe, Vorlauf 1–7 Tage), gleiche
ehrliche Prüfung: je ein Jahr testen, auf den übrigen trainieren.

Ausgabe
  models/speed7_<station>.json      XGBoost-Regressor (Knoten)
  models/speed7_ref.json            typisches Verhältnis Messung / ICON je Station (Bezug für die App)
  results/speed_skill.csv           mittlerer Fehler (MAE) und Abweichung je Vorlauftag: ICON roh, ICON skaliert, lokal
Danach export_app_models.py laufen lassen, das die Modelle in app/model/ übernimmt.

Aufruf (aus training/): python train_speed.py ../data ../data/dwd ../results ../models
"""
import json, sys
import numpy as np
import pandas as pd
import xgboost as xgb
import train_7day as T
from features import load_obs, water_factor

DATA, DWD, RES, MODELS = sys.argv[1:5]
PARAMS = dict(T.HOURLY_PARAMS, objective="reg:squarederror")


def ref_ratio(H):
    """Typisches Verhältnis Messung / ICON (nur bei ICON ≥ 6 kn): Bezug für „heute mehr oder weniger als sonst“."""
    s = H[H.dwd_ws10 >= 6]
    return float(s.obs_kn.sum() / s.dwd_ws10.sum())


def main():
    sst = {p: T.read_sst(p) for p in ("darss_ort", "barth", "darss_schwelle")}
    bodden = T.read_bodden_proxy()
    for p in T.SITES:
        T.BLH[p] = T.read_blh(p)
    raw = {p: {m: T.read_prev(p, m) for m in T.MOD} for p in ("darss_ort", "barth", "darss_schwelle")}
    feats = json.load(open(f"{MODELS}/hourly7_features.json"))       # exakt wie das stündliche Modell und die App
    rows, refs = [], {}
    for site in T.SITES:
        obs = load_obs(DWD, site)
        H = pd.concat([T.features(site, d, raw[site], raw["barth"], raw["darss_schwelle"], sst[site], sst["darss_schwelle"], bodden)
                       for d in T.LEADS])
        H["obs_kn"] = obs.reindex(H.index).values * water_factor(site, H.index)
        H = H.dropna(subset=["obs_kn", "models_mean_ws10"])
        H = H[H.year.isin(T.YEARS)]
        years = sorted(H.year.unique())
        H["pred"] = np.nan
        H["icon_scaled"] = np.nan
        for test_year in years:
            tr, te = H[H.year != test_year], H[H.year == test_year]
            m = xgb.XGBRegressor(**PARAMS).fit(tr[feats], tr.obs_kn)
            H.loc[te.index, "pred"] = m.predict(te[feats])
            H.loc[te.index, "icon_scaled"] = te.dwd_ws10 * ref_ratio(tr)       # einfache Alternative: ICON × fester Faktor
        for lead in T.LEADS:
            s = H[(H.lead_day == lead) & H.daylight]
            big = s[s.obs_kn >= 10]                                          # dort, wo es fürs Kiten zählt
            row = dict(site=site, lead_day=lead, n=len(s))
            for k, col in (("icon", "dwd_ws10"), ("icon_skaliert", "icon_scaled"), ("lokal", "pred")):
                e = s[col] - s.obs_kn
                row[f"mae_{k}"] = round(float(e.abs().mean()), 2)
                row[f"bias_{k}"] = round(float(e.mean()), 2)
                row[f"mae_ab10_{k}"] = round(float((big[col] - big.obs_kn).abs().mean()), 2)
            rows.append(row)
        final = xgb.XGBRegressor(**PARAMS).fit(H[feats], H.obs_kn)
        final.save_model(f"{MODELS}/speed7_{site}.json")
        refs[site] = round(ref_ratio(H), 4)
        print("fertig", site, "Bezug Messung/ICON", refs[site], flush=True)
    json.dump(refs, open(f"{MODELS}/speed7_ref.json", "w"))
    R = pd.DataFrame(rows)
    R.to_csv(f"{RES}/speed_skill.csv", index=False)
    pd.set_option("display.width", 220)
    print("\nMittlerer Fehler in kn bei Tageslicht (alle Stunden | nur Stunden ab 10 kn gemessen), Jahr-Auslassung:")
    print(R[["site", "lead_day", "n", "mae_icon", "mae_icon_skaliert", "mae_lokal",
             "mae_ab10_icon", "mae_ab10_icon_skaliert", "mae_ab10_lokal", "bias_icon", "bias_lokal"]].to_string(index=False))


if __name__ == "__main__":
    main()
