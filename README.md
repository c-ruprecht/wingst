# wingst – lokale Kite-Windvorhersage Zingst, Darß und Bodden

Korrigiert freie Wettermodelle (ICON, GFS, ECMWF über Open-Meteo) mit lokalen Messungen,
damit die Vorhersage für „Stunden am Stück ab 12 kn / ab 15 kn“ an Ostseestrand und Bodden genauer wird.

## Aufbau

```
environment.yml              Conda-Umgebung (mamba env create -f environment.yml)
training/
  download_training_data.py  Open-Meteo, DWD, DMI, Copernicus Sentinel-1 laden -> data/
  features.py                Merkmale (nur Modellwerte, die live verfügbar sind) und Zielgrößen
  train.py                   XGBoost je Station und Schwelle, Jahr-Auslassung, Weglass-Test, SHAP
  forward.py                 Faktorgruppen einzeln zu ICON hinzufügen (AUC-Gewinn)
  satellite.py               Sentinel-1-Wind: Strand- und Boddenfaktoren relativ zu den Stationen
models/                      trainierte Modelle (XGBoost-JSON) + Merkmalsliste
results/                     Auswertungen als CSV
```

## Ablauf

```
mamba env create -f environment.yml && mamba activate kitewind
copernicusmarine login                       # einmalig, für Sentinel-1
python training/download_training_data.py    # legt data/ an (nicht im Repo)
cd training
python train.py   ../data ../data/dwd ../results
python forward.py ../data ../data/dwd ../results
python satellite.py ../data/sentinel1 ../data/dwd ../results
```

## Stand (Nov 2026)

Trainingszeitraum 2023–2025 (ICON-Archiv vollständig ab Ende 2022), ausgewertet nur bei Tageslicht,
jedes Jahr einmal ausgelassen und getestet.

| Station | Schwelle | AUC ICON roh | AUC lokal |
|---|---|---|---|
| Darßer Ort | ≥ 12 kn | 0,923 | 0,985 |
| Darßer Ort | ≥ 15 kn | 0,945 | 0,989 |
| Barth | ≥ 12 kn | 0,965 | 0,980 |
| Barth | ≥ 15 kn | 0,976 | 0,987 |

- Fehler „Stunden am Stück ≥ 12 kn“ pro Tag: Darßer Ort 1,9 h → 0,7 h, Barth 0,9 h → 0,75 h.
- Hauptgewinn ist die Nachkalibrierung von ICON selbst; Sonne/Tageszeit und andere Modelle bringen wenig,
  Wassertemperatur und Luftdichte bei kurzer Vorlaufzeit nichts Messbares.
- Sentinel-1: Strand vor Prerow/Zingst ≈ 3–5 % weniger Wind als vor Darßer Ort, bei S–SW 10–13 % weniger.
  Über der Boddenkette ≈ 1,34–1,45 × der Messung Barth (Flugplatz, geschützt).

## Offene Punkte

- Auswertung nach Vorlaufzeit (Tag 1–7) mit Open-Meteo „Previous Runs“
- Höher aufgelöster SAR-Wind (eigene Verarbeitung, ~300 m) für den Barther Bodden
- PWA unter `app/`: lädt Open-Meteo, DWD (Bright Sky) und DMI im Browser und rechnet das Modell lokal

## Datenquellen

DWD Open Data · DMI Open Data (CC BY 4.0) · Open-Meteo (CC BY 4.0, nicht-kommerziell) ·
Copernicus Marine Service (Sentinel-1 L3 Wind) · IOW/BSH MARNET
