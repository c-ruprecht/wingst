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
  depth_layer.py             Tiefenebene der App aus dem BSH-Geländemodell (50 m) -> app/depth.geojson
  depth_map.py               Vergleich Satellit/EMODnet/BSH an bekannten Flächen -> data/depth/vergleich.html
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
python train_speed.py ../data ../data/dwd ../results ../models  # Windstärke in kn (für die Windanimation), vor export_app_models.py
python sat_station_analysis.py   # Satellit im Verhältnis zu Darßer Ort / Barth -> app/satellit.json (Windanalyse → Satellit)
python ostsee_waves.py           # Welle/Downwind je Richtung an Prerow und Zingst -> app/welle.json (pip install global-land-mask)
python sar_grid_factors.py   # Satellitenfaktoren je Gitterpunkt/Richtung für die Windanimation -> app/model/wind_grid.json
python netatmo_wind.py --find --history --check   # private Netatmo-Windmesser am Bodden finden und gegen Barth prüfen
python import_uba_zingst.py <UBA-Lieferung.zip>   # UBA Zingst 2016–25 -> data/uba, Vergleich -> results/uba_zingst_vergleich.csv
curl -L -o ../data/depth/Elevation-Bathymetry.zip https://gdi.bsh.de/de/data/Elevation-Bathymetry.zip
python depth_layer.py                        # Tiefenebene der App -> app/depth.geojson
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

DWD Open Data · Umweltbundesamt, Station Zingst DEUB028 (DL-DE-BY-2.0, Kopie in `reference/`, Format umgestellt) · DMI Open Data (CC BY 4.0) · Open-Meteo (CC BY 4.0, nicht-kommerziell) ·
Copernicus Marine Service (Sentinel-1 L3 Wind) · IOW/BSH MARNET

## Erweiterung November 2026: Thermik, Bodden, Nowcast

- **Neue Merkmale (7-Tage-Modell):** Ersatz für die Bodden-Wassertemperatur (Lufttemperatur Barth, über 5 Tage geglättet), Bodden minus Ostsee, Luft minus Bodden, Thermik-Index (Land-See-Kontrast × Sonne), auflandige Komponente × Stabilität. Wirkung (Kreuzvalidierung 2024↔2025): stündliche AUC +0,005 bis +0,02; Tages-AUC Darßer Ort +0,002 bis +0,012; Barth unverändert (Tagesmodell Barth nutzt die neuen Merkmale nicht).
- **Seewind** (`training/seabreeze.py`): An Sonnentagen mit schwachem Grundwind dreht Darßer Ort mittags von SO auf ONO und legt von 5 auf 8–9 kn zu; ICON bildet das gut ab. In Barth unterschätzt ICON die Thermik am späten Nachmittag um 1–1,5 kn.
- **Nowcast in der App:** Zeile „Jetzt-korr.“ = ICON + aktueller Messfehler an Darßer Ort/Barth, klingt mit τ = 20 h bzw. 3 h ab (aus der Autokorrelation des ICON-Fehlers 2023–2025: MAE Darßer Ort 2,9 → 1,8 kn nach 3 h).
- **Noch herunterladen:** `--only blh` (GFS-Grenzschichthöhe frühere Läufe) und `--only sst_bodden` (Copernicus-Satelliten-Wassertemperatur, zur Prüfung des Bodden-Ersatzes). Das Training nutzt die Grenzschichthöhe automatisch, sobald die Dateien da sind.

## Windanalyse-Seite

`app/analyse.html` (Menüpunkt „Windanalyse“): welches Modell an welchem Vorhersagetag am besten trifft, Treffer/Fehlalarme, Jahreszeiten, Richtungsfehler, Windklima 2016–2025. Zahlen: `python3 training/analysis_page.py results <dwd-ordner> app/analyse.json` (nutzt die kreuzvalidierten Tagesvorhersagen `results/7day_daily_oof_*.csv` aus `train_7day.py`).

## Schwellen 14/18 kn, Spotguide (November 2026)

- Kitebar = 2 h am Stück ab **14 kn**, zweite Stufe **18 kn**, jeweils Wind auf dem Wasser. Für den Bodden wird die Messung Barth mit 1,22 multipliziert (Median Wasser/Station aus 439 Sentinel-1-Bildern, `training/sar_spot_model.py`).
- `app/spotguide.html`: Karte, je Spot Eignung nach Windrichtung (Ufer, Gleichmäßigkeit des Windes aus den Radarbildern, `training/spot_directions.py`), geschätzte Welle aus Fetch und Tiefe, Windklima, Ausstattung live aus OpenStreetMap (Overpass), Kurzbeschreibung mit Quellen (`app/spotinfo.js`).
- Stündliche Tabelle: nur noch das beste Rohmodell (Ostsee ICON, Bodden Mittel der drei), lokale Chance für 14+ und 18+, Jetzt-Korrektur, Böen, Wetter.
