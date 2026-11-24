"""UBA-Messstation Zingst (DEUB028) einlesen: Stundenwerte Wind 2016–2025 aus der Lieferung des Umweltbundesamts.

Lieferung: je Jahr zwei CSV-Dateien im 24-Spalten-Format, UB<Jahr>SWIG_… (Geschwindigkeit, m/s) und
UB<Jahr>SWIR_… (Richtung, Grad). Zeit in MEZ (UTC+1, ohne Sommerzeit), „Wert01“ = Mittel 00:00–01:00 MEZ,
also Ende des Intervalls; fehlend = −999. Messhöhe 20 m über Grund, Station 54,436836 N / 12,724964 E.

Ausgabe: data/uba/zingst_wind.csv  Spalten time (UTC, Ende der Stunde), ws_ms, wd   – im selben Raster wie DWD
(Stundenwert zur vollen Stunde UTC).

Quelle: Umweltbundesamt (UBA), Datenlizenz Deutschland – Namensnennung 2.0 (https://www.govdata.de/dl-de/by-2-0).
Geändert: Format umgestellt (lange Tabelle, UTC).

Aufruf: python training/import_uba_zingst.py <Ordner oder ZIP der Lieferung>
"""
import glob, io, os, re, sys, zipfile
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "uba", "zingst_wind.csv")


def files(src):
    if src.endswith(".zip"):
        z = zipfile.ZipFile(src)
        return [(n, z.read(n).decode("latin-1")) for n in z.namelist() if n.lower().endswith(".csv")]
    return [(f, open(f, encoding="latin-1").read()) for f in glob.glob(os.path.join(src, "**", "*.csv"), recursive=True)]


def read_one(text):
    d = pd.read_csv(io.StringIO(text), sep=";", quotechar="'")
    vals = [f"Wert{i:02d}" for i in range(1, 25)]
    long = d.melt(id_vars=["Datum"], value_vars=vals, var_name="h", value_name="v")
    end_mez = pd.to_datetime(long["Datum"].astype(str), format="%Y%m%d") + pd.to_timedelta(long["h"].str[4:].astype(int), "h")
    long["time"] = (end_mez - pd.Timedelta(hours=1)).dt.tz_localize("UTC")      # MEZ = UTC+1
    long["v"] = pd.to_numeric(long["v"], errors="coerce").where(lambda s: s > -990)
    return long.set_index("time")["v"]


def main(src):
    parts = {"ws_ms": [], "wd": []}
    for name, text in files(src):
        kind = re.search(r"SWI([GR])", os.path.basename(name))
        if not kind:
            continue
        parts["ws_ms" if kind.group(1) == "G" else "wd"].append(read_one(text))
    df = pd.DataFrame({k: pd.concat(v).sort_index() for k, v in parts.items()})
    df = df[~df.index.duplicated()]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_csv(OUT, index_label="time", float_format="%.2f")
    print(f"{OUT}: {len(df)} Stunden {df.index.min():%Y-%m-%d} – {df.index.max():%Y-%m-%d}, "
          f"fehlend Geschw. {df.ws_ms.isna().mean():.1%}, Richtung {df.wd.isna().mean():.1%}")


def compare():
    """Vergleich mit Darßer Ort und Barth (DWD, falls data/dwd vorhanden): Verhältnis je Windrichtung am Darßer Ort."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from features import load_obs
    import numpy as np
    dwd = os.path.join(ROOT, "data", "dwd")
    if not os.path.exists(os.path.join(dwd, "darss_ort_wind.csv")):
        print("kein data/dwd – Vergleich übersprungen (python training/download_training_data.py --only dwd)")
        return
    z = pd.read_csv(OUT, parse_dates=["time"], index_col="time")
    raw = pd.read_csv(os.path.join(dwd, "darss_ort_wind.csv"), dtype={"MESS_DATUM": str})
    raw["time"] = pd.to_datetime(raw["MESS_DATUM"], format="%Y%m%d%H", utc=True)
    J = pd.DataFrame({"zingst": z.ws_ms * 1.943844, "darss": load_obs(dwd, "darss_ort"), "barth": load_obs(dwd, "barth"),
                      "dir": raw.set_index("time")["D"].where(lambda s: s >= 0)}).dropna()
    x = J[J.darss >= 8]
    sec = ((((x["dir"] + 22.5) % 360) // 45).astype(int)).map(dict(enumerate("N NO O SO S SW W NW".split())))
    R = pd.DataFrame({"n": sec.value_counts(), "zingst_zu_darss": (x.zingst / x.darss).groupby(sec).median(),
                      "zingst_zu_barth": (x.zingst / x.barth).groupby(sec).median()}).round(2)
    R = R.reindex("N NO O SO S SW W NW".split())
    R.to_csv(os.path.join(ROOT, "results", "uba_zingst_vergleich.csv"), index_label="richtung_darss")
    print(f"{J.index.min():%Y}–{J.index.max():%Y}, {len(J)} Stunden, r(Zingst, Darßer Ort) = {J.zingst.corr(J.darss):.2f}, "
          f"r(Zingst, Barth) = {J.zingst.corr(J.barth):.2f}\nMedian Zingst/Station bei ≥ 8 kn am Darßer Ort:\n{R.to_string()}")


if __name__ == "__main__":
    main(sys.argv[1])
    compare()
