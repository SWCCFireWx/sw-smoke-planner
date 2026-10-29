#!/usr/bin/env python3
"""
SW Smoke Dispersion Planner - data builder.

Runs inside GitHub Actions (.github/workflows/update-data.yml) and writes the
site's data folder:

  manifest.json        forecast run, valid times, grid, field encodings
  f_<field>.png        one image per field; every forecast hour is a horizontal
                       band, each pixel holds a 16-bit value (R = high byte,
                       G = low byte, 65535 = missing)
  gacc.geojson         NIFC National GACC Boundaries
  perimeters.geojson   NIFC WFIGS current perimeters (wildfire + prescribed)
  meta.json            when each source was last fetched and whether it worked

Forecast source: NOAA National Blend of Models (NBM) CONUS "core" GRIB2 files
from NOAA's open-data bucket on AWS. Only the needed GRIB messages are
downloaded (HTTP byte ranges from the .idx inventory files). NBM provides the
fire-weather fields directly: mixing height (MIXHT), transport wind
(TRWSPD/TRWDIR), 10-m wind, gust, 2-m temperature and RH, 1-hr PoP and QPF.

Requires: numpy, scipy, pygrib, pyproj  (installed by the workflow).
Usage:   python fetch_data.py --out _site/data
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import math
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
import zlib

import numpy as np

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ---------------------------------------------------------------------------
# Domain: Southwest GACC (AZ/NM/W TX) plus the edges of the neighboring GACCs.
# ---------------------------------------------------------------------------
BBOX = {"west": -120.5, "south": 29.5, "east": -100.0, "north": 41.5}
STEP = 0.1                       # output grid spacing in degrees (~10 km)
NEIGHBORS = 12                   # NBM 2.5-km points averaged into each output cell

BUCKET = "https://noaa-nbm-grib2-pds.s3.amazonaws.com"
FHOURS = list(range(1, 49)) + list(range(51, 97, 3))   # hourly to 48 h, 3-hourly to 96 h

NIFC = "https://services3.arcgis.com/T4QMspbfLg3qTGWY/ArcGIS/rest/services"
GACC_URL = NIFC + "/DMP_NationalGACCBoundaries_Public/FeatureServer/0/query"
PERIM_URL = NIFC + "/WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"

UA = "SW-Smoke-Dispersion-Planner (GitHub Actions; non-commercial)"
M_TO_FT = 3.28084
MS_TO_KT = 1.943844
MS_TO_MPH = 2.236936
MISSING = 65535

# Output fields: name -> (scale, offset, units). Stored value = round((v - offset) / scale).
FIELDS = {
    "mh":  (10.0,  0.0,   "ft AGL"),      # mixing height
    "tw":  (0.1,   0.0,   "kt"),          # transport wind speed
    "twd": (1.0,   0.0,   "deg"),         # transport wind direction (from)
    "vi":  (100.0, 0.0,   "kt-ft"),       # ventilation rate
    "pop": (1.0,   0.0,   "%"),           # chance of precip (>0.01 in) in the window ending this hour
    "qpf": (0.001, 0.0,   "in"),          # precip amount in the window ending this hour
    "ws":  (0.1,   0.0,   "mph"),         # 10-m wind speed
    "wd":  (1.0,   0.0,   "deg"),         # 10-m wind direction (from)
    "wg":  (0.1,   0.0,   "mph"),         # 10-m gust
    "t":   (0.1,  -80.0,  "F"),           # 2-m temperature
    "rh":  (1.0,   0.0,   "%"),           # 2-m relative humidity
}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def http_get(url, params=None, headers=None, retries=5, timeout=120):
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    last = None
    for attempt in range(1, retries + 1):
        try:
            h = {"User-Agent": UA}
            h.update(headers or {})
            req = urllib.request.Request(full, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404:
                raise
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 5 * attempt))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5 * attempt)
    raise RuntimeError(f"giving up on {url}: {last}")


def http_get_json(url, params):
    return json.loads(http_get(url, params).decode("utf-8"))


def exists(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Small file helpers
# ---------------------------------------------------------------------------
def write_json(out, name, obj):
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, separators=(",", ":"))
    print(f"  wrote {name} ({os.path.getsize(path) / 1024:.0f} KB)")


def write_png16(path, arr):
    """Write a uint16 2-D array as an 8-bit RGB PNG: R = high byte, G = low byte, B = 0."""
    h, w = arr.shape
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[..., 0] = (arr >> 8).astype(np.uint8)
    rgb[..., 1] = (arr & 0xFF).astype(np.uint8)
    raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------------------
# ArcGIS (GACC boundaries, WFIGS perimeters)
# ---------------------------------------------------------------------------
def arcgis_geojson(url, extra):
    feats, offset = [], 0
    while True:
        params = {
            "where": "1=1", "outFields": "*", "returnGeometry": "true",
            "geometry": f"{BBOX['west']},{BBOX['south']},{BBOX['east']},{BBOX['north']}",
            "geometryType": "esriGeometryEnvelope", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects", "outSR": "4326", "f": "geojson",
            "resultOffset": offset, "resultRecordCount": 1000,
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
    return feats


def round_coords(geom, nd):
    def r(c):
        return round(c, nd) if isinstance(c, (int, float)) else [r(x) for x in c]
    if geom and "coordinates" in geom:
        geom["coordinates"] = r(geom["coordinates"])
    return geom


def pick(props, *names):
    for n in names:
        v = props.get(n)
        if v not in (None, ""):
            return v
    return None


def fetch_gaccs(out):
    print("Fetching GACC boundaries ...", flush=True)
    feats = arcgis_geojson(GACC_URL, {"outFields": "GACCName,GACCAbbreviation,GACCUnitID",
                                      "maxAllowableOffset": "0.01", "geometryPrecision": "3"})
    res = []
    for f in feats:
        p = f.get("properties") or {}
        name = p.get("GACCName") or ""
        res.append({"type": "Feature", "geometry": round_coords(f.get("geometry"), 3),
                    "properties": {"name": name, "abbr": p.get("GACCAbbreviation") or p.get("GACCUnitID") or "",
                                   "isSW": "southwest" in name.lower()}})
    if not res:
        raise RuntimeError("no GACC features returned")
    write_json(out, "gacc.geojson", {"type": "FeatureCollection", "features": res})
    return len(res)


def fetch_perimeters(out):
    print("Fetching WFIGS current perimeters ...", flush=True)
    feats = arcgis_geojson(PERIM_URL, {"maxAllowableOffset": "0.0005", "geometryPrecision": "5"})
    res = []
    for f in feats:
        p = f.get("properties") or {}
        if not f.get("geometry"):
            continue
        typ = (pick(p, "attr_IncidentTypeCategory", "IncidentTypeCategory", "poly_IncidentTypeCategory") or "").upper()
        res.append({"type": "Feature", "geometry": round_coords(f["geometry"], 5), "properties": {
            "name": pick(p, "poly_IncidentName", "attr_IncidentName", "IncidentName") or "Unnamed",
            "type": typ or "WF",
            "acres": pick(p, "poly_GISAcres", "attr_IncidentSize", "GISAcres"),
            "contained": pick(p, "attr_PercentContained", "PercentContained"),
            "discovered": pick(p, "attr_FireDiscoveryDateTime", "FireDiscoveryDateTime"),
            "updated": pick(p, "poly_DateCurrent", "poly_PolygonDateTime", "attr_ModifiedOnDateTime_dt"),
            "state": pick(p, "attr_POOState", "POOState"),
            "id": pick(p, "attr_UniqueFireIdentifier", "UniqueFireIdentifier", "poly_IRWINID"),
        }})
    print(f"  {len(res)} perimeters ({sum(1 for x in res if x['properties']['type'] == 'RX')} prescribed)")
    write_json(out, "perimeters.geojson", {"type": "FeatureCollection", "features": res})
    return len(res)


# ---------------------------------------------------------------------------
# NBM: inventory parsing and message selection
# ---------------------------------------------------------------------------
def nbm_url(cycle, fh):
    return f"{BUCKET}/blend.{cycle:%Y%m%d}/{cycle:%H}/core/blend.t{cycle:%H}z.core.f{fh:03d}.co.grib2"


def parse_idx(text):
    """Return a list of (start, end_or_None, line) byte ranges from a GRIB .idx file."""
    rows = []
    for line in text.splitlines():
        parts = line.split(":")
        if len(parts) > 2 and parts[1].isdigit():
            rows.append((int(parts[1]), line))
    out = []
    for i, (start, line) in enumerate(rows):
        end = rows[i + 1][0] - 1 if i + 1 < len(rows) else None
        out.append((start, end, line))
    return out


ACC_RE = re.compile(r":APCP:surface:(\d+)-(\d+) hour acc fcst:")


def _plain(line):
    return "std dev" not in line and "prob" not in line


def select_messages(idx_rows, fh, prev_fh):
    """Pick the byte range for each needed NBM field in one forecast file."""
    want = {
        "MIXHT": lambda l: ":MIXHT:" in l and _plain(l),
        "TRWSPD": lambda l: ":TRWSPD:" in l and _plain(l),
        "TRWDIR": lambda l: ":TRWDIR:" in l and _plain(l),
        "WIND": lambda l: ":WIND:10 m above ground:" in l and _plain(l),
        "WDIR": lambda l: ":WDIR:10 m above ground:" in l and _plain(l),
        "GUST": lambda l: ":GUST:10 m above ground:" in l and _plain(l),
        "TMP": lambda l: ":TMP:2 m above ground:" in l and _plain(l),
        "RH": lambda l: ":RH:2 m above ground:" in l and _plain(l),
    }
    inst = f":{fh} hour fcst:"
    sel = {}
    for key, ok in want.items():
        cands = [(s, e, l) for s, e, l in idx_rows if ok(l)]
        if cands:   # prefer the instantaneous record valid at this hour
            exact = [c for c in cands if inst in c[2]]
            sel[key] = (exact or cands)[0]

    gap = fh - prev_fh
    for key, is_prob in (("POP", True), ("QPF", False)):
        best = None
        for s, e, l in idx_rows:
            m = ACC_RE.search(l)
            if not m or int(m.group(2)) != fh or "std dev" in l:
                continue
            if is_prob != (">0.254" in l):
                continue
            if not is_prob and "prob" in l:
                continue
            win = int(m.group(2)) - int(m.group(1))
            score = (0 if win == gap else 1, win)   # exact match to the time step first, then shortest
            if best is None or score < best[0]:
                best = (score, (s, e, l), win)
        if best:
            sel[key] = best[1]
            sel[key + "_WIN"] = best[2]
    return sel


def fetch_range(url, start, end):
    rng = f"bytes={start}-" + ("" if end is None else str(end))
    return http_get(url, headers={"Range": rng}, timeout=180)


def decode_grib(msg, latlons=False):
    """Decode one GRIB2 message -> (values 2-D float32 with NaN, lats, lons). Replaced in tests."""
    import pygrib
    g = pygrib.fromstring(msg)
    vals = np.ma.filled(np.ma.masked_invalid(g.values).astype(np.float32), np.nan)
    if latlons:
        lats, lons = g.latlons()
        return vals, lats, lons
    return vals, None, None


# ---------------------------------------------------------------------------
# NBM: regridding to a regular lat/lon grid
# ---------------------------------------------------------------------------
class Regridder:
    def __init__(self, lats, lons):
        from scipy.spatial import cKDTree
        lons = np.where(lons > 180, lons - 360, lons)
        m = 0.4
        mask = ((lats >= BBOX["south"] - m) & (lats <= BBOX["north"] + m) &
                (lons >= BBOX["west"] - m) & (lons <= BBOX["east"] + m))
        self.mask = mask.ravel()
        self.coslat = math.cos(math.radians((BBOX["south"] + BBOX["north"]) / 2))
        pts = np.column_stack([lats.ravel()[self.mask], lons.ravel()[self.mask] * self.coslat])
        self.nx = int(round((BBOX["east"] - BBOX["west"]) / STEP)) + 1
        self.ny = int(round((BBOX["north"] - BBOX["south"]) / STEP)) + 1
        glat = BBOX["north"] - np.arange(self.ny) * STEP          # row 0 = north
        glon = BBOX["west"] + np.arange(self.nx) * STEP
        LON, LAT = np.meshgrid(glon, glat)
        tgt = np.column_stack([LAT.ravel(), LON.ravel() * self.coslat])
        dist, self.idx = cKDTree(pts).query(tgt, k=NEIGHBORS)
        self.outside = dist[:, 0] > STEP * 1.5                     # beyond the NBM grid

    def scalar(self, vals):
        v = vals.ravel()[self.mask][self.idx]
        with np.errstate(all="ignore"):
            out = np.nanmean(v, axis=1)
        out[self.outside] = np.nan
        return out.reshape(self.ny, self.nx)

    def vector(self, speed, direction):
        """Average wind by u/v components; returns (mean speed, vector-mean direction)."""
        s = speed.ravel()[self.mask][self.idx]
        d = np.radians(direction.ravel()[self.mask][self.idx])
        with np.errstate(all="ignore"):
            u = np.nanmean(-s * np.sin(d), axis=1)
            v = np.nanmean(-s * np.cos(d), axis=1)
            spd = np.nanmean(s, axis=1)
        dirn = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
        spd[self.outside] = np.nan
        dirn[self.outside] = np.nan
        return spd.reshape(self.ny, self.nx), dirn.reshape(self.ny, self.nx)


# ---------------------------------------------------------------------------
# NBM: main forecast build
# ---------------------------------------------------------------------------
def find_cycle(now=None):
    now = now or dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    last = FHOURS[-1]
    for back in range(1, 13):
        cyc = now - dt.timedelta(hours=back)
        if exists(nbm_url(cyc, last) + ".idx") and exists(nbm_url(cyc, FHOURS[0]) + ".idx"):
            return cyc
    raise RuntimeError("no complete NBM cycle found in the last 12 hours")


def get_idx(cycle, fh):
    try:
        return fh, parse_idx(http_get(nbm_url(cycle, fh) + ".idx").decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return fh, None
        raise


def download(cycle, fh, sel):
    url = nbm_url(cycle, fh)
    raw = {key: fetch_range(url, s, e) for key, (s, e, _) in
           ((k, v) for k, v in sel.items() if not k.endswith("_WIN"))}
    return fh, {"raw": raw, "qwin": sel.get("QPF_WIN"), "popwin": sel.get("POP_WIN")}


def fetch_forecast(out):
    cycle = find_cycle()
    print(f"Using NBM cycle {cycle:%Y-%m-%d %HZ}", flush=True)

    # 1) Read every inventory so we know which forecast hours really exist.
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        idxs = {fh: rows for fh, rows in ex.map(lambda f: get_idx(cycle, f), FHOURS) if rows}
    if not idxs:
        raise RuntimeError("no NBM inventories found")
    hours = sorted(idxs)
    skipped = [f for f in FHOURS if f not in idxs]
    if skipped:
        print(f"  forecast hours not in this run (skipped): {skipped}")

    # 2) Choose messages, matching precip windows to the real spacing between files.
    sels = {fh: select_messages(idxs[fh], fh, hours[i - 1] if i else 0) for i, fh in enumerate(hours)}

    # 3) Download only those byte ranges, 8 files at a time.
    files = {}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for fh, res in ex.map(lambda f: download(cycle, f, sels[f]), hours):
            files[fh] = res
    print(f"  downloaded {len(files)} forecast hours", flush=True)

    rg = None
    grids = {k: [] for k in FIELDS}
    times, qwin, popwin = [], [], []
    for fh in hours:
        raw = files[fh]["raw"]
        dec = {}
        for key, msg in raw.items():
            vals, lats, lons = decode_grib(msg, latlons=rg is None)
            if rg is None:
                print("  building regridder ...", flush=True)
                rg = Regridder(lats, lons)
            dec[key] = vals
        if rg is None:
            continue
        nan = np.full((rg.ny, rg.nx), np.nan, dtype=np.float32)
        g = lambda k: rg.scalar(dec[k]) if k in dec else nan

        mh_ft = g("MIXHT") * M_TO_FT
        if "TRWSPD" in dec and "TRWDIR" in dec:
            tw_ms, twd = rg.vector(dec["TRWSPD"], dec["TRWDIR"])
        else:
            tw_ms, twd = nan, nan
        tw_kt = tw_ms * MS_TO_KT
        if "WIND" in dec and "WDIR" in dec:
            ws_ms, wd = rg.vector(dec["WIND"], dec["WDIR"])
        else:
            ws_ms, wd = nan, nan

        grids["mh"].append(mh_ft)
        grids["tw"].append(tw_kt)
        grids["twd"].append(twd)
        grids["vi"].append(mh_ft * tw_kt)                     # knot-feet
        grids["pop"].append(g("POP"))
        grids["qpf"].append(g("QPF") / 25.4)                   # mm -> in
        grids["ws"].append(ws_ms * MS_TO_MPH)
        grids["wd"].append(wd)
        grids["wg"].append(g("GUST") * MS_TO_MPH)
        grids["t"].append((g("TMP") - 273.15) * 9 / 5 + 32)
        grids["rh"].append(g("RH"))
        times.append(int((cycle + dt.timedelta(hours=fh)).timestamp()))
        qwin.append(files[fh]["qwin"])
        popwin.append(files[fh]["popwin"])

    # Encode each field as a PNG strip (one band per forecast hour).
    fields_meta = {}
    for k, (scale, offset, units) in FIELDS.items():
        stack = np.stack(grids[k])                              # (nt, ny, nx)
        q = np.round((stack - offset) / scale)
        q = np.where(np.isfinite(q), np.clip(q, 0, MISSING - 1), MISSING).astype(np.uint16)
        write_png16(os.path.join(out, f"f_{k}.png"), q.reshape(-1, rg.nx))
        fields_meta[k] = {"file": f"f_{k}.png", "scale": scale, "offset": offset, "units": units}
        print(f"  f_{k}.png ({os.path.getsize(os.path.join(out, f'f_{k}.png')) / 1024:.0f} KB)")

    write_json(out, "manifest.json", {
        "generated": int(time.time()),
        "source": "NOAA National Blend of Models (NBM) CONUS core",
        "cycle": cycle.strftime("%Y-%m-%dT%H:00Z"),
        "cycle_epoch": int(cycle.timestamp()),
        "times": times,
        "qpf_window_h": qwin,
        "pop_window_h": popwin,
        "grid": {"west": BBOX["west"], "north": BBOX["north"], "step": STEP, "nx": rg.nx, "ny": rg.ny},
        "missing": MISSING,
        "fields": fields_meta,
    })
    return len(times)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_site/data")
    args = ap.parse_args()
    out = args.out
    os.makedirs(out, exist_ok=True)
    meta = {"sources": {}}

    def run(key, fn, label, required=False):
        try:
            n = fn(out)
            meta["sources"][key] = {"label": label, "updated": int(time.time()), "count": n, "ok": True}
        except Exception as e:
            print(f"!! {key} failed: {e}", file=sys.stderr, flush=True)
            meta["sources"][key] = {"label": label, "ok": False, "error": str(e)[:300]}
            if required:
                raise
            # Write an empty layer so the page still loads.
            write_json(out, f"{key}.geojson", {"type": "FeatureCollection", "features": []})

    run("gacc", fetch_gaccs, "NIFC National GACC Boundaries")
    run("perimeters", fetch_perimeters, "NIFC WFIGS Current Interagency Fire Perimeters")
    # The forecast is required: if NBM fails the job fails and the previous site stays live.
    run("forecast", fetch_forecast, "NOAA NBM CONUS", required=True)
    meta["last_run"] = int(time.time())
    write_json(out, "meta.json", meta)


if __name__ == "__main__":
    main()
