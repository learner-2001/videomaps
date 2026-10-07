"""Fetch real-world assets for the Brazil video.
Primary: geoBoundaries ADM0 (CC-BY, high detail). Fallback: Natural Earth 50m (public domain).
Also: flags (flagcdn), fonts (google/fonts), facts (World Bank + REST Countries).
"""
import json, os, time
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
A = os.path.join(ROOT, "assets")
for d in ("geo", "flags", "fonts", "data"):
    os.makedirs(os.path.join(A, d), exist_ok=True)

S = requests.Session()
S.headers["User-Agent"] = "videomaps-render/1.1 (research)"
NEIGHBOURS = ["BRA", "ARG", "BOL", "COL", "GUY", "PRY", "PER", "SUR", "URY", "VEN"]
ISO2 = {"BRA": "br", "ARG": "ar", "BOL": "bo", "COL": "co", "GUY": "gy", "PRY": "py",
        "PER": "pe", "SUR": "sr", "URY": "uy", "VEN": "ve"}
NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
FONTS = {"ArchivoBlack-Regular.ttf": "ofl/archivoblack/ArchivoBlack-Regular.ttf",
         "BarlowCondensed-Black.ttf": "ofl/barlowcondensed/BarlowCondensed-Black.ttf",
         "Inter.ttf": "ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
         "JetBrainsMono.ttf": "ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf"}


def get(url, tries=3, timeout=90):
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(1.2 * (i + 1))
    return None


def natural_earth_fallback():
    r = get(NE_URL)
    if not r:
        return {}
    fc = r.json()
    out = {}
    want = set(NEIGHBOURS)
    for f in fc.get("features", []):
        p = f.get("properties", {})
        iso3 = p.get("ISO_A3") or p.get("ADM0_A3")
        if iso3 in want and iso3 not in out:
            gj = {"type": "FeatureCollection", "features": [f]}
            out[iso3] = gj
    return out


def fetch_country_shapes():
    ne = None  # lazy
    out, errors = {}, []
    for iso3 in NEIGHBOURS:
        meta = get(f"https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM0/")
        gj = None
        if meta is not None:
            m = meta.json()
            url = m.get("simplifiedGeoJSON") or m.get("gjDownloadURL")
            r = get(url) if url else None
            if r is not None:
                gj = r.json()
                out[iso3] = {"bytes": len(r.content), "features": len(gj.get("features", [])),
                             "source": "geoBoundaries/" + str(m.get("boundaryCanonical", "gbOpen")),
                             "license": m.get("boundaryLicense", "?")}
        if gj is None:
            if ne is None:
                ne = natural_earth_fallback()
            if iso3 in ne:
                gj = ne[iso3]
                out[iso3] = {"bytes": len(json.dumps(gj)), "features": len(gj["features"]),
                             "source": "NaturalEarth 50m", "license": "public domain"}
        if gj is None:
            errors.append(iso3)
            continue
        p = os.path.join(A, "geo", f"{iso3}.geojson")
        json.dump(gj, open(p, "w"))
        print(f"  {iso3}: {out[iso3]['bytes']:>9,}B feats={out[iso3]['features']} via {out[iso3]['source'][:24]}")
    json.dump(out, open(os.path.join(A, "geo", "_index.json"), "w"), indent=1)
    return out, errors


def fetch_flags():
    out = {}
    for iso3, iso2 in ISO2.items():
        for w in (1280, 640, 320):
            r = get(f"https://flagcdn.com/w{w}/{iso2}.png")
            if r:
                open(os.path.join(A, "flags", f"{iso2}.png"), "wb").write(r.content)
                out[iso2] = {"bytes": len(r.content), "width": w}
                break
    print(f"  flags: {len(out)}/{len(ISO2)}")
    json.dump(out, open(os.path.join(A, "flags", "_index.json"), "w"), indent=1)
    return out


def fetch_fonts():
    ok = {}
    for name, path in FONTS.items():
        r = get(f"https://raw.githubusercontent.com/google/fonts/main/{path}")
        if r and r.content[:4] in (b"\x00\x01\x00\x00", b"true", b"OTTO"):
            open(os.path.join(A, "fonts", name), "wb").write(r.content)
            ok[name] = len(r.content)
    print(f"  fonts: {len(ok)}/{len(FONTS)}")
    return ok


def fetch_facts():
    facts = {}
    ind = {"gdp_cur_usd": "NY.GDP.MKTP.CD", "gdp_const_usd": "NY.GDP.MKTP.KD", "pop": "SP.POP.TOTL",
           "agri_share_gdp": "NV.AGR.TOTL.ZS", "gdp_growth": "NY.GDP.MKTP.KD.ZG",
           "food_exports_usd": "TX.VAL.FOOD.ZS.UN", "arable_km2": "AG.LND.ARBL.K2",
           "crop_land_pct": "AG.LND.CREL.ZA"}
    for key, code in ind.items():
        r = get(f"https://api.worldbank.org/v2/country/BRA/indicator/{code}?format=json&date=2018:2025&per_page=20")
        try:
            facts[key] = [{"date": x["date"], "value": x["value"]} for x in (r.json()[1] or []) if x["value"] is not None]
        except Exception:
            facts[key] = []
    for iso, key in (("br", "brazil"), ("ar", "argentina")):
        r = get(f"https://restcountries.com/v3.1/alpha/{iso}?fields=name,area,population,borders,cca2,maps,flag")
        try:
            d = r.json()[0]
            facts[key] = {"area_km2": d.get("area"), "pop": d.get("population"), "borders": d.get("borders")}
        except Exception as e:
            facts[key] = {"error": str(e)}
    json.dump(facts, open(os.path.join(A, "data", "facts_raw.json"), "w"), indent=1)
    got = {k: (len(v) if isinstance(v, list) else "obj") for k, v in facts.items()}
    print(f"  facts: {got}")
    return facts


if __name__ == "__main__":
    t0 = time.time()
    geo, gerr = fetch_country_shapes()
    fl, fn, fa = fetch_flags(), fetch_fonts(), fetch_facts()
    sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(A) for f in fs)
    rep = {"secs": round(time.time() - t0, 1), "assets_MB": round(sz / 1e6, 2),
           "geo_countries": len(geo), "geo_missing": gerr, "flags": len(fl),
           "fonts": len(fn), "facts_keys": len(fa),
           "geo_bytes": {k: v["bytes"] for k, v in geo.items()}}
    json.dump(rep, open(os.path.join(ROOT, "fetch_report.json"), "w"), indent=1)
    print("[fetch] " + json.dumps(rep))
    raise SystemExit(0 if len(geo) == len(NEIGHBOURS) else 1)
