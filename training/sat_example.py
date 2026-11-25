"""Beispielbilder für die Windanalyse (Satellit): ein Überflug bei Ostwind und einer bei Westwind, mit den Messungen
an Darßer Ort und Barth und dem Satellitenwind an den Spots – zeigt, wie aus „gemessen × Satellitenfaktor“ der Wind
am Spot wird (Jetzt-korr.).

Hintergrund
  - mit eigenen Bildern (data/sar300/wind/*.npz aus sar_highres.py): das echte Windfeld des Überflugs, 300 m
  - ohne: nur die Wasserflächen (aus data/sar300/water_mask.npy oder notfalls aus results/sar300_mean_ratio.png)
Werte an Spots und Stationen: results/sar300_spot_samples.csv

Ausgabe: app/img/sat_beispiel_ost.png, app/img/sat_beispiel_west.png, app/sat_beispiele.json

Aufruf (Repo-Ordner): python training/sat_example.py [--ost JJJJ-MM-TTTHH] [--west JJJJ-MM-TTTHH]
  Ohne Angabe sucht das Skript selbst aus: alle sechs Spots im Bild, ≥ 90 % der Wasserfläche abgedeckt, kein Eis;
  Ost: Ostsee und Bodden etwa gleich stark, West: typisches Verhältnis Bodden/Barth.
"""
import argparse, glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAR = os.path.join(ROOT, "data", "sar300")
BBOX = (12.30, 54.28, 12.90, 54.52)
H, W = 89, 130                                      # Gitter von sar_highres.py (300 m)
SPOTS = {"prerow": ("Prerow", 54.455506, 12.555672), "zingst": ("Zingst", 54.442567, 12.707110),
         "born": ("Born", 54.381079, 12.504912), "saal": ("Saal", 54.312080, 12.480294),
         "pruchten": ("Pruchten", 54.386068, 12.659851), "dabitz": ("Dabitz", 54.362277, 12.805830)}
STATIONS = {"darss": ("Darßer Ort", 54.4738, 12.5014), "barth": ("Barth", 54.3405, 12.7109)}
OST = ("prerow", "zingst")
# Farben wie die Windanimation: Blau (wenig) -> Gelb bei 14 kn -> Rot
STOPS = [(0, (59, 111, 209)), (6, (47, 163, 224)), (10, (54, 195, 192)), (14, (242, 211, 59)),
         (18, (245, 130, 44)), (22, (224, 54, 42)), (28, (163, 22, 95))]
CMAP = LinearSegmentedColormap.from_list("wingst", [(v / 28, tuple(c / 255 for c in rgb)) for v, rgb in STOPS])


def water_mask():
    f = os.path.join(SAR, "water_mask.npy")
    if os.path.exists(f):
        return np.load(f).astype(bool), "Wassermaske 300 m"
    # Notlösung: aus der gespeicherten Mittelwertkarte ablesen (farbige Zellen = Wasser)
    from PIL import Image
    im = np.array(Image.open(os.path.join(ROOT, "results", "sar300_mean_ratio.png")).convert("RGB")).astype(int)
    top, bottom, left, right = 50, 568, 89, 925           # Achsenrahmen dieser Abbildung
    m = np.zeros((H, W), bool)
    for i in range(H):
        for j in range(W):
            y = int(top + (i + 0.5) / H * (bottom - top)); x = int(left + (j + 0.5) / W * (right - left))
            r, g, b = im[y, x]
            m[i, j] = not (r > 235 and g > 235 and b > 235)
    return m, "Wasserflächen aus der Satelliten-Mittelwertkarte"


def field_for(t):
    """Windfeld (kn) des Überflugs aus data/sar300/wind/*.npz, falls vorhanden."""
    for f in glob.glob(os.path.join(SAR, "wind", "*.npz")):
        z = np.load(f)
        tz = pd.Timestamp(str(z["time"]))
        tz = tz.tz_localize("UTC") if tz.tzinfo is None else tz.tz_convert("UTC")
        if abs((tz - t).total_seconds()) < 3600:
            return z["v"] * 1.943844
    return None


def coverage(t, mask):
    """Anteil der Wasserpixel mit Wind im Bild (None, wenn das Einzelbild lokal nicht vorliegt)."""
    f = field_for(t)
    if f is None:
        return None
    return float(np.isfinite(f[mask]).mean())


def pick(S, kind, mask):
    """Deutlicher, vollständiger, eisfreier Fall.
    Ost: Ostsee und Bodden etwa gleich stark (zeigt, dass der Darßer Ort bei Ost zu wenig misst, nicht die Spots).
    West: Bodden-Verhältnis zu Barth nahe am Mittel dieser Richtung."""
    from sar_ice import ice_times
    w = S.pivot(index="time", columns="spot", values="sar")
    m = S.drop_duplicates("time").set_index("time")[["darss", "barth", "dir"]]
    J = w.join(m).dropna(subset=list(SPOTS))                 # alle sechs Spots im Bild
    J = J[~J.index.isin(ice_times(S)) & J.index.month.isin(range(4, 12))]   # Beispiel nur aus der eisfreien Zeit
    ost = J[list(OST)].mean(axis=1); bod = J[["born", "saal", "pruchten", "dabitz"]].mean(axis=1)
    if kind == "ost":
        x = J[(J.dir > 60) & (J.dir < 120) & (J.darss >= 10) & (J.barth >= 6) & (bod >= 12)]
        score = (ost[x.index] / bod[x.index] - 1).abs()
    else:
        x = J[(J.dir > 235) & (J.dir < 295) & (J.darss >= 16) & (J.barth >= 10)]
        r = bod[x.index] / x.barth
        score = (r - r.median()).abs()
    for t in score.sort_values().index:                      # bestes Bild, das die Fläche fast ganz abdeckt
        c = coverage(t, mask)
        if c is None or c >= 0.9:
            return t
    return None


def compass(d):
    return ["N", "NO", "O", "SO", "S", "SW", "W", "NW"][int(((d + 22.5) % 360) // 45)]


def draw(S, t, kind, mask, mask_src):
    g = S[S.time == t]
    row = g.iloc[0]
    darss, barth, wdir = float(row.darss), float(row.barth), float(row.dir)
    fld = field_for(t)
    fig, ax = plt.subplots(figsize=(9.2, 4.6), dpi=130)
    ext = (BBOX[0], BBOX[2], BBOX[1], BBOX[3])
    land = np.where(mask, np.nan, 1.0)
    ax.imshow(land, extent=ext, cmap=LinearSegmentedColormap.from_list("l", ["#e9e4d8", "#e9e4d8"]), vmin=0, vmax=1, aspect="auto")
    if fld is not None:
        im = ax.imshow(np.where(mask, fld, np.nan), extent=ext, cmap=CMAP, vmin=0, vmax=28, aspect="auto", interpolation="nearest")
        cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01); cb.set_label("Wind auf dem Wasser (kn)")
    else:
        ax.imshow(np.where(mask, 1.0, np.nan), extent=ext, cmap=LinearSegmentedColormap.from_list("w", ["#cfe6f3", "#cfe6f3"]), vmin=0, vmax=1, aspect="auto")
    ax.set_xlim(BBOX[0], BBOX[2]); ax.set_ylim(BBOX[1], BBOX[3]); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    spots = {}
    for key, (name, la, lo) in SPOTS.items():
        r = g[g.spot == key]
        if r.empty or not np.isfinite(r.sar.iloc[0]):
            continue
        kn = float(r.sar.iloc[0]); ref = darss if key in OST else barth
        spots[key] = {"name": name, "kn": round(kn, 1), "station": "darss" if key in OST else "barth", "faktor": round(kn / ref, 2)}
        c = CMAP(min(kn, 28) / 28)
        ax.scatter(lo, la, s=170, color=c, edgecolor="#15232a", linewidth=1.2, zorder=4)
        off = {"prerow": (10, -30), "zingst": (8, 6)}.get(key, (8, 6))
        ax.annotate(f"{name}\n{kn:.0f} kn · ×{kn / ref:.2f}".replace(".", ","), (lo, la), xytext=off, textcoords="offset points",
                    fontsize=8.5, fontweight="bold", color="#15232a", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.85))
    for key, (name, la, lo) in STATIONS.items():
        kn = darss if key == "darss" else barth
        ax.scatter(lo, la, s=150, marker="s", color=CMAP(min(kn, 28) / 28), edgecolor="#15232a", linewidth=1.6, zorder=4)
        off, ha = ((-10, -6), "right") if key == "darss" else ((8, -22), "left")
        ax.annotate(f"{name} gemessen\n{kn:.0f} kn".replace(".", ","), (lo, la), xytext=off, textcoords="offset points", ha=ha,
                    fontsize=8.5, color="#15232a", zorder=5, bbox=dict(boxstyle="round,pad=0.25", fc="#fff6d6", ec="#15232a", lw=0.6))
    # Windpfeil (weht von wdir)
    a = np.radians(wdir); x0, y0 = 12.86, 54.30
    ax.annotate("", xy=(x0 - 0.035 * np.sin(a), y0 - 0.02 * np.cos(a)), xytext=(x0 + 0.035 * np.sin(a), y0 + 0.02 * np.cos(a)),
                arrowprops=dict(arrowstyle="-|>", lw=2.2, color="#15232a"), zorder=6)
    ax.text(x0, y0 - 0.035, f"Wind aus {compass(wdir)}", ha="center", fontsize=8.5, color="#15232a")
    loc = t.tz_convert("Europe/Berlin")
    ax.set_title(f"Sentinel-1, {loc:%d.%m.%Y %H:%M} Uhr – Wind aus {compass(wdir)} ({wdir:.0f}°)", fontsize=11, loc="left")
    fig.tight_layout()
    os.makedirs(os.path.join(ROOT, "app", "img"), exist_ok=True)
    out = os.path.join(ROOT, "app", "img", f"sat_beispiel_{kind}.png")
    fig.savefig(out, dpi=130); plt.close(fig)
    print("->", out, "(echtes Windfeld)" if fld is not None else f"(ohne Windfeld, {mask_src})")
    return {"id": kind, "bild": f"img/sat_beispiel_{kind}.png", "zeit": f"{loc:%Y-%m-%d %H:%M}", "richtung": compass(wdir), "grad": round(wdir),
            "darss": round(darss, 1), "barth": round(barth, 1), "spots": spots, "windfeld": fld is not None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ost"); ap.add_argument("--west")
    a = ap.parse_args()
    S = pd.read_csv(os.path.join(ROOT, "results", "sar300_spot_samples.csv"), parse_dates=["time"]).drop_duplicates(["time", "spot"])
    mask, src = water_mask()
    out = []
    for kind in ("ost", "west"):
        want = getattr(a, kind)
        t = pd.Timestamp(want, tz="UTC") if want else pick(S, kind, mask)
        if t is None:
            print("kein passender Überflug für", kind); continue
        out.append(draw(S, t, kind, mask, src))
    json.dump(out, open(os.path.join(ROOT, "app", "sat_beispiele.json"), "w"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
