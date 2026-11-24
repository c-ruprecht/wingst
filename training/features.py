"""Merkmale und Zielgrößen für das lokale Kite-Windmodell.

Wichtig: Alle Merkmale stammen aus Modellvorhersagen von Open-Meteo, die die App
später live abrufen kann. Messwerte dienen nur als Zielgröße.
"""
import glob, os
import numpy as np
import pandas as pd

MS2KN = 1 / 0.514444
YEARS = [2023, 2024, 2025]          # ICON-Archiv ab Ende 2022 vollständig
SITES = {"darss_ort": "06097", "barth": "00298"}
HEIGHT_FACTOR = {"darss_ort": 0.983, "barth": 1.0}   # 12 m -> 10 m (log-Profil, z0 = 0.2 mm)

MODEL_VARS = {
    "icon_seamless": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_80m",
                      "temperature_2m", "pressure_msl", "cloud_cover", "shortwave_radiation", "cape",
                      "precipitation", "temperature_850hPa", "wind_speed_850hPa", "wind_direction_850hPa"],
    "gfs_seamless": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "wind_speed_80m",
                     "temperature_2m", "cloud_cover", "temperature_850hPa", "wind_speed_850hPa"],
    "ecmwf_ifs025": ["wind_speed_10m", "wind_direction_10m", "wind_gusts_10m"],
}
SHORT = {"icon_seamless": "icon", "gfs_seamless": "gfs", "ecmwf_ifs025": "ecmwf"}


def read_om(root, place, model):
    fs = sorted(glob.glob(f"{root}/openmeteo/{place}_{model}_*.csv"))
    d = pd.concat([pd.read_csv(f) for f in fs])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    return d.drop_duplicates("time").set_index("time").sort_index()


def model_frame(root, place):
    parts = []
    for m, vars_ in MODEL_VARS.items():
        d = read_om(root, place, m)[vars_]
        d.columns = [f"{SHORT[m]}_{c}" for c in vars_]
        parts.append(d)
    sst = read_om(root, place, "sst")[["sea_surface_temperature"]].rename(columns={"sea_surface_temperature": "sst"})
    parts.append(sst)
    return pd.concat(parts, axis=1)


def add_derived(df, ctx):
    """ctx: Modellwerte anderer Orte (für Gradienten und Land-See-Kontrast)."""
    out = pd.DataFrame(index=df.index)
    # --- 1. Modellwind ---
    for m in ("icon", "gfs", "ecmwf"):
        out[f"{m}_ws10"] = df[f"{m}_wind_speed_10m"]
        out[f"{m}_gust"] = df[f"{m}_wind_gusts_10m"]
        rad = np.deg2rad(df[f"{m}_wind_direction_10m"])
        out[f"{m}_u"] = -df[f"{m}_wind_speed_10m"] * np.sin(rad)
        out[f"{m}_v"] = -df[f"{m}_wind_speed_10m"] * np.cos(rad)
    out["icon_ws80"] = df["icon_wind_speed_80m"]
    out["gfs_ws80"] = df["gfs_wind_speed_80m"]
    ws = out[["icon_ws10", "gfs_ws10", "ecmwf_ws10"]]
    out["models_mean_ws10"] = ws.mean(axis=1)
    out["models_spread_ws10"] = ws.std(axis=1)
    out["icon_gust_factor"] = out["icon_gust"] / out["icon_ws10"].clip(lower=1)
    out["icon_shear_80_10"] = out["icon_ws80"] / out["icon_ws10"].clip(lower=1)
    # --- 2. Richtung ---
    rad = np.deg2rad(df["icon_wind_direction_10m"])
    out["dir_sin"], out["dir_cos"] = np.sin(rad), np.cos(rad)
    rad850 = np.deg2rad(df["icon_wind_direction_850hPa"])
    out["dir850_sin"], out["dir850_cos"] = np.sin(rad850), np.cos(rad850)
    out["ws850"] = df["icon_wind_speed_850hPa"].fillna(df["gfs_wind_speed_850hPa"])
    # --- 3. Stabilität / Wassertemperatur ---
    out["sst"] = df["sst"]
    out["t2m_minus_sst"] = df["icon_temperature_2m"] - df["sst"]
    t850 = df["icon_temperature_850hPa"].fillna(df["gfs_temperature_850hPa"])
    out["t850_minus_sst"] = t850 - df["sst"]
    # --- 4. Sonne / Seewind ---
    out["swrad"] = df["icon_shortwave_radiation"]
    out["cloud"] = df["icon_cloud_cover"]
    out["land_sea_dT"] = ctx["barth_t2m"] - ctx["sea_sst"]
    hour = out.index.hour + out.index.minute / 60
    out["hour_sin"], out["hour_cos"] = np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24)
    doy = out.index.dayofyear
    out["doy_sin"], out["doy_cos"] = np.sin(2 * np.pi * doy / 365.25), np.cos(2 * np.pi * doy / 365.25)
    # --- 5. Druck / Konvektion ---
    out["dp_north_south"] = ctx["sea_pmsl"] - ctx["barth_pmsl"]
    out["cape"] = df["icon_cape"]
    out["precip"] = df["icon_precipitation"]
    # --- 6. Luftdichte ---
    out["air_density"] = df["icon_pressure_msl"] * 100 / (287.05 * (df["icon_temperature_2m"] + 273.15))
    return out


GROUPS = {
    "Andere Modelle (GFS, ECMWF, Mittel, Streuung)": lambda c: c.startswith(("gfs_", "ecmwf_", "models_")),
    "Wassertemperatur / Stabilität": lambda c: c in ("sst", "t2m_minus_sst", "t850_minus_sst"),
    "Richtung (10 m, 850 hPa)": lambda c: c.startswith(("dir_", "dir850_")),
    "Sonne / Tageszeit / Land-See": lambda c: c in ("swrad", "cloud", "land_sea_dT", "hour_sin", "hour_cos"),
    "Jahreszeit": lambda c: c.startswith("doy_"),
    "Höhenwind / Scherung / Böen": lambda c: c in ("ws850", "icon_ws80", "icon_shear_80_10", "icon_gust_factor", "icon_gust"),
    "Druckgefälle / Schauer": lambda c: c in ("dp_north_south", "cape", "precip"),
    "Luftdichte": lambda c: c == "air_density",
}


def load_obs(dwd_root, site):
    d = pd.read_csv(f"{dwd_root}/{site}_wind.csv", dtype={"MESS_DATUM": str})
    d["time"] = pd.to_datetime(d["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    d["F"] = pd.to_numeric(d["F"], errors="coerce")
    d.loc[d["F"] < 0, "F"] = np.nan
    d = d.set_index("time").sort_index()
    # hängende Sensoren: >= 6 h exakt gleicher Wert
    run = (d["F"].diff() != 0).cumsum()
    stuck = d.groupby(run)["F"].transform("size") >= 6
    d.loc[stuck & (d["F"] > 0), "F"] = np.nan
    return (d["F"] * MS2KN * HEIGHT_FACTOR[site]).rename("obs_kn")


def build(om_root, dwd_root):
    ctx_raw = {p: model_frame(om_root, p) for p in ("barth", "darss_schwelle")}
    ctx = pd.DataFrame({
        "barth_t2m": ctx_raw["barth"]["icon_temperature_2m"],
        "barth_pmsl": ctx_raw["barth"]["icon_pressure_msl"],
        "sea_sst": ctx_raw["darss_schwelle"]["sst"],
        "sea_pmsl": ctx_raw["darss_schwelle"]["icon_pressure_msl"],
    })
    frames = []
    for site in SITES:
        mf = model_frame(om_root, site)
        X = add_derived(mf, ctx.reindex(mf.index))
        y = load_obs(dwd_root, site).reindex(X.index)
        X["obs_kn"] = y
        X["site"] = site
        frames.append(X)
    df = pd.concat(frames)
    df = df[df.index.year.isin(YEARS)]
    local = df.index.tz_convert("Europe/Berlin")
    df["year"] = local.year
    df["daylight"] = (local.hour >= 7) & (local.hour <= 19)
    return df.dropna(subset=["obs_kn", "icon_ws10"])


_WF = None


def water_factor(site, index):
    """Faktor Station -> Wind auf dem Wasser je Zeitpunkt (Barth: je Jahreszeit und Ortszeit, siehe water_factor.py)."""
    global _WF
    if site != "barth":
        return np.ones(len(index))
    if _WF is None:
        import json, os
        _WF = np.array(json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "model", "water_factor.json")))["barth"])
    loc = pd.DatetimeIndex(index).tz_convert("Europe/Berlin")
    m = loc.month
    se = np.select([np.isin(m, [3, 4, 5]), np.isin(m, [6, 7, 8]), np.isin(m, [9, 10, 11])], [1, 2, 3], 0)
    return _WF[se, loc.hour]
