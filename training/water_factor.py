"""Wind auf dem Bodden aus der Messung Barth (an Land) – Faktor je Jahreszeit und Uhrzeit.

Warum nicht einfach x1,22: Sentinel-1 überfliegt nur gegen 7 und 18 Uhr. Genau dann ist der Wind an Land (Barth)
gebremst, mittags durchmischt die Sonne die Luft über Land und Barth misst fast so viel wie der Darßer Ort.
Ein fester Faktor aus den Satellitenzeiten überschätzt den Bodden deshalb mittags, vor allem im Frühjahr.

Ansatz: Wasser zu Wasser ist stabiler als Wasser zu Land. Laut Sentinel-1 ist der Bodden-Wind ein Anteil K
des Winds am Darßer Ort, morgens und abends ähnlich, aber je Jahreszeit verschieden (Frühjahr ~1,0, Winter ~0,88). Der Tagesgang Darßer Ort / Barth kommt aus den DWD-Messungen:
  Faktor(Saison, Stunde) = K x Median(Darßer Ort / Barth | Saison, Stunde)
Ungeprüfte Annahme: der Bodden hat keinen eigenen Tagesgang gegenüber dem Darßer Ort. Prüfen ließe sich das nur mit
einer Messung auf dem Bodden.

Aufruf: python water_factor.py <dwd-ordner> <results-ordner> <app/model>
Ergebnis: <app/model>/water_factor.json  {"k": .., "barth": [[24 Werte] x 4 Saisons: Winter, Frühling, Sommer, Herbst]}
"""
import json, sys
import numpy as np, pandas as pd
from features import load_obs

DWD, RES, OUT = sys.argv[1:4]
BODDEN = ("born", "saal", "pruchten", "dabitz")


def season(m):
    return np.select([np.isin(m, [3, 4, 5]), np.isin(m, [6, 7, 8]), np.isin(m, [9, 10, 11])], [1, 2, 3], 0)


s = pd.read_csv(f"{RES}/sar300_spot_samples.csv", parse_dates=["time"])
s = s[s.spot.isin(BODDEN) & (s.darss >= 6)]
K = (s.sar / s.darss).groupby(season(s.time.dt.tz_convert("Europe/Berlin").dt.month)).median()   # je Saison

x = pd.DataFrame({"d": load_obs(DWD, "darss_ort"), "b": load_obs(DWD, "barth")}).dropna()
x = x[(x.d >= 8) | (x.b >= 8)]
x.index = x.index.tz_convert("Europe/Berlin")
r = (x.d / x.b).groupby([season(x.index.month), x.index.hour]).median().unstack()   # 4 x 24
r = r.T.rolling(3, center=True, min_periods=1).mean().T                          # Stunden glätten
fac = r.mul(K, axis=0).clip(0.8, 1.3).round(3)   # nach oben begrenzt: mehr als 1,3 zeigt Sentinel-1 nie (morgens)
json.dump({"k": K.round(3).tolist(), "note": "Bodden = Barth x Faktor[Saison][Stunde Ortszeit]; Saison 0 Winter, 1 Frühling, 2 Sommer, 3 Herbst",
           "barth": fac.values.tolist()}, open(f"{OUT}/water_factor.json", "w"))
print("K (Bodden / Darßer Ort, Sentinel-1) je Saison:", K.round(3).tolist())
print(fac[[7, 9, 11, 13, 15, 17, 19]].round(2).to_string())
