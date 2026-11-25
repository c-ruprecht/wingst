"""Welle an der Ostsee (Prerow, Zingst): Anlauflänge je Windrichtung, Wellenhöhe und -periode, Eignung für
Downwind-Wingen auf der Welle. Ergebnis: app/welle.json (Seite Windanalyse → Welle Ostsee).

Vorgehen
  1. Anlauflänge (Fetch): von einem Punkt ~500 m vor dem Strand Strahlen über das Wasser bis zur nächsten Küste
     (Land-/Wasser-Maske ~1 km, Paket global-land-mask), bis 400 km. Effektiver Fetch nach SPM: Strahlen im Fächer
     ±42° um die Windrichtung, mit cos gewichtet – glättet einzelne Lücken.
  2. Welle: Wind-See nach dem Shore Protection Manual (US Army Corps of Engineers, tiefes Wasser, durch den Fetch
     begrenzt, höchstens voll ausgereift). Ohne Dünung aus anderen Richtungen und ohne Brandung an der Sandbank –
     am Strand bricht die Welle über der Sandbank früher.
  3. Lage zum Strand: Küstennormale aus den Fetch-Strahlen (mittlere Richtung zum offenen Wasser), Winkel zwischen
     Windrichtung und Normale -> auflandig, schräg auflandig, sideshore, schräg ablandig, ablandig.
  4. Downwind: schräg auflandig mit langem Fetch ist ideal (Wind und Welle laufen schräg zum Strand, man fährt
     parallel zum Ufer mit der Welle). Fahrtrichtung entlang der Küste (nach Osten / Westen).
  5. Häufigkeit: an wie vielen Tagen pro Jahr der Wind aus dieser Richtung mindestens 2 h am Stück ab 14 kn bei
     Tageslicht erreicht – Messung Darßer Ort (DWD 2021–heute), mit dem Satellitenfaktor Strand/Darßer Ort je
     Richtung auf den Strand umgerechnet (bei Ostwind misst der Darßer Ort rund 45 % zu wenig).

Aufruf (Repo-Ordner): python training/ostsee_waves.py      (pip install global-land-mask)
"""
import json, math, os, sys
import numpy as np
import pandas as pd
from global_land_mask import globe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "welle.json")
SPOTS = {"prerow": (54.4520, 12.5600), "zingst": (54.4440, 12.6880)}
DIRS16 = ["N", "NNO", "NO", "ONO", "O", "OSO", "SO", "SSO", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
G = 9.81
KN = 0.514444


def move(la, lo, bearing, km):
    return (la + km / 111.2 * math.cos(math.radians(bearing)),
            lo + km / 111.2 * math.sin(math.radians(bearing)) / math.cos(math.radians(la)))


def ray(la, lo, bearing, max_km=400, step=0.5):
    d = step
    while d < max_km:
        if globe.is_land(*move(la, lo, bearing, d)):
            return d
        d += step
    return max_km


def offshore_start(la, lo):
    """Punkt ~500 m vor dem Strand: vom Spot nach Norden gehen, bis Wasser, dann 0,5 km weiter."""
    for d in np.arange(0, 5, 0.1):
        p = move(la, lo, 0, d)
        if not globe.is_land(*p):
            return move(*p, 0, 0.5)
    return la, lo


def wave(u_kn, fetch_km):
    """SPM 1984, tiefes Wasser, Fetch-begrenzt: Höhe Hm0 (m) und Periode Tm (s)."""
    ua = 0.71 * (u_kn * KN) ** 1.23
    f = fetch_km * 1000
    h = 5.112e-4 * ua * f ** 0.5
    t = 6.238e-2 * (ua * f) ** (1 / 3)
    return min(h, 2.482e-2 * ua ** 2), min(t, 0.830 * ua)


def lage(angle):
    a = abs(angle)
    return ("auflandig" if a < 30 else "schräg auflandig" if a < 70 else "sideshore" if a <= 110
            else "schräg ablandig" if a < 150 else "ablandig")


def rating(ufer, fetch, t18):
    if "ablandig" in ufer:
        return 0, "nein – Wind vom Land, keine Welle"
    if ufer == "auflandig":
        return (1, "Welle ja, aber kein Downwind-Kurs: Wind direkt auf den Strand, kabbelig, man muss kreuzen") if fetch >= 20 \
            else (0, "kaum Welle")
    if ufer == "schräg auflandig":
        if fetch >= 40 and t18 >= 4:
            return 3, "sehr gut – Wind und Welle schräg auf den Strand, langer Anlauf, saubere Wellenzüge"
        if fetch >= 15:
            return 2, "gut – schräg auflandig, mittlerer Anlauf, Welle etwas kürzer"
        return 1, "möglich – schräg auflandig, aber kurzer Anlauf: eher Kabbelwasser"
    if fetch >= 40:
        return 2, "gut – sideshore, Welle läuft parallel zum Strand; Abstand zum Ufer halten"
    return 1, "möglich – sideshore, wenig Anlauf: flach bis Kabbelwasser, gut zum Strecke machen"


def sat_factor():
    """Satellitenfaktor Strand / Darßer Ort je 8er-Richtung (app/satellit.json, sat_station_analysis.py)."""
    f = os.path.join(ROOT, "app", "satellit.json")
    if not os.path.exists(f):
        return None
    r = json.load(open(f))["areas"]["Ostsee"]["darss"]["richtung"]
    return {k: v.get("median") for k, v in r.items()}


SEC8 = ["N", "NO", "O", "SO", "S", "SW", "W", "NW"]


def freq_darss(factor=None):
    """Tage pro Jahr je 16er-Richtung mit mind. 2 h am Stück ≥ 14 kn bei Tageslicht – am Darßer Ort gemessen,
    mit factor (je 8er-Richtung) auf den Strand umgerechnet."""
    f = os.path.join(ROOT, "data", "dwd", "darss_ort_wind.csv")
    if not os.path.exists(f):
        return None
    d = pd.read_csv(f, dtype={"MESS_DATUM": str})
    d["time"] = pd.to_datetime(d.MESS_DATUM, format="%Y%m%d%H", utc=True).dt.tz_convert("Europe/Berlin")
    d = d[(d.F >= 0) & (d.D >= 0)]
    d = d[(d.time.dt.hour >= 7) & (d.time.dt.hour <= 19)]
    d["kn"] = d.F / KN * 0.983
    d["s"] = (((d.D + 11.25) % 360) // 22.5).astype(int)
    if factor:
        s8 = (((d.D + 22.5) % 360) // 45).astype(int).map(lambda i: factor.get(SEC8[i]) or 1.0)
        d["kn"] = d.kn * s8
    d["date"] = d.time.dt.date
    days = d.date.nunique()
    hits = {}
    for date, g in d.groupby("date"):
        g = g.sort_values("time")
        run, best = 0, {}
        for kn, s in zip(g.kn, g.s):
            run = run + 1 if kn >= 14 else 0
            if run >= 2:
                best[s] = True
        for s in best:
            hits[s] = hits.get(s, 0) + 1
    return {DIRS16[s]: round(hits.get(s, 0) / days * 365) for s in range(16)}, int(days)


def main():
    fr_raw = freq_darss()
    fac = sat_factor()
    fr = freq_darss(fac) if fac else fr_raw
    out = {"meta": {"methode": "Fetch aus Land-/Wasser-Maske (~1 km), Wind-See nach SPM 1984 (tiefes Wasser)",
                    "tage_darss": fr[1] if fr else None, "strand_faktor": fac}, "spots": {}}
    for spot, (la, lo) in SPOTS.items():
        sla, slo = offshore_start(la, lo)
        rays = {b: ray(sla, slo, b) for b in range(0, 360, 3)}
        # Küstennormale: mittlere Richtung zum offenen Wasser (Strahlen bis 30 km gekappt)
        x = sum(min(v, 30) * math.sin(math.radians(b)) for b, v in rays.items())
        y = sum(min(v, 30) * math.cos(math.radians(b)) for b, v in rays.items())
        normal = (math.degrees(math.atan2(x, y)) + 360) % 360
        rows = []
        for i, name in enumerate(DIRS16):
            b = i * 22.5
            fan = [(o, rays[int(round((b + o) / 3) * 3) % 360]) for o in range(-42, 43, 6)]
            eff = sum(F * math.cos(math.radians(o)) ** 2 for o, F in fan) / sum(math.cos(math.radians(o)) for o, _ in fan)
            ang = (b - normal + 180) % 360 - 180
            ufer = lage(ang)
            h14, t14 = wave(14, eff); h18, t18 = wave(18, eff); h22, t22 = wave(22, eff)
            heading = (b + 180) % 360                       # wohin man downwind fährt
            east = math.cos(math.radians(heading - (normal + 90))) > 0
            score, text = rating(ufer, eff, t18)
            rows.append({"richtung": name, "grad": b, "fetch_km": round(eff), "fetch_gerade_km": round(rays[int(round(b / 3) * 3) % 360]),
                         "winkel": round(ang), "ufer": ufer,
                         "welle": {"14": [round(h14, 2), round(t14, 1)], "18": [round(h18, 2), round(t18, 1)], "22": [round(h22, 2), round(t22, 1)]},
                         "downwind_nach": "Osten" if east else "Westen", "note": score, "text": text,
                         "tage_pro_jahr": fr[0][name] if fr else None,
                         "tage_pro_jahr_darss": fr_raw[0][name] if fr_raw else None})
        out["spots"][spot] = {"normal": round(normal), "start": [round(sla, 4), round(slo, 4)], "richtungen": rows}
        print(f"{spot}: Strand schaut nach {normal:.0f}°")
        for r in rows:
            print(f"  {r['richtung']:>3} {r['fetch_km']:4d} km  {r['ufer']:17s} 18 kn: {r['welle']['18'][0]:.2f} m / {r['welle']['18'][1]:.1f} s"
                  f"  Note {r['note']}  -> {r['downwind_nach']}  {r['tage_pro_jahr']} Tage/Jahr (Darßer Ort {r['tage_pro_jahr_darss']})")
    json.dump(out, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))
    print("->", OUT)


if __name__ == "__main__":
    main()
