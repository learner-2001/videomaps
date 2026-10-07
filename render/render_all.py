"""Renderer: MapLibre 2D top-down + baked georeferenced overlays + DOM typography,
timed off the VO.  Parallel workers, per-chunk encode, frames deleted as we go, resumable.

  python render/render_all.py --preview 3.5,16,45,58.5      # 4 full-quality stills, then stop
  python render/render_all.py                                # all frames -> out/brazil.mp4
"""
import argparse, json, math, os, shutil, subprocess, sys, time
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A, OUT = os.path.join(ROOT, "assets"), os.path.join(ROOT, "out")
OVER = os.path.join(OUT, "over")
TL = json.load(open(os.path.join(ROOT, "audio", "timeline.json")))
W, H, FPS, TOTAL = TL["w"], TL["h"], TL["fps"], TL["total"]
NFR = TL["frames"]
ML = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl"
JQ = int(os.environ.get("JQ", "93"))
CAST = ["BRA", "ARG", "BOL", "COL", "GUY", "PRY", "PER", "SUR", "URY", "VEN", "FRA", "CHL", "ECU"]
CREDITS = ("Boundaries: geoBoundaries (CC-BY 4.0) · Natural Earth · Flags: flagcdn (MIT) · "
           "Photography: Wikimedia Commons (CC) · Figures: World Bank / USDA WASDE")
ARGS = ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader",
        "--disable-dev-shm-usage", "--disable-gpu-sandbox", "--hide-scrollbars",
        "--force-color-profile=srgb", "--js-flags=--expose-gc"]


def serve(root_dir):
    """MapLibre image sources are fetched with XHR, which Chromium blocks on file:// pages.
    A localhost static server is the smallest fix that keeps everything else identical."""
    import http.server, socketserver, functools, threading
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root_dir)
    class Q(socketserver.ThreadingTCPServer):
        allow_reuse_address, daemon_threads = True, True
    srv = Q(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def ff():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return shutil.which("ffmpeg")


def geo(iso):
    for d in ("geo_min", "geo"):
        p = os.path.join(A, d, f"{iso}.geojson")
        if os.path.exists(p):
            return json.load(open(p))
    return None


def bounds_of(geoms):
    xs, ys = [], []

    def walk(c):
        if isinstance(c[0], (int, float)):
            xs.append(c[0]); ys.append(c[1])
        else:
            for k in c:
                walk(k)
    for g in geoms:
        walk(g["coordinates"])
    return min(xs), min(ys), max(xs), max(ys)


def merc_y(lat):
    r = math.radians(max(-84.9, min(84.9, lat)))
    return (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2


def cam_for(box, pad=1.30):
    lon1, lat1, lon2, lat2 = box
    xe = max((lon2 - lon1) / 360.0, 1e-6)
    ye = max(abs(merc_y(lat2) - merc_y(lat1)), 1e-6)
    z = math.log2(min(W / (256 * xe), H / (256 * ye)) / pad)
    return [(lon1 + lon2) / 2, (lat1 + lat2) / 2], round(min(max(z, 0.4), 11.5), 3)


TITLES = [
    ("TWELVE NATIONS,<br>ONE HALFWAY CONTINENT", "South America · 2025 GDP share", "49.9", "%", "OF THE CONTINENT'S ECONOMY IS BRAZIL"),
    ("THE FIFTH<br>LARGEST ON EARTH", "Land area · World Bank", "8.36", "M km²", "after Russia, Canada, the USA and China"),
    ("TEN COUNTRIES<br>TOUCH BRAZIL", "Derived from border geometry", "10", "", "every South American state except Chile and Ecuador — plus France, by way of Guyane"),
    ("FOUR IN TEN<br>THINGS IT SELLS", "Food, % of merchandise exports · World Bank 2025", "40.7", "%", "is food"),
    ("THE WORLD'S<br>KITCHEN SHELF", "Share of global exports", "58", "%", "of soybeans · 76% of orange juice · 44% of sugar"),
    ("SMALL FOOTPRINT,<br>HUGE HARVEST", "Arable land, % of territory · World Bank", "6.7", "%", "India farms on 51.8% · France on 31.4%"),
    ("STILL<br>MOSTLY WILD", "Forest cover · FAO / World Bank 2023", "59.0", "%", "and the Amazon outdrains every other river"),
    ("212 MILLION<br>PEOPLE", "Population / urban share · World Bank 2025", "88.2", "%", "live in cities"),
    ("BRAZIL", None, None, None, "It doesn't just occupy a continent. It supplies one."),
]


def scenes():
    g = {i: geo(i) for i in CAST}
    br = bounds_of([g["BRA"]["features"][0]["geometry"]])
    sa = bounds_of([g[i]["features"][0]["geometry"] for i in CAST if g.get(i)])
    per = {}
    for ln, (title, note, big, unit, sub) in zip(TL["lines"], TITLES):
        box = sa if ln["i"] in (1, 3, 9) else br
        if ln["i"] in (5, 6):
            box = [br[0], br[1] + 4, br[2] - 2, br[3]]
        if ln["i"] == 7:
            box = [br[0], br[3] - 12, br[2], br[3] + 2]           # hold on the Amazon basin
        c, z = cam_for(box)
        per[ln["i"]] = dict(i=ln["i"], start=ln["start"], end=ln["end"], text=ln["text"],
                            c=c, z=z, title=(title or "").replace("<br>", "\n"), note=note,
                            big=big, unit=unit, sub=sub,
                            count=big is not None and ln.get("reveal") is not None)
    out = [per[ln["i"]] for ln in TL["lines"]]
    for k, s in enumerate(out):
        s["z0"], s["z1"] = s["z"] - 0.20, s["z"] + 0.14
        s["c0"] = [s["c"][0] - 0.7, s["c"][1] + 0.5]
        s["c1"] = [s["c"][0] + 0.7, s["c"][1] - 0.5]
        s["bear0"], s["bear1"] = round(-4.0 + k * 1.1, 2), round(-1.0 + k * 1.1, 2)
        del s["c"], s["z"]
    return out


def style_and_layers():
    srcs, lays = {}, [{"id": "bg", "type": "background", "paint": {"background-color": "#04121c"}}]
    geoms = {}
    for iso in CAST:
        g = geo(iso)
        if not g:
            continue
        geoms[iso] = g
        srcs[f"g-{iso}"] = {"type": "geojson", "data": g}
    for iso in CAST:
        if iso not in geoms:
            continue
        main = iso == "BRA"
        lays.append({"id": f"base-{iso}", "type": "fill", "source": f"g-{iso}",
                     "paint": {"fill-color": "#0b3323" if main else "#07182a", "fill-opacity": 0.95}})
    for iso in CAST:
        if iso not in geoms:
            continue
        for wdt, op in ((1.3, 0.92), (7, 0.16)):   # 2 glow passes, not 3
            lays.append({"id": f"gl{wdt}-{iso}", "type": "line", "source": f"g-{iso}",
                         "paint": {"line-color": "#7dffb9" if iso == "BRA" else "#5aa2ff",
                                   "line-width": wdt, "line-opacity": op}})
    laymap = {}
    ip = os.path.join(OVER, "index.json")
    if os.path.exists(ip):
        idx = json.load(open(ip))
        for sid, items in idx["scenes"].items():
            seen = set()
            for it in items:
                if it["file"] in seen:
                    continue
                seen.add(it["file"])
                lid = f"ov-{sid}-{it['iso']}-{it['kind'].replace('_', '-')}"
                srcs[lid] = {"type": "image", "url": "over/" + it["file"], "coordinates": it["coords"]}
                op = 0.84 if it["kind"].startswith("photo") else 0.88
                lays.append({"id": lid, "type": "raster", "source": lid,
                             "paint": {"raster-opacity": 0.0, "raster-fade-duration": 0,
                                       "raster-resampling": "linear",
                                       "raster-opacity-transition": {"duration": 0, "delay": 0}}})
                laymap.setdefault(str(sid), []).append([lid, op])
    return {"version": 8, "sources": srcs, "layers": lays}, laymap


JS = r"""
const C = window.__CFG, SCN = C.scenes, LAY = C.layers, TOTAL = C.total;
const ease = t => t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3)/2;
const clamp = (v,a,b) => Math.max(a, Math.min(b, v));
const errs = [];
const map = new maplibregl.Map({container:'map', style:C.style, antialias:true, attributionControl:false,
  renderWorldCopies:false, fadeDuration:0, optimizeForTerrain:false, interactive:false});
map.on('error', e => errs.push(((e && e.error && (e.error.message || e.error.text)) || (e && e.message) || JSON.stringify(e)).slice(0,160)));
map.on('load', () => { window.__loaded = true; });
window.diag = () => ({loaded: !!window.__loaded, errs: errs.slice(0,3),
                      layers: map.getStyle().layers.length, srcs: Object.keys(map.getStyle().sources).length});
const _fitCache = new Map();
function fit(el, txt, maxpx, minpx){
  txt = txt || '';
  const key = el.id;
  if (_fitCache.get(key) === txt) return;          // same string -> same layout: skip all measuring
  _fitCache.set(key, txt);
  el.textContent = txt;
  if (!txt) { el.style.display='none'; return; }
  el.style.display=''; el.style.fontSize = maxpx + 'px';
  let g = 0;
  while ((el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1)
         && parseFloat(el.style.fontSize) > minpx && g++ < 14)
    el.style.fontSize = (parseFloat(el.style.fontSize) - 4) + 'px';
}
function numStr(s, k){
  if (s.big === null || s.big === undefined) return '';
  if (!s.count) return s.big + (s.unit ? '\u2009' + s.unit : '');
  const v = parseFloat(s.big) * k;
  const dec = parseFloat(s.big) % 1 ? 1 : 0;
  const num = v.toFixed(dec).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const unit = (s.unit || '').trim();
  return unit === '%' ? num + '%' : num + (unit.length <= 6 ? '\u2009' + unit : '');
}
window.seek = (t) => new Promise(res => {
  let i = 0; for (let j = SCN.length - 1; j >= 0; j--) if (t >= SCN[j].start) { i = j; break; }
  const s = SCN[i], span = Math.max(0.001, s.end - s.start);
  const k = clamp((t - s.start) / span, 0, 1);
  const kk = ease(clamp((k - 0.06) / 0.88, 0, 1));
  map.jumpTo({center:[s.c0[0] + (s.c1[0]-s.c0[0])*kk, s.c0[1] + (s.c1[1]-s.c0[1])*kk],
              zoom: s.z0 + (s.z1 - s.z0)*kk, bearing: s.bear0 + (s.bear1 - s.bear0)*kk, pitch: 0});
  const prev = i > 0 ? SCN[i-1].i : -1;
  for (const sid in LAY) for (const [lid, op] of LAY[sid]) {
    if (!map.getLayer(lid)) continue;
    let a = 0;
    if (sid === String(s.i)) a = op * clamp((k - 0.04) / 0.20, 0, 1);
    else if (sid === String(prev)) a = op * (1 - clamp(k / 0.20, 0, 1));            // crossfade out
    else if (i === SCN.length - 1) a = op * 0.55 * clamp((k - 0.10) / 0.30, 0, 1);  // finale: everything back on
    map.setPaintProperty(lid, 'raster-opacity', a);
  }
  const local = t - s.start, rev = clamp((local - 0.30) / 0.95, 0, 1);
  document.getElementById('tag').textContent = ['GEOGRAPHY','SIZE','BORDERS','TRADE','AGRICULTURE',
                                                'LAND USE','NATURE','PEOPLE','BRAZIL'][i] || '';
  fit(document.getElementById('card'), s.title, 58, 30);
  const numEl = document.getElementById('num'), nv = numStr(s, rev);
  if (numEl.textContent !== nv) numEl.textContent = nv;
  fit(document.getElementById('sub'), local > 0.34 ? (s.note || '') : '', 40, 22);
  fit(document.getElementById('kick'), local > 0.5 ? (s.sub || '') : '', 30, 16);
  fit(document.getElementById('cap'), s.text, 36, 20);
  document.getElementById('bar').style.width = (100 * t / TOTAL).toFixed(2) + '%';
  document.body.style.setProperty('--fade', i === SCN.length - 1
        ? String(1 - clamp((k - 0.80) / 0.20, 0, 1) * 0.75) : '1');
  requestAnimationFrame(() => requestAnimationFrame(() => res(i)));
});
"""

HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="{ML}.css" rel="stylesheet"><style>{css}</style></head><body>
<div id="map"></div><div id="vig"></div>
<div id="ui">
  <div class="safe" id="head"><div id="tag"></div><div id="card"></div></div>
  <div class="safe" id="mid"><div id="num"></div><div id="sub"></div><div id="kick"></div></div>
  <div class="safe" id="foot"><div id="cap"></div><div id="note"></div>
    <div id="barw"><div id="bar"></div></div><div id="cred">{cred}</div></div>
</div>
<script src="{ML}.js"></script>
<script>window.__CFG={cfg};</script>
<script>{js}</script></body></html>"""


def build_page(base=""):
    style, laymap = style_and_layers()
    css = open(os.path.join(ROOT, "render", "page.css")).read()
    css = (css.replace("VWpx", f"{W}px").replace("VHpx", f"{H}px")
              .replace("FONTARCH", f"{base}assets/fonts/ArchivoBlack-Regular.ttf")
              .replace("FONTBARLOW", f"{base}assets/fonts/BarlowCondensed-Black.ttf")
              .replace("FONTJET", f"{base}assets/fonts/JetBrainsMono.ttf"))
    cfg = {"style": style, "layers": laymap, "scenes": scenes(), "total": TOTAL, "fps": FPS,
           "w": W, "h": H}
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "index.html")
    open(p, "w").write(HTML.format(ML=ML, css=css, js=JS, cfg=json.dumps(cfg), cred=CREDITS))
    return p


def new_page(br, url, dsf=None):
    dsf = dsf if dsf is not None else float(os.environ.get("DSF", "1"))
    ctx = br.new_context(viewport={"width": W, "height": H}, device_scale_factor=dsf)
    pg = ctx.new_page()
    err = []
    pg.on("pageerror", lambda e: err.append(str(e)[:160]))
    pg.goto(url, wait_until="load")
    pg.evaluate("""async()=>{const t=Date.now();while(!window.__loaded&&Date.now()-t<60000)
                   await new Promise(r=>setTimeout(r,150));}""")
    return ctx, pg, err


def shot(pg, t, path):
    pg.evaluate(f"window.seek({t})")
    pg.screenshot(path=path, type="jpeg", quality=int(os.environ.get("JQ", "93")))


def preview(times):
    from playwright.sync_api import sync_playwright
    srv, base = serve(ROOT)
    url = base + "/out/index.html"
    build_page(base=base + "/")
    d = os.path.join(OUT, "preview")
    os.makedirs(d, exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch(args=ARGS)
        ctx, pg, err = new_page(br, url)
        print("[preview] diag:", json.dumps(pg.evaluate("()=>window.diag()")))
        t0 = time.time()
        for t in times:
            f = os.path.join(d, f"t{t:06.2f}.jpg")
            shot(pg, t, f)
            from PIL import Image as _I
            im = np.asarray(_I.open(f).convert("L"))
            print(f"  t={t:6.2f}s -> {os.path.basename(f)} {os.path.getsize(f)/1024:6.1f}KB  std={im.std():.1f}")
        # timing pass: steady-state ms/frame with the real style in memory
        ts=[]
        for n in range(12):
            s0=time.time(); pg.evaluate(f"window.seek({3+n*0.9})")
            pg.screenshot(path="/tmp/_t.jpg", type="jpeg", quality=JQ); ts.append((time.time()-s0)*1000)
        print(f"[preview] {len(times)} frames in {time.time()-t0:.1f}s | steady {np.mean(ts):.0f}ms/frame "
              f"| est {np.mean(ts)*NFR/60000:.1f} min for {NFR} on 1 worker")
        print("[preview] map errors:", json.dumps(pg.evaluate("()=>window.diag()")["errs"])[:400])
        br.close(); srv.shutdown()


def calibrate(url):
    """Render 14 frames; if a frame costs more than CAL_MAX ms, drop to 720x1280 with a
    Lanczos upscale at encode time. A finished 1080p-looking video beats a cancelled run."""
    from playwright.sync_api import sync_playwright
    t0 = time.time()
    with sync_playwright() as p:
        br = p.chromium.launch(args=ARGS)
        ctx, pg, err = new_page(br, url, dsf=float(os.environ.get("DSF", "1")))
        ts = []
        for n in range(14):
            s0 = time.time()
            shot(pg, 2.0 + n * 1.3, "/tmp/_cal.jpg")
            ts.append((time.time() - s0) * 1000)
        diag = pg.evaluate("()=>window.diag()")
        br.close()
    warm = float(np.mean(ts[:4])); steady = float(np.mean(ts[5:]))
    print(f"[calib] mean {np.mean(ts):.0f}ms  first4 {warm:.0f}ms  steady {steady:.0f}ms  "
          f"({time.time()-t0:.1f}s wall) layers={diag['layers']} errs={len(diag['errs'])}", flush=True)
    return steady


def worker(slot, a, b, url, q):
    """render frames [a,b) of this slot, encode a segment, delete the frames."""
    from playwright.sync_api import sync_playwright
    fdir = os.path.join(OUT, f"frames_{slot}")
    os.makedirs(fdir, exist_ok=True)
    seg_pre = os.path.join(OUT, f"seg_{slot:02d}.mp4")
    if os.path.exists(seg_pre) and os.path.getsize(seg_pre) > 200_000:
        print(f"  [w{slot}] skip frames {a}-{b} (segment exists)", flush=True)
        return slot
    state = {"done": 0, "ms": [], "err": []}
    with sync_playwright() as p:
        br = p.chromium.launch(args=ARGS)
        ctx, pg, err = new_page(br, url)
        state["err"] += err
        for n in range(a, b):
            t = (n + 0.5) / FPS
            if (n - a) and (n - a) % RELOAD_EVERY == 0:
                pg.reload(wait_until="load")
                pg.evaluate("""async()=>{const s=Date.now();while(!window.__loaded&&Date.now()-s<60000)
                              await new Promise(r=>setTimeout(r,150));}""")
                if pg.evaluate("typeof window.gc") == "function":
                    pg.evaluate("window.gc()")
            s0 = time.time()
            for attempt in range(2):
                try:
                    shot(pg, t, os.path.join(fdir, f"f{n:05d}.jpg"))
                    break
                except Exception as e:
                    state["err"].append(f"frame {n}: {str(e)[:90]}")
            state["ms"].append(round((time.time() - s0) * 1000, 1))
            state["done"] += 1
            if state["done"] % 60 == 0:
                print(f"  [w{slot}] {state['done']}/{b-a} frames  mean {np.mean(state['ms']):.0f}ms", flush=True)
        br.close()
    seg = os.path.join(OUT, f"seg_{slot:02d}.mp4")
    dsf = float(os.environ.get("DSF", "1"))
    vf = [] if dsf >= 0.999 else ["-vf", f"scale={W}:{H}:flags=lanczos"]
    subprocess.run([ff(), "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(FPS),
                    "-start_number", str(a), "-i", os.path.join(fdir, "f%05d.jpg"), *vf,
                    "-c:v", "libx264", "-preset", os.environ.get("PRESET", "veryfast"),
                    "-crf", os.environ.get("CRF", "18"), "-pix_fmt", "yuv420p", seg], check=True)
    shutil.rmtree(fdir, ignore_errors=True)
    ms = state["ms"]
    json.dump({"slot": slot, "a": a, "b": b, "frames": state["done"], "ms_mean": round(float(np.mean(ms)), 1),
               "ms_p90": round(float(np.percentile(ms, 90)), 1), "err": state["err"][:8],
               "seg_mb": round(os.path.getsize(seg) / 1e6, 2)},
              open(os.path.join(OUT, f"seg_{slot:02d}.json"), "w"), indent=1)
    return slot


RELOAD_EVERY = int(os.environ.get("RELOAD_EVERY", "400"))


def full():
    import multiprocessing as mp
    srv, base = serve(ROOT)
    url = base + "/out/index.html"
    build_page(base=base + "/")
    os.makedirs(OUT, exist_ok=True)
    segs = sorted(os.path.join(OUT, f) for f in os.listdir(OUT) if f.startswith("seg_") and f.endswith(".mp4"))
    done = len(segs) * int(os.environ.get("CHUNK", "300"))
    n = mp.cpu_count() if os.environ.get("WORKERS") == "auto" else int(os.environ.get("WORKERS", "4"))
    per = int(os.environ.get("CHUNK", "300"))
    jobs, slot = [], 0
    for a in range(0, NFR, per):
        jobs.append((slot, a, min(a + per, NFR), url, int(os.environ.get("JQ", "93"))))
        slot += 1
    t0 = time.time()
    if os.environ.get("CALIBRATE", "1") == "1":
        steady = calibrate(url)
        if steady > float(os.environ.get("CAL_MAX", "1150")):
            os.environ["DSF"] = "0.667"
            print(f"[render] calibration: {steady:.0f}ms/frame -> switching to 720x1280 + Lanczos upscale",
                  flush=True)
        else:
            os.environ["DSF"] = "1"
            print(f"[render] calibration: {steady:.0f}ms/frame -> keeping full {W}x{H}", flush=True)
    print(f"[render] {NFR} frames @ {W}x{H} dsf={os.environ.get('DSF','1')} · {len(jobs)} segments · {n} workers"
          f"{' (resume: ' + str(done) + ' frames already encoded)' if done else ''}")
    with mp.get_context("spawn").Pool(n) as pool:
        for r in pool.imap_unordered(worker_run, jobs):
            el = time.time() - t0
            print(f"[render] segment {r} done  elapsed {el/60:.1f} min  {os.path.getsize(r)/1e6:.1f}MB", flush=True)
    mix = os.path.join(ROOT, "audio", "mix_stereo.wav")
    lst = os.path.join(OUT, "concat.txt")
    open(lst, "w").write("\n".join(f"file '{p}'" for p in sorted(os.path.join(OUT, f) for f in os.listdir(OUT)
                                                                  if f.startswith("seg_") and f.endswith(".mp4"))))
    out = os.path.join(OUT, "brazil.mp4")
    subprocess.run([ff(), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-i", mix, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out], check=True)
    secs = round(time.time() - t0, 1)
    info = subprocess.run([ff(), "-hide_banner", "-i", out], capture_output=True, text=True).stderr
    mb = round(os.path.getsize(out) / 1e6, 1)
    json.dump({"frames": NFR, "secs": secs, "mb": mb, "segments": len(jobs), "out": os.path.basename(out)},
              open(os.path.join(OUT, "render_report.json"), "w"), indent=1)
    srv.shutdown()
    print(f"[render] DONE {os.path.basename(out)} {mb}MB in {secs/60:.1f} min")
    print("  " + [l.strip() for l in info.splitlines() if "Duration" in l or "Video:" in l][0])


def worker_run(job):
    slot, a, b, url, q = job
    os.environ["DSF"] = os.environ.get("DSF", "1")
    try:
        worker(slot, a, b, url, q)
    except Exception as e:
        print(f"[worker {slot}] FAILED {e}", flush=True)
    return os.path.join(OUT, f"seg_{slot:02d}.mp4")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", default="", help="comma list of times in seconds")
    a = ap.parse_args()
    if a.preview:
        preview([float(x) for x in a.preview.split(",")])
    else:
        full()
