"""Simplify the fetched admin-0 polygons so the GL renderer is fast and stable.
geoBoundaries ships 1.9-4.0 MB per country; at 1080x1920 we need <250 KB each and
the silhouette must stay recognisable. Writes assets/geo_min/{ISO}.geojson.
"""
import json, math, os, time
from shapely.geometry import shape, mapping

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "geo")
DST = os.path.join(ROOT, "assets", "geo_min")
MAX_PTS = int(os.environ.get("GEO_MAX_PTS", 2600))
os.makedirs(DST, exist_ok=True)


def count_pts(g):
    c = mapping(g)
    if c["type"] == "Polygon":
        return sum(len(r) for r in c["coordinates"])
    return sum(len(r) for poly in c["coordinates"] for r in poly)


def simplify_to(g, target):
    if g.is_empty:
        return g
    lo, hi = 0.0, max(g.bounds[2] - g.bounds[0], g.bounds[3] - g.bounds[1]) * 0.6
    tol = hi / 8.0
    best = g
    for _ in range(16):
        s = g.simplify(tol, preserve_topology=True)
        if s.is_empty:
            hi = tol; tol = (lo + hi) / 2; continue
        n = count_pts(s)
        if n > target:
            lo = tol; tol = (tol + hi) / 2 if hi > tol else tol * 1.35
        else:
            best = s
            if n > target * 0.55:
                break
            hi = tol; tol = max(tol / 1.9, 1e-6)
        if tol < 1e-6:
            break
    return best


def rnd(g, nd=5):
    c = mapping(g)

    def fix(ring):
        return [[round(x, nd), round(y, nd)] for x, y in ring]
    if c["type"] == "Polygon":
        c["coordinates"] = [fix(r) for r in c["coordinates"]]
    else:
        c["coordinates"] = [[fix(r) for r in poly] for poly in c["coordinates"]]
    return c


def area_km2(g):
    try:
        c = g.centroid
        return g.area * (111.32 ** 2) * math.cos(math.radians(c.y))
    except Exception:
        return None


if __name__ == "__main__":
    t0 = time.time()
    rep = {}
    for f in sorted(os.listdir(SRC)):
        if not f.endswith(".geojson"):
            continue
        iso = f.split(".")[0]
        gj = json.load(open(os.path.join(SRC, f)))
        feats = [x for x in gj.get("features", []) if x.get("geometry")]
        if not feats:
            rep[iso] = {"error": "no geometry"}
            continue
        g = shape(feats[0]["geometry"])
        if g.geom_type == "GeometryCollection":
            from shapely.ops import unary_union
            g = unary_union([x for x in g.geoms if x.area > 0])
        before = count_pts(g)
        g2 = simplify_to(g, MAX_PTS)
        gj2 = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"iso": iso},
                                                            "geometry": rnd(g2)}]}
        p = os.path.join(DST, f"{iso}.geojson")
        json.dump(gj2, open(p, "w"), separators=(",", ":"))
        rep[iso] = {"pts": count_pts(g2), "pts_before": before, "kb": round(os.path.getsize(p) / 1024, 1),
                    "kb_src": round(os.path.getsize(os.path.join(SRC, f)) / 1024, 1),
                    "centroid": [round(g2.centroid.x, 3), round(g2.centroid.y, 3)],
                    "bbox": [round(v, 3) for v in g2.bounds], "area_km2": round(area_km2(g2) or 0)}
        print(f"  {iso}: {rep[iso]['pts_before']:>7,} pts -> {rep[iso]['pts']:>5,} pts   "
              f"{rep[iso]['kb_src']:>7.1f}KB -> {rep[iso]['kb']:>6.1f}KB   area~{rep[iso]['area_km2']:,.0f} km2")
    json.dump(rep, open(os.path.join(DST, "_prep.json"), "w"), indent=1)
    print(f"[prep] {len(rep)} countries in {time.time()-t0:.1f}s")
    raise SystemExit(0 if len(rep) >= 10 else 1)
