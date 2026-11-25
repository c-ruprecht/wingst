"""Eis auf dem Bodden in Satellitenbildern erkennen.

Auf Eis wirft das Radar anders zurück als auf Wasser; die Wind-Umrechnung (CMOD5.N) liest dann fast Windstille.
Im Winter 2026 (Jan–Feb) war der Bodden zeitweise zugefroren, die Bilder zeigten dort 2–4 kn bei 7–12 kn in Barth.

Regel: Überflug im Dezember bis März, Barth mindestens 5 kn, und der Bodden-Wind (Median der Bodden-Spots) liegt
unter 75 % von Barth. Auf offenem Wasser (April–November) liegt nur jeder 20. Überflug so tief, im Median ist
der Bodden 1,2-mal Barth. Alle Treffer liegen im Eiswinter Januar–März 2026.
Diese Überflüge zählen für den Bodden nicht. Die Ostsee friert nicht zu und bleibt drin.
"""
import pandas as pd

BODDEN = ("born", "saal", "pruchten", "dabitz")
ICE_MONTHS = (12, 1, 2, 3)


def ice_times(samples):
    """Zeitpunkte (UTC) mit Eis auf dem Bodden aus results/sar300_spot_samples.csv."""
    s = samples[samples.spot.isin(BODDEN) & (samples.barth >= 5) & samples.time.dt.month.isin(ICE_MONTHS)]
    r = (s.sar / s.barth).groupby(s.time).median()
    return set(r[r < 0.75].index)


def drop_ice(samples):
    """Bodden-Zeilen von Eis-Überflügen entfernen; gibt (bereinigt, Anzahl Überflüge mit Eis) zurück."""
    t = ice_times(samples)
    bad = samples.spot.isin(BODDEN) & samples.time.isin(t)
    return samples[~bad].copy(), len(t)
