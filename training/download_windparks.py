"""Stromerzeugung der Offshore-Windparks in der westlichen Ostsee (ENTSO-E, „Actual Generation per Generation Unit“ 16.1.A).

Ein Windpark ist ein großer Windmesser auf Nabenhöhe (~100 m) über offener See. Zwischen etwa 7 und 25 kn steigt die
Leistung steil mit dem Wind – genau der Kitebereich. ENTSO-E veröffentlicht nur Einheiten ab 100 MW, also:
  50Hertz (DE):  Baltic 2, Wikinger, Arkona, Baltic Eagle, Windanker
  DK2 (Dänemark): Kriegers Flak, Rødsand 2, Nysted (Rødsand 1)   – westlich/nordwestlich, Westwind kommt dort zuerst an
Nicht enthalten: EnBW Baltic 1 (48 MW, direkt vor Zingst) – nur auf Anfrage bei EnBW.

Zugang (kostenlos):
  1. Konto anlegen: https://transparency.entsoe.eu  (oben rechts „Login“ → „Register“)
  2. E-Mail an transparency@entsoe.eu, Betreff „Restful API access“, im Text die Konto-E-Mail-Adresse
  3. Nach Freischaltung: „My Account Settings“ → „Generate a new token“
Aufruf:
  pip install entsoe-py
  export ENTSOE_TOKEN=...
  python download_windparks.py --start 2019-01-01 --end 2025-12-31
Ergebnis: data/windparks/<zone>_<JJJJ-MM>.csv (MW je Park, stündlich, UTC) und data/windparks/parks.csv (Name, Leistung)
"""
import argparse, os, re, time
import pandas as pd

OUT = "data/windparks"
ZONES = ["DE_50HZ", "DK_2"]
KEEP = re.compile(r"baltic|arkona|wikinger|eagle|windanker|kriegers|r.dsand|nysted", re.I)


def main(start, end):
    from entsoe import EntsoePandasClient
    tok = os.environ.get("ENTSOE_TOKEN")
    if not tok:
        raise SystemExit("ENTSOE_TOKEN fehlt (siehe Kopf der Datei).")
    cl = EntsoePandasClient(api_key=tok)
    os.makedirs(OUT, exist_ok=True)
    parks = {}
    for zone in ZONES:
        for m in pd.period_range(start, end, freq="M"):
            fn = f"{OUT}/{zone}_{m}.csv"
            if os.path.exists(fn):
                continue
            a = pd.Timestamp(m.start_time, tz="UTC"); b = pd.Timestamp(m.end_time, tz="UTC").ceil("D")
            for attempt in range(4):
                try:
                    df = cl.query_generation_per_plant(zone, start=a, end=b, psr_type="B18", include_eic=False)
                    break
                except Exception as e:
                    print(f"  {zone} {m}: {e.__class__.__name__} {str(e)[:120]}", flush=True)
                    df = None
                    time.sleep(10 * (attempt + 1))
            if df is None or df.empty:
                pd.DataFrame().to_csv(fn); continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [c[0] for c in df.columns]
            df = df.loc[:, [c for c in df.columns if KEEP.search(str(c))]]
            df = df.tz_convert("UTC").resample("h").mean()
            df.to_csv(fn)
            for c in df.columns:
                parks[c] = max(parks.get(c, 0), float(df[c].max()))
            print(f"  {zone} {m}: {list(df.columns)}", flush=True)
    if parks:
        pd.Series(parks, name="max_mw").rename_axis("park").to_csv(f"{OUT}/parks.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2019-01-01")
    ap.add_argument("--end", default="2025-12-31")
    a = ap.parse_args()
    main(a.start, a.end)
