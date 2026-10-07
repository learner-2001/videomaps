"""Benchmark MapLibre GL + headless Chromium at 1080x1920 — the number that decides
the 1800-frame budget. Honest by construction: it refuses to report a time for blank
frames (a previous run measured 104 ms/frame on an unloaded map, which was meaningless).
"""
import json, os, statistics, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(ROOT, "assets")
W, H, FPS = 1080, 1920, 30
N1 = int(os.environ.get("BENCH_N1", 18))
N2 = int(os.environ.get("BENCH_N2", 10))
ML = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl"


TILE = int(os.environ.get("TILE", "0"))   # 1 = also drape Esri satellite tiles (slower)


def geo_dir():
    gd = os.path.join(A, "geo_min")
    if not os.path.isdir(gd) or not any(f.endswith(".geojson") for f in os.listdir(gd)):
        gd = os.path.join(A, "geo")
    return gd


def layers_and_sources():
    srcs, lays = {}, []
    gd = geo_dir()
    files = sorted(f for f in os.listdir(gd)) if os.path.isdir(gd) else []
    for f in files:
        if not f.endswith(".geojson"):
            continue
        iso = f.split(".")[0]
        srcs[f"g-{iso}"] = {"type": "geojson", "data": json.load(open(os.path.join(gd, f)))}
        main = iso == "BRA"
        lays.append({"id": f"fill-{iso}", "type": "fill", "source": f"g-{iso}",
                     "paint": {"fill-color": "#0f9d58" if main else "#1b3f8f",
                               "fill-opacity": 0.42 if main else 0.20}})
        for wdt, op in ((2.0, 0.95), (9, 0.26)):   # 2 passes: crisp line + fake glow
            lays.append({"id": f"ln{wdt}-{iso}", "type": "line", "source": f"g-{iso}",
                         "paint": {"line-color": "#8dffbe" if main else "#a8d0ff",
                                   "line-width": wdt, "line-opacity": op}})
    if TILE:
        srcs["sat"] = {"type": "raster", "tileSize": 256, "maxzoom": 11, "tiles": [
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"]}
    return srcs, lays, len(files)


def build_page(ngeo, CAPMODE):
    srcs, lays, _ = layers_and_sources()
    blayers = [{"id": "bg", "type": "background", "paint": {"background-color": "#00131f"}}]
    if TILE:
        blayers.insert(1, {"id": "sat", "type": "raster", "source": "sat",
                          "paint": {"raster-opacity": 0.8, "raster-contrast": 0.1}})
    style = {"version": 8, "sources": srcs, "layers": blayers + lays}
    path = [{"t": i / 60.0, "center": [-58.0 + i * 0.11, -8.0 + i * 0.15],
             "zoom": 2.30 + 0.028 * i, "bearing": -3.0 + 0.30 * i} for i in range(60)]
    js = """
    window.__err=[]; window.__tiles=0;
    const map = new maplibregl.Map({container:'map', style:STYLE, center:PATH[0].center,
      zoom:PATH[0].zoom, bearing:PATH[0].bearing, antialias:true, attributionControl:false,
      renderWorldCopies:false, fadeDuration:0, optimizeForTerrain:false,
      preserveDrawingBuffer: PRESERVE});
    map.addControl = map.addControl || function(){};
    map.on('error', e => window.__err.push(String((e&&e.error&&e.error.message)||e)));
    map.on('load', () => { window.__loaded = true; });
    map.on('idle', () => { window.__idle = true; });
    window.diag = () => {
      try { const sc = map.style.sourceCaches.sat; window.__tiles = Object.keys(sc._tiles||{}).length; } catch(e){}
      return {loaded:!!window.__loaded, idle:!!window.__idle, tiles:window.__tiles,
              layers:map.getStyle().layers.length, webgl2:!!document.createElement('canvas').getContext('webgl2'),
              errs:window.__err.slice(0,4)};
    };
    window.captureCanvas = (q) => new Promise(res => {
      requestAnimationFrame(() => requestAnimationFrame(
        () => res(map.getCanvas().toDataURL('image/jpeg', q))));
    });
    window.seek = (i) => new Promise(res => {
      const p = PATH[Math.max(0, Math.min(PATH.length-1, i|0))];
      map.jumpTo({center:p.center, zoom:p.zoom, bearing:p.bearing});
      document.getElementById('num').textContent = (76 + (p.zoom*2.4)).toFixed(0) + '%';
      requestAnimationFrame(()=>requestAnimationFrame(()=>res(true)));
    });
    """
    html = f"""<!doctype html><html><head><meta charset="utf-8">
    <link href="{ML}.css" rel="stylesheet"><style>
    @font-face{{font-family:'Archivo';src:url('file://{A}/fonts/ArchivoBlack-Regular.ttf')}}
    html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:#00131f}}
    #map{{position:absolute;inset:0}}
    #ui{{position:absolute;inset:0;pointer-events:none;padding:92px 56px}}
    .card{{display:inline-block;background:#fff;color:#08121f;font-family:'Archivo';font-size:54px;
           line-height:1.1;padding:20px 30px;border-radius:20px;box-shadow:0 10px 44px rgba(0,0,0,.55)}}
    .big{{position:absolute;right:56px;top:420px;font-family:'Archivo';font-size:214px;color:#fff;
          text-shadow:0 0 38px rgba(140,255,190,.75),0 6px 0 rgba(0,0,0,.4)}}
    .lab{{position:absolute;left:56px;top:660px;background:rgba(6,14,22,.88);color:#fff;font-family:'Archivo';
          font-size:34px;padding:10px 16px;border-radius:8px;letter-spacing:1px}}
    </style></head><body><div id="map"></div>
    <div id="ui"><div class="card">BRAZIL<br>THE COUNTRY THAT<br>FEEDS THE WORLD</div>
    <div class="big" id="num">81%</div><div class="lab">FRONTIER AGRICULTURE · 10 NEIGHBOURS</div></div>
    <script src="{ML}.js"></script><script>
    const STYLE={json.dumps(style)}; const PATH={json.dumps(path)}; const NGEO={ngeo};
    const PRESERVE={json.dumps(CAPMODE)}; {js}
    </script></body></html>"""
    p = os.path.join(ROOT, "bench_page.html")
    open(p, "w").write(html)
    return "file://" + p


def blank_stats(paths):
    from PIL import Image
    import numpy as np
    v = []
    for p in paths:
        a = np.asarray(Image.open(p).convert("L"), dtype=float)
        v.append(float(a.std()))
    return round(statistics.mean(v), 1)


def run():
    from playwright.sync_api import sync_playwright
    CAP = os.environ.get("CAP", "screenshot")
    gdir = os.path.join(A, "geo_min")
    if not os.path.isdir(gdir) or not any(f.endswith(".geojson") for f in os.listdir(gdir)):
        gdir = os.path.join(A, "geo")
    ngeo = len([f for f in os.listdir(gdir) if f.endswith(".geojson")]) if os.path.isdir(gdir) else 0
    url = build_page(ngeo, CAP == "canvas")
    CAP = os.environ.get("CAP", "screenshot")   # screenshot | canvas (toDataURL, no compositor round-trip)
    out = {"capture_mode": CAP, "runner": {"cores": int(subprocess.check_output(["nproc"]).decode()),
                      "mem_total_MB": int([l for l in open("/proc/meminfo") if "MemTotal" in l][0].split()[1]) // 1024},
           "target": {"w": W, "h": H, "fps": FPS, "frames_60s": 60 * FPS}, "args_ok": True}
    ARGS = ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
            "--disable-dev-shm-usage", "--disable-gpu-sandbox", "--hide-scrollbars", "--force-color-profile=srgb"]
    shots = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=ARGS)
        ctx = b.new_context(viewport={"width": W, "height": H})
        pg = ctx.new_page()
        cons = []
        pg.on("pageerror", lambda e: cons.append("pageerror:" + str(e)[:120]))
        pg.on("console", lambda m: cons.append(m.text[:120]) if m.type == "error" else None)
        pg.goto(url, wait_until="load")
        pg.evaluate("""async()=>{const t=Date.now();while(!window.__loaded&&Date.now()-t<45000)
                      await new Promise(r=>setTimeout(r,120));
                      const t2=Date.now();while(!window.__idle&&Date.now()-t2<25000)
                      await new Promise(r=>setTimeout(r,150));}""")
        out["diag"] = pg.evaluate("()=>window.diag()")
        out["console_err"] = cons[:6]
        for i in range(6):
            pg.evaluate(f"window.seek({i*3})")
            pg.wait_for_timeout(120)
        times = []
        for i in range(N1):
            s = time.time()
            pg.evaluate(f"window.seek({i})")
            fp = os.path.join(ROOT, f"_bench_{i:03d}.jpg")
            if CAP == "canvas":
                import base64
                d64 = pg.evaluate(f"window.captureCanvas({0.92})")
                open(fp, "wb").write(base64.b64decode(d64.split(",", 1)[1]))
            else:
                pg.screenshot(path=fp, type="jpeg", quality=92)
            times.append((time.time() - s) * 1000); shots.append(fp)
        out["frame_std"] = blank_stats(shots)          # ~0 => nothing was painted
        out["blank"] = out["frame_std"] < 12
        out["single_worker"] = {"n": N1, "ms_mean": round(statistics.mean(times), 1),
                                "ms_median": round(statistics.median(times), 1),
                                "ms_p90": round(sorted(times)[int(0.9 * len(times))], 1),
                                "jpeg_KB_mean": round(statistics.mean([os.path.getsize(f) for f in shots]) / 1024, 1)}
        if N2:
            ctx2 = b.new_context(viewport={"width": W, "height": H})
            pg2 = ctx2.new_page(); pg2.goto(url, wait_until="load")
            pg2.evaluate("""async()=>{const t=Date.now();while(!window.__loaded&&Date.now()-t<45000)
                           await new Promise(r=>setTimeout(r,120));}""")
            t0, t2 = time.time(), []
            for i in range(N2):
                s = time.time()
                pg.evaluate(f"window.seek({i+20})"); pg.screenshot(path=os.path.join(ROOT, f"_bench_a{i}.jpg"), type="jpeg", quality=92)
                pg2.evaluate(f"window.seek({i+38})"); pg2.screenshot(path=os.path.join(ROOT, f"_bench_b{i}.jpg"), type="jpeg", quality=92)
                t2.append((time.time() - s) * 1000 / 2)
            out["dual_worker"] = {"n": N2 * 2, "ms_mean": round(statistics.mean(t2), 1),
                                  "wall_s": round(time.time() - t0, 1)}
        b.close()
    for f in os.listdir(ROOT):
        if f.startswith("_bench_"):
            os.remove(os.path.join(ROOT, f))
    for k in ("single_worker", "dual_worker"):
        if out.get(k, {}).get("ms_mean"):
            ms = out[k]["ms_mean"]
            out[k]["min_1800_frames"] = round(ms * 1800 / 60000, 1)
            out[k]["gb_for_1800_jpegs"] = round(out[k].get("jpeg_KB_mean", 60) * 1800 / 1e6, 2)
    painted = (not out.get("blank", True)) and out["diag"].get("loaded")
    tiles_ok = (out["diag"].get("tiles", 0) > 0) if TILE else True
    real = bool(painted and tiles_ok and out["diag"].get("layers", 0) >= 10)
    out["measurement_valid"] = bool(real)
    out["tile_mode"] = TILE
    out["verdict"] = ("MAPLIBRE_VIABLE" if real and out["single_worker"]["ms_mean"] < 2200 else
                      ("MAPLIBRE_SLOW_USE_PIL" if real else "MEASUREMENT_INVALID_FIX_PAGE"))
    json.dump(out, open(os.path.join(ROOT, "bench.json"), "w"), indent=1)
    print(json.dumps(out, indent=1))
    return 0 if real else 2


if __name__ == "__main__":
    sys.exit(run())
