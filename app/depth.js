/* Tiefenebene (BSH-Geländemodell, training/depth_layer.py → depth.geojson) – genutzt von Vorhersage und Spotguide.
   WingstDepth.bind(getMap, {key, def}) verbindet #btnDepth und #depthLegend (je Seite eigene Einstellung und Vorgabe),
   WingstDepth.add(map, beforeId) im map.on("load"). */
const WingstDepth = (() => {
  const LAYERS = ["depth-zone", "depth-line", "depth-line3", "depth-rinne"];
  let cfg = { key: "wingst-depth", def: true };
  const isOn = () => { try { const v = localStorage.getItem(cfg.key); return v == null ? cfg.def : v === "1"; } catch (e) { return cfg.def; } };
  function add(map, before) {
    map.addSource("depth", { type: "geojson", data: "depth.geojson",
      attribution: "Tiefen: BSH, Digitales Geländemodell 50 m" });
    map.addLayer({ id: "depth-zone", type: "fill", source: "depth", filter: ["==", ["get", "art"], "zone"],
      paint: { "fill-color": ["match", ["get", "klasse"], "stehtief", "#f2b84b", "#f7e3a1"],
               "fill-opacity": ["match", ["get", "klasse"], "stehtief", 0.6, 0.5] } }, before);
    map.addLayer({ id: "depth-line", type: "line", source: "depth",
      filter: ["all", ["==", ["get", "art"], "linie"], ["==", ["get", "tiefe"], 2]],
      paint: { "line-color": "#2b6c8f", "line-opacity": 0.7, "line-width": 1.1 } }, before);
    map.addLayer({ id: "depth-line3", type: "line", source: "depth",
      filter: ["all", ["==", ["get", "art"], "linie"], ["==", ["get", "tiefe"], 3]],
      paint: { "line-color": "#2b6c8f", "line-opacity": 0.6, "line-width": 0.7, "line-dasharray": [3, 2] } }, before);
    map.addLayer({ id: "depth-rinne", type: "fill", source: "depth", filter: ["==", ["get", "art"], "rinne"],
      paint: { "fill-color": "#0b2a6b", "fill-opacity": 0.75 } }, before);
    set(map, isOn());
  }
  function set(map, on) {
    try { localStorage.setItem(cfg.key, on ? "1" : "0"); } catch (e) {}
    const b = document.getElementById("btnDepth"), lg = document.getElementById("depthLegend");
    if (b) { b.classList.toggle("on", on); b.setAttribute("aria-pressed", on); }
    if (lg) lg.hidden = !on;
    if (map) LAYERS.forEach(id => map.getLayer(id) && map.setLayoutProperty(id, "visibility", on ? "visible" : "none"));
  }
  function bind(getMap, opts) {
    if (opts) cfg = { ...cfg, ...opts };
    const b = document.getElementById("btnDepth");
    if (b) b.addEventListener("click", () => set(getMap(), !isOn()));
    set(getMap(), isOn());
  }
  return { add, set, bind };
})();
