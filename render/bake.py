"""Bake georeferenced overlays: each country's flag (or a real CC photo) masked to its own
border polygon, in Web-Mercator space so it locks to the MapLibre geometry at any zoom/bearing.
This is the reference video's signature look - flag texture inside a country shape - but built
from real polygons instead of a hallucinated map.
Outputs out/over/*.png + out/over/index.json (with corner coords for MapLibre image sources).
"""
import json, math, os, sys, time
import requests
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(ROOT, "assets")
OUT = os.path.join(ROOT, "out", "over")
Z = 6                      # world pixel scale for baking: 256 * 2^Z
S = requests.Session()
S.headers["User-Agent"] = "videomaps-bake/1.0 (CC-licensed imagery)"
ISO2 = {"BRA": "br", "ARG": "ar", "BOL": "bo", "COL": "co", "GUY": "gy", "PRY": "py",
        "PER": "pe", "SUR": "sr", "URY": "uy", "VEN": "ve", "FRA": "gf", "CHL": "cl", "ECU": "ec"}
# neighbour order for the "ten neighbours" scene (flag fills), then the two exceptions
NEIGH = ["ARG", "URY", "PRY", "BOL", "PER", "COL", "VEN", "GUY", "SUR", "FRA"]
EXCEPT = ["CHL", "ECU"]
PHOTO_QUERY = {
    "amazon": "Amazon river from the air",
    "soy": "soybean field harvest Brazil",
    "citrus": "orange grove harvest Brazil",
    "cane": "sugarcane harvest Brazil",
    "rio": "Rio de Janeiro coastline aerial",
    "forest": "Araucaria forest Brazil aerial",
}


def merc_xy(lon, lat):
    x = (lon + 180.0) / 360.0
    r = math.radians(max(-84.9, min(84.9, lat)))
    y = (1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0
    return x, y


def inv_merc(x, y):
    lon = x * 360.0 - 180.0
    n = math.pi * (1 - 2 * y)
    lat = math.degrees(math.atan(math.sinh(n)))
    return lon, lat


def rings(geom):
    """yield polygon rings as [(lon,lat),...]"""
    t = geom.get("type")
    c = geom.get("coordinates")
    if t == "Polygon":
        for r in c:
            yield r
    elif t == "MultiPolygon":
        for poly in c:
            for r in poly:
                yield r


def bbox_of(geom):
    xs, ys = [], []
    for rs in rings(geom):
        for q in rs:
            xs.append(q[0]); ys.append(q[1])
    return min(xs), min(ys), max(xs), max(ys)


def poly_mask(size, geom, x0, ytop, x1, ybot, feather=1.2):
    """Project lon/lat ring vertices into the baked image grid using the same Web-Mercator
    math MapLibre uses, so the mask cannot drift away from the polygon at any zoom."""
    ww, hh = size
    dx = (x1 - x0) or 1e-9
    dy = (ybot - ytop) or 1e-9
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    for r in rings(geom):
        pts = []
        for lon, lat in r:
            mx, my = merc_xy(lon, lat)
            pts.append(((mx - x0) / dx * ww, (my - ytop) / dy * hh))
        if len(pts) > 2:
            d.polygon(pts, fill=255)
    if feather:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    return m


def load_geom(iso):
    for d in ("geo_min", "geo"):
        p = os.path.join(A, d, f"{iso}.geojson")
        if os.path.exists(p):
            gj = json.load(open(p))
            for f in gj["features"]:
                if f.get("geometry"):
                    return f["geometry"]
    return None


def commons_photo(query, dest):
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search"
           f"&gsrnamespace=6&gsrlimit=8&gsrsearch={requests.utils.quote(query)}"
           "&prop=imageinfo&iiprop=url|mime|size|extmetadata&iiurlwidth=1600")
    try:
        pages = S.get(url, timeout=45).json().get("query", {}).get("pages", {})
    except Exception as e:
        print("  commons search failed:", e)
        return None
    best = None
    for p in sorted(pages.values(), key=lambda z: z.get("index", 99)):
        ii = (p.get("imageinfo") or [{}])[0]
        mime = ii.get("mime", "")
        if mime not in ("image/jpeg", "image/png"):
            continue
        w, h = ii.get("width", 0), ii.get("height", 0)
        if w < 900 or h < 600:
            continue
        best = (ii.get("thumburl") or ii.get("url"), p.get("title"),
                ((ii.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "CC"))
        break
    if not best:
        return None
    r = S.get(best[0], timeout=60)
    if r.status_code != 200:
        return None
    open(dest, "wb").write(r.content)
    return {"file": os.path.basename(dest), "title": best[1], "license": best[2], "bytes": len(r.content)}


def bake(iso, img, kind, scenes, scene_ids=("3",)):
    geom = load_geom(iso)
    if geom is None:
        return None
    w0, s0, e0, n0 = bbox_of(geom)
    x0, y1 = merc_xy(w0, n0)      # top-left (merc y grows downward)
    x1, y0 = merc_xy(e0, s0)
    W = 256 * (2 ** Z)
    ww = max(8, int(round(abs(x1 - x0) * W)))
    hh = max(8, int(round(abs(y0 - y1) * W)))     # mercator y grows downward: use abs
    if ww * hh > 24_000_000:                 # keep VRAM/encode sane
        f = math.sqrt(24_000_000 / (ww * hh)); ww, hh = int(ww * f), int(hh * f)
    src = Image.open(img).convert("RGB")
    scale = max(ww / src.width, hh / src.height)
    src = src.resize((int(src.width * scale) + 1, int(src.height * scale) + 1), Image.LANCZOS)
    left = (src.width - ww) // 2
    top = (src.height - hh) // 2
    src = src.crop((left, top, left + ww, top + hh))
    m = poly_mask((ww, hh), geom, x0, y1, x1, y0)
    out = Image.new("RGBA", (ww, hh))
    out.paste(src, (0, 0), m)
    if kind.endswith("except"):
        g = out.convert("L").convert("RGBA")
        g.putalpha(Image.eval(m, lambda v: int(v * 0.30)))
        out = g
    elif kind == "flag":
        out.putalpha(Image.eval(m, lambda v: int(v * 0.92)))
    p = os.path.join(OUT, f"{iso}_{kind}.png")
    out.save(p, optimize=True)
    rec = {"iso": iso, "kind": kind, "file": os.path.basename(p), "wh": [ww, hh],
           "bbox_lonlat": [round(w0, 3), round(s0, 3), round(e0, 3), round(n0, 3)],
           "center": [round((w0 + e0) / 2, 3), round((s0 + n0) / 2, 3)],
           "coords": [[w0, n0], [e0, n0], [e0, s0], [w0, s0]]}
    for sid in scene_ids:
        scenes.setdefault(sid, []).append(rec)
    return rec


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    index = {"z_scale": 256 * (2 ** Z), "scenes": {}}
    scenes = {str(k): [] for k in (1, 2, 3, 4, 5, 6, 7, 8, 9)}
    photos = {}
    for key, q in PHOTO_QUERY.items():
        dest = os.path.join(A, "photos", f"{key}.jpg")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not os.path.exists(dest):
            meta = commons_photo(q, dest)
            photos[key] = meta
            print(f"  photo {key:7s} <- {meta['title'] if meta else 'NONE'} {meta['license'] if meta else ''}")
        else:
            photos[key] = {"file": f"{key}.jpg", "title": "(cached)"}
    # scene 3: every neighbour filled with its own flag
    for n, iso in enumerate(NEIGH):
        f = os.path.join(A, "flags", f"{ISO2[iso]}.png")
        if os.path.exists(f):
            bake(iso, f, "flag", scenes, scene_ids=("3", "4"))
    for iso in EXCEPT:                       # shown greyed: the two that do NOT touch Brazil
        f = os.path.join(A, "flags", f"{ISO2[iso]}.png")
        if os.path.exists(f):
            bake(iso, f, "except", scenes, scene_ids=("3",))
    # scene 2/5/6/7/8: Brazil filled with real CC photography
    for key, sc in (("rio", "2"), ("soy", "5"), ("citrus", "5"), ("cane", "6"),
                    ("forest", "7"), ("amazon", "7")):
        p = os.path.join(A, "photos", f"{key}.jpg")
        if os.path.exists(p):
            r = bake("BRA", p, f"photo_{key}", scenes, scene_ids=(sc,))
            if r:
                r["credit"] = photos.get(key, {}).get("title", "")
                scenes[sc].append(r)
    if os.path.exists(os.path.join(A, "flags", "br.png")):
        bake("BRA", os.path.join(A, "flags", "br.png"), "flag", scenes, scene_ids=("1", "2", "9"))
    json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "z_scale": 256 * (2 ** Z), "photos": photos, "scenes": scenes,
               "bake_secs": round(time.time() - t0, 1)},
              open(os.path.join(OUT, "index.json"), "w"), indent=1)
    tot = sum(len(v) for v in scenes.values())
    n = sum(1 for f in os.listdir(OUT) if f.endswith(".png"))
    mb = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT) if f.endswith(".png")) / 1e6
    print(f"[bake] {n} overlays ({mb:.1f}MB) across {tot} scene slots in {time.time()-t0:.1f}s")
    return 0 if n >= 8 else 1


if __name__ == "__main__":
    sys.exit(main())
