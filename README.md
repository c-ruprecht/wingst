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
  train_7day.py              7-Tage-Modell aus früheren Modellläufen (Vorlaufzeit 1–7 Tage):
                             stündliche Wahrscheinlichkeit + Tageswahrscheinlichkeit „kitebar“
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
python train_7day.py ../data ../data/dwd ../results ../models   # braucht data/openmeteo_prev
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

## 7-Tage-Modell (frühere Modellläufe 2024–2025, auf einem Jahr trainiert, am anderen getestet)

Tagesentscheidung „kitebar“ = mind. 2 h am Stück ab Schwelle bei Tageslicht. AUC DWD (ICON, Tag 7: GFS) → lokal:

| Vorlauf | Darßer Ort 12 kn | Darßer Ort 15 kn | Barth 12 kn | Barth 15 kn |
|---|---|---|---|---|
| 1 Tag | 0,88 → 0,94 | 0,91 → 0,94 | 0,93 → 0,94 | 0,96 → 0,95 |
| 3 Tage | 0,85 → 0,90 | 0,87 → 0,91 | 0,90 → 0,88 | 0,92 → 0,91 |
| 5 Tage | 0,77 → 0,83 | 0,77 → 0,83 | 0,78 → 0,78 | 0,81 → 0,80 |
| 7 Tage | 0,67 → 0,70 | 0,69 → 0,72 | 0,66 → 0,66 | 0,70 → 0,65 |

Details: `results/7day_daily_skill.csv`, `results/7day_hourly_auc.csv`, `results/7day_importance.csv`.

## Offene Punkte

- 7-Tage-Modell in die App einbauen (Merkmale im Browser berechnen, XGBoost-Bäume in JavaScript auswerten)
- Höher aufgelöster SAR-Wind (eigene Verarbeitung, ~300 m) für den Barther Bodden
- PWA unter `app/`: lädt Open-Meteo, DWD (Bright Sky) und DMI im Browser und rechnet das Modell lokal

## Datenquellen

DWD Open Data · DMI Open Data (CC BY 4.0) · Open-Meteo (CC BY 4.0, nicht-kommerziell) ·
Copernicus Marine Service (Sentinel-1 L3 Wind) · IOW/BSH MARNET

## Erweiterung November 2026: Thermik, Bodden, Nowcast

- **Neue Merkmale (7-Tage-Modell):** Ersatz für die Bodden-Wassertemperatur (Lufttemperatur Barth, über 5 Tage geglättet), Bodden minus Ostsee, Luft minus Bodden, Thermik-Index (Land-See-Kontrast × Sonne), auflandige Komponente × Stabilität. Wirkung (Kreuzvalidierung 2024↔2025): stündliche AUC +0,005 bis +0,02; Tages-AUC Darßer Ort +0,002 bis +0,012; Barth unverändert (Tagesmodell Barth nutzt die neuen Merkmale nicht).
- **Seewind** (`training/seabreeze.py`): An Sonnentagen mit schwachem Grundwind dreht Darßer Ort mittags von SO auf ONO und legt von 5 auf 8–9 kn zu; ICON bildet das gut ab. In Barth unterschätzt ICON die Thermik am späten Nachmittag um 1–1,5 kn.
- **Nowcast in der App:** Zeile „Jetzt-korr.“ = ICON + aktueller Messfehler an Darßer Ort/Barth, klingt mit τ = 20 h bzw. 3 h ab (aus der Autokorrelation des ICON-Fehlers 2023–2025: MAE Darßer Ort 2,9 → 1,8 kn nach 3 h).
- **Noch herunterladen:** `--only blh` (GFS-Grenzschichthöhe frühere Läufe) und `--only sst_bodden` (Copernicus-Satelliten-Wassertemperatur, zur Prüfung des Bodden-Ersatzes). Das Training nutzt die Grenzschichthöhe automatisch, sobald die Dateien da sind.
