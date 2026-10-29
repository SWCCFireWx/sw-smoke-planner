#!/usr/bin/env python3
"""
SW Smoke Dispersion Planner - data fetcher.

Runs inside GitHub Actions (see .github/workflows/update-data.yml). It downloads:
  1. GACC boundaries          (NIFC ArcGIS)  -> data/gacc.geojson
  2. Current fire perimeters  (NIFC WFIGS)   -> data/perimeters.geojson
  3. Hourly forecast grid     (Open-Meteo GFS/HRRR) -> data/forecast.json
and writes data/meta.json with timestamps and status.

The web page only ever reads these files from the repository, so nobody has to
touch ArcGIS or any API by hand. Uses only the Python standard library.

Usage:
  python fetch_data.py                   # perimeters every run, forecast if older than 5.5 h
  python fetch_data.py --force-forecast  # always refresh the forecast
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")

# ---------------------------------------------------------------------------
# Domain: Southwest GACC (AZ, NM, W TX, OK panhandle area) plus the edges of the
# neighboring GACCs (Southern California, Great Basin, Rocky Mountain, Southern
# Area) so the map is not cut off at the SW boundary.
# ---------------------------------------------------------------------------
BBOX = {"west": -120.5, "south": 29.5, "east": -100.0, "north": 41.5}
GRID = {"west": -119.25, "east": -101.25, "south": 30.75, "north": 40.5, "step": 0.75}

NIFC = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services"
GACC_URL = NIFC + "/DMP_NationalGACCBoundaries_Public/FeatureServer/0/query"
PERIM_URL = NIFC + "/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"

OM_URL = "https://api.open-meteo.com/v1/gfs"   # GFS + HRRR blend (gfs_seamless)
LEVELS = [850, 800, 700, 600, 500]              # hPa levels used for transport wind
FORECAST_HOURS = 96
BATCH = 35              # locations per Open-Meteo request
BATCH_PAUSE = 12        # seconds between requests (stay well under free-tier limits)

UA = "SW-Smoke-Dispersion-Planner (GitHub Actions; non-commercial)"
M_TO_FT = 3.28084
KT_TO_MPH = 1.15078


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_get_json(url, params, retries=5):
    full = url + "?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last = e
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")[:300]
            except Exception:
                pass
            print(f"  HTTP {e.code} (attempt {attempt}/{retries}) {body}", flush=True)
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(90, 15 * attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            print(f"  network error (attempt {attempt}/{retries}): {e}", flush=True)
            time.sleep(10 * attempt)
    raise RuntimeError(f"giving up on {url}: {last}")


def arcgis_geojson(url, extra):
    """Query an ArcGIS FeatureServer layer inside BBOX, following pagination."""
    feats, offset = [], 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "true",
            "geometry": f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": 1000,
        }
        params.update(extra)
        j = http_get_json(url, params)
        if "error" in j:
            raise RuntimeError(f"ArcGIS error: {j['error']}")
        batch = j.get("features") or []
        feats.extend(batch)
        exceeded = j.get("exceededTransferLimit") or (j.get("properties") or {}).get("exceededTransferLimit")
        if not exceeded or not batch:
            break
        offset += len(batch)
    return {"type": "FeatureCollection", "features": feats}


def round_coords(geom, nd):
    def r(c):
        if isinstance(c, (int, float)):
            return round(c, nd)
        return [r(x) for x in c]
    if geom and "coordinates" in geom:
        geom["coordinates"] = r(geom["coordinates"])
    return geom


def pick(props, *names):
    for n in names:
        v = props.get(n)
        if v not in (None, ""):
            return v
    return None


def write_json(name, obj):
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)
    print(f"  wrote {path} ({os.path.getsize(path)/1024:.0f} KB)")


def read_json(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 1. GACC boundaries
# ---------------------------------------------------------------------------
def fetch_gaccs():
    print("Fetching GACC boundaries ...", flush=True)
    fc = arcgis_geojson(GACC_URL, {
        "outFields": "GACCName,GACCAbbreviation,GACCUnitID",
        "maxAllowableOffset": "0.01",
        "geometryPrecision": "3",
    })
    out = []
    for f in fc["features"]:
        p = f.get("properties") or {}
        name = p.get("GACCName") or ""
        out.append({
            "type": "Feature",
            "geometry": round_coords(f.get("geometry"), 3),
            "properties": {
                "name": name,
                "abbr": p.get("GACCAbbreviation") or p.get("GACCUnitID") or "",
                "isSW": "southwest" in name.lower(),
            },
        })
    if not out:
        raise RuntimeError("no GACC features returned")
    print(f"  {len(out)} GACCs: " + ", ".join(sorted(x['properties']['name'] for x in out)))
    write_json("gacc.geojson", {"type": "FeatureCollection", "features": out})
    return len(out)


# ---------------------------------------------------------------------------
# 2. Current WFIGS perimeters (wildfire + prescribed fire)
# ---------------------------------------------------------------------------
def fetch_perimeters():
    print("Fetching WFIGS current perimeters ...", flush=True)
    fc = arcgis_geojson(PERIM_URL, {"maxAllowableOffset": "0.0005", "geometryPrecision": "5"})
    out = []
    for f in fc["features"]:
        p = f.get("properties") or {}
        if not f.get("geometry"):
            continue
        typ = (pick(p, "attr_IncidentTypeCategory", "IncidentTypeCategory", "poly_IncidentTypeCategory") or "").upper()
        out.append({
            "type": "Feature",
            "geometry": round_coords(f["geometry"], 5),
            "properties": {
                "name": pick(p, "poly_IncidentName", "attr_IncidentName", "IncidentName") or "Unnamed",
                "type": typ or "WF",
                "acres": pick(p, "poly_GISAcres", "attr_IncidentSize", "GISAcres"),
                "contained": pick(p, "attr_PercentContained", "PercentContained"),
                "discovered": pick(p, "attr_FireDiscoveryDateTime", "FireDiscoveryDateTime"),
                "updated": pick(p, "poly_DateCurrent", "poly_PolygonDateTime", "attr_ModifiedOnDateTime_dt"),
                "state": pick(p, "attr_POOState", "POOState"),
                "id": pick(p, "attr_UniqueFireIdentifier", "UniqueFireIdentifier", "poly_IRWINID"),
            },
        })
    n_rx = sum(1 for x in out if x["properties"]["type"] == "RX")
    print(f"  {len(out)} perimeters ({n_rx} prescribed)")
    write_json("perimeters.geojson", {"type": "FeatureCollection", "features": out})
    return len(out)


# ---------------------------------------------------------------------------
# 3. Forecast grid (Open-Meteo GFS/HRRR)
# ---------------------------------------------------------------------------
def grid_points():
    g = GRID
    nlat = int(round((g["north"] - g["south"]) / g["step"])) + 1
    nlon = int(round((g["east"] - g["west"]) / g["step"])) + 1
    pts = []
    for i in range(nlat):
        for j in range(nlon):
            pts.append((round(g["south"] + i * g["step"], 4), round(g["west"] + j * g["step"], 4)))
    return pts


def hourly_vars():
    v = ["temperature_2m", "relative_humidity_2m", "precipitation_probability", "precipitation",
         "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m", "boundary_layer_height"]
    for L in LEVELS:
        v += [f"wind_speed_{L}hPa", f"wind_direction_{L}hPa", f"geopotential_height_{L}hPa"]
    return v


def _r(x, nd=0):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return int(round(x)) if nd == 0 else round(x, nd)


def process_location(loc):
    """Compute mixing height, transport wind and ventilation rate for one point."""
    h = loc["hourly"]
    elev = loc.get("elevation") or 0.0
    n = len(h["time"])
    out = {k: [] for k in ("mh", "tw", "twd", "vi", "pop", "qpf", "ws", "wd", "wg", "t", "rh")}
    for i in range(n):
        blh = h["boundary_layer_height"][i]
        ws10 = h["wind_speed_10m"][i]           # knots (requested in kn)
        wd10 = h["wind_direction_10m"][i]
        gust = h["wind_gusts_10m"][i]

        mh_ft = tw = twd = vi = None
        if blh is not None and ws10 is not None and wd10 is not None:
            top = elev + max(blh, 0.0)
            speeds = [ws10]
            us = [-ws10 * math.sin(math.radians(wd10))]
            vs = [-ws10 * math.cos(math.radians(wd10))]
            # Average every pressure level that sits inside the mixed layer.
            for L in LEVELS:
                z = h[f"geopotential_height_{L}hPa"][i]
                s = h[f"wind_speed_{L}hPa"][i]
                d = h[f"wind_direction_{L}hPa"][i]
                if None in (z, s, d):
                    continue
                if elev + 10 < z <= top:
                    speeds.append(s)
                    us.append(-s * math.sin(math.radians(d)))
                    vs.append(-s * math.cos(math.radians(d)))
            tw = sum(speeds) / len(speeds)                      # scalar mean speed (kt)
            u, v = sum(us) / len(us), sum(vs) / len(vs)
            twd = (math.degrees(math.atan2(-u, -v)) + 360) % 360  # direction wind is FROM
            mh_ft = max(blh, 0.0) * M_TO_FT
            vi = mh_ft * tw                                     # knot-feet (NM smoke mgmt)

        out["mh"].append(None if mh_ft is None else int(round(mh_ft / 10.0) * 10))
        out["tw"].append(_r(tw, 1))
        out["twd"].append(_r(twd))
        out["vi"].append(None if vi is None else int(round(vi / 100.0) * 100))
        out["pop"].append(_r(h["precipitation_probability"][i]))
        out["qpf"].append(_r(h["precipitation"][i], 2))
        out["ws"].append(None if ws10 is None else _r(ws10 * KT_TO_MPH))
        out["wd"].append(_r(wd10))
        out["wg"].append(None if gust is None else _r(gust * KT_TO_MPH))
        out["t"].append(_r(h["temperature_2m"][i]))
        out["rh"].append(_r(h["relative_humidity_2m"][i]))
    return out, elev


def fetch_forecast():
    pts = grid_points()
    print(f"Fetching Open-Meteo GFS/HRRR forecast for {len(pts)} grid points ...", flush=True)
    variables = hourly_vars()
    points, times = [], None
    for b in range(0, len(pts), BATCH):
        chunk = pts[b:b + BATCH]
        params = {
            "latitude": ",".join(str(p[0]) for p in chunk),
            "longitude": ",".join(str(p[1]) for p in chunk),
            "hourly": ",".join(variables),
            "wind_speed_unit": "kn",
            "temperature_unit": "fahrenheit",
            "precipitation_unit": "inch",
            "timeformat": "unixtime",
            "timezone": "GMT",
            "forecast_hours": FORECAST_HOURS,
        }
        j = http_get_json(OM_URL, params)
        locs = j if isinstance(j, list) else [j]
        if len(locs) != len(chunk):
            raise RuntimeError(f"expected {len(chunk)} locations, got {len(locs)}")
        for (lat, lon), loc in zip(chunk, locs):
            if "hourly" not in loc:
                raise RuntimeError(f"no hourly data for {lat},{lon}: {str(loc)[:200]}")
            if times is None:
                times = loc["hourly"]["time"]
            series, elev = process_location(loc)
            points.append({"lat": lat, "lon": lon, "elev": _r(elev), **series})
        print(f"  {min(b + BATCH, len(pts))}/{len(pts)} points", flush=True)
        if b + BATCH < len(pts):
            time.sleep(BATCH_PAUSE)

    write_json("forecast.json", {
        "generated": int(time.time()),
        "source": "Open-Meteo GFS API (NOAA GFS + HRRR, gfs_seamless)",
        "units": {"mh": "ft AGL", "tw": "kt", "vi": "kt-ft", "pop": "%", "qpf": "in/hr",
                  "ws": "mph (10 m)", "wg": "mph", "t": "F", "rh": "%"},
        "levels_hPa": LEVELS,
        "grid": GRID,
        "times": times,
        "points": points,
    })
    return len(points)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-forecast", action="store_true")
    ap.add_argument("--forecast-max-age-hours", type=float, default=5.5)
    args = ap.parse_args()

    meta = read_json("meta.json") or {}
    meta.setdefault("sources", {})
    now = int(time.time())
    failures = 0

    def run(key, fn, label):
        nonlocal failures
        try:
            count = fn()
            meta["sources"][key] = {"label": label, "updated": int(time.time()), "count": count, "ok": True}
        except Exception as e:  # keep the previous file if a source is down
            failures += 1
            print(f"!! {key} failed: {e}", file=sys.stderr, flush=True)
            prev = meta["sources"].get(key, {})
            prev.update({"label": label, "ok": False, "error": str(e)[:300], "last_attempt": int(time.time())})
            meta["sources"][key] = prev

    # GACC boundaries rarely change: refresh once a day.
    gacc_age = now - (meta["sources"].get("gacc", {}).get("updated") or 0)
    if gacc_age > 86400 or not os.path.exists(os.path.join(DATA, "gacc.geojson")):
        run("gacc", fetch_gaccs, "NIFC National GACC Boundaries")

    run("perimeters", fetch_perimeters, "NIFC WFIGS Current Interagency Fire Perimeters")

    fc_age = now - (meta["sources"].get("forecast", {}).get("updated") or 0)
    if args.force_forecast or fc_age > args.forecast_max_age_hours * 3600 \
            or not os.path.exists(os.path.join(DATA, "forecast.json")):
        run("forecast", fetch_forecast, "Open-Meteo GFS/HRRR")
    else:
        print(f"Forecast is {fc_age/3600:.1f} h old; skipping (use --force-forecast to override).")

    meta["last_run"] = int(time.time())
    write_json("meta.json", meta)
    # Only fail the workflow if nothing usable exists.
    if failures and not os.path.exists(os.path.join(DATA, "forecast.json")):
        sys.exit(1)


if __name__ == "__main__":
    main()
