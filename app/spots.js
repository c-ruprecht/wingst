/* Spots, Messstationen und Richtungsfenster. win = [von, bis] in Grad, aus der der Wind kommen darf (im Uhrzeigersinn). */
const SPOTS = [
  { id: "prerow",     name: "Prerow Strand", short: "Prerow", lat: 54.452, lon: 12.575, water: "Ostsee",            win: [250, 60],  ref: "darss" },
  { id: "zingst",     name: "Zingst Strand", short: "Zingst", lat: 54.445, lon: 12.690, water: "Ostsee",            win: [250, 70],  ref: "darss" },
  { id: "born",       name: "Born", short: "Born",          lat: 54.380, lon: 12.504, water: "Saaler Bodden",     win: [135, 270], ref: "barth" },
  { id: "saal",       name: "Saal", short: "Saal",          lat: 54.318, lon: 12.478, water: "Saaler Bodden",     win: [225, 315], ref: "barth" },
  { id: "fuhlendorf", name: "Fuhlendorf", short: "Fuhlend.",    lat: 54.385, lon: 12.580, water: "Bodstedter Bodden", win: [270, 90],  ref: "barth" },
  { id: "pruchten",   name: "Pruchten", short: "Pruchten",      lat: 54.383, lon: 12.660, water: "Barther Bodden",    win: [270, 100], ref: "barth" }
];

const STATIONS = [
  { id: "darss",   name: "Darßer Ort",   src: "dwd", sid: "06097", lat: 54.4738, lon: 12.5014 },
  { id: "barth",   name: "Barth",        src: "dwd", sid: "00298", lat: 54.3405, lon: 12.7109 },
  { id: "arkona",  name: "Arkona",       src: "dwd", sid: "00183", lat: 54.6791, lon: 13.4344 },
  { id: "vindebk", name: "Vindebæk Kyst (DK)", src: "dmi", sid: "06147", lat: 54.879, lon: 12.1841 },
  { id: "gedser",  name: "Gedser (DK)",  src: "dmi", sid: "06149", lat: 54.5687, lon: 11.9435 }
];

const SILL = { name: "Darßer Schwelle", lat: 54.70, lon: 12.70,
  plot: "https://www.iow.de/files/forschung/mediathek/marnet/images/weekly/ds_ws_en.png",
  plotDir: "https://www.iow.de/files/forschung/mediathek/marnet/images/weekly/ds_wd_en.png",
  page: "https://www.iow.de/marnet-darss-sill.html" };

const THRESHOLDS = [12, 15, 20];
const MIN_RUN = 2;   // Stunden am Stück für „kitebar“
