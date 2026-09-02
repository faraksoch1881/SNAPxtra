#!/usr/bin/env python3
"""
dem_stamps_prep.py
------------------
1. Parse GAMMA-format projected_dem.par to get UTM extent
2. Convert UTM extent to WGS84 lat/lon with buffer
3. Download DEM from OpenTopography API
4. Reproject to WGS84 geographic if needed
5. Convert to float32 raw binary (StaMPS format)
6. Write demparms.in (filename only, no full path)

Requirements:
    pip install gdal numpy requests

Usage:
    python dem_stamps_prep.py --geo projected_dem.par [--dem_api YOUR_API_KEY] [--outdir ./]

Arguments:
    --geo      : GAMMA DEM parameter file (projected_dem.par)
    --dem_api  : OpenTopography API key (optional, falls back to default)
    --outdir   : Output directory for dem.raw and demparms.in (default: current dir)
    --demtype  : DEM type: SRTMGL1 (30m), SRTMGL3 (90m), COP30 (default: COP30)
    --buffer   : Degree buffer around extent (default: 0.1)
"""

import sys
import os
import math
import argparse
import requests
from datetime import datetime
import numpy as np

try:
    from osgeo import gdal, osr
    gdal.UseExceptions()
except ImportError:
    raise ImportError("GDAL Python bindings required:  conda install gdal  or  pip install gdal")


# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_API_KEY = "ae0dcd6ceeecbf5ada9579d49e16b3a9"
OT_URL          = "https://portal.opentopography.org/API/globaldem"
# ─────────────────────────────────────────────────────────────────────────────


# ── 1. Parse GAMMA .par file ─────────────────────────────────────────────────
def parse_gamma_par(par_path):
    """Read key:value pairs from a GAMMA parameter file."""
    params = {}
    with open(par_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('%') or line.startswith('#'):
                continue
            if ':' in line:
                key, _, val = line.partition(':')
                params[key.strip()] = val.strip()
    return params


# ── 2. UTM -> WGS84 (no external library) ───────────────────────────────────
def utm_to_latlon(easting, northing, zone, northern=True):
    """Convert UTM easting/northing to WGS84 lat/lon (degrees)."""
    a  = 6378137.0
    f  = 1 / 298.257223563
    b  = a * (1 - f)
    e2 = 1 - (b / a) ** 2
    k0 = 0.9996
    E0 = 500000.0
    N0 = 0.0 if northern else 10000000.0

    x  = easting  - E0
    y  = northing - N0

    M   = y / k0
    mu  = M / (a * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    e1  = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

    phi = (mu
           + (3*e1/2        - 27*e1**3/32)   * math.sin(2*mu)
           + (21*e1**2/16   - 55*e1**4/32)   * math.sin(4*mu)
           + (151*e1**3/96)                   * math.sin(6*mu)
           + (1097*e1**4/512)                 * math.sin(8*mu))

    N1 = a / math.sqrt(1 - e2 * math.sin(phi)**2)
    T1 = math.tan(phi)**2
    C1 = e2 / (1 - e2) * math.cos(phi)**2
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi)**2)**1.5
    D  = x / (N1 * k0)

    lat = phi - (N1 * math.tan(phi) / R1) * (
          D**2/2
          - (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*e2/(1-e2)) * D**4/24
          + (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*e2/(1-e2) - 3*C1**2) * D**6/720)

    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    lon  = lon0 + (
           D
           - (1 + 2*T1 + C1) * D**3/6
           + (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*e2/(1-e2) + 24*T1**2) * D**5/120
           ) / math.cos(phi)

    return math.degrees(lat), math.degrees(lon)


# ── 3. Compute WGS84 extent from .par ────────────────────────────────────────
def get_wgs84_extent(params, buffer=0.1):
    """
    Derive the 4 UTM corners from par metadata,
    convert to WGS84, return (west, east, south, north) with buffer.
    """
    ul_e     = float(params['corner_east'])
    ul_n     = float(params['corner_north'])
    post_e   = float(params['post_east'])    # metres per pixel east
    post_n   = float(params['post_north'])   # metres per pixel north
    width    = int(params['width'])
    nlines   = int(params['nlines'])
    zone     = int(params['projection_zone'])
    northern = float(params.get('false_northing', 0)) == 0.0

    # Lower-right corner
    lr_e = ul_e + (width  - 1) * post_e
    lr_n = ul_n - (nlines - 1) * post_n

    corners = [
        ("NW", ul_e, ul_n),
        ("NE", lr_e, ul_n),
        ("SW", ul_e, lr_n),
        ("SE", lr_e, lr_n),
    ]

    print("\n  UTM corners → WGS84:")
    lats, lons = [], []
    for name, e, n in corners:
        lat, lon = utm_to_latlon(e, n, zone, northern)
        lats.append(lat); lons.append(lon)
        print(f"    {name}: E={e:.2f} N={n:.2f}  →  lon={lon:.6f} lat={lat:.6f}")

    west  = min(lons);  east  = max(lons)
    south = min(lats);  north = max(lats)

    print(f"\n  Exact extent  (W,E,S,N): {west:.4f}, {east:.4f}, {south:.4f}, {north:.4f}")
    west  -= buffer;  east  += buffer
    south -= buffer;  north += buffer
    print(f"  +{buffer}° buffer      : {west:.4f}, {east:.4f}, {south:.4f}, {north:.4f}")

    return west, east, south, north


# ── 4. Download from OpenTopography ──────────────────────────────────────────
def download_dem(west, east, south, north, api_key, demtype, tif_path):
    """Download DEM GeoTIFF from OpenTopography API."""
    params = {
        'demtype'     : demtype,
        'south'       : round(south, 6),
        'north'       : round(north, 6),
        'west'        : round(west,  6),
        'east'        : round(east,  6),
        'outputFormat': 'GTiff',
        'API_Key'     : api_key,
    }
    print(f"\n  Downloading {demtype} DEM from OpenTopography...")
    print(f"    Extent: W={params['west']} E={params['east']} "
          f"S={params['south']} N={params['north']}")
    print(f"    API key: {api_key[:6]}{'*'*10}")

    resp = requests.get(OT_URL, params=params, stream=True, timeout=300)

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenTopography download failed: HTTP {resp.status_code}\n{resp.text[:500]}")

    content_type = resp.headers.get('Content-Type', '')
    if 'json' in content_type or 'html' in content_type:
        raise RuntimeError(f"API returned error response:\n{resp.text[:500]}")

    total = int(resp.headers.get('content-length', 0))
    downloaded = 0
    with open(tif_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=65536):
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded / total * 100
                print(f"\r    Progress: {downloaded/1e6:.1f} / {total/1e6:.1f} MB  ({pct:.0f}%)",
                      end='', flush=True)
    print(f"\n  Downloaded: {tif_path}  ({os.path.getsize(tif_path)/1e6:.1f} MB)")


# ── 5. Reproject to WGS84 geographic if needed ───────────────────────────────
def reproject_if_needed(src_path, tmp_path):
    ds  = gdal.Open(src_path)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(ds.GetProjection())
    wgs84 = osr.SpatialReference()
    wgs84.ImportFromEPSG(4326)
    ds = None

    if srs.IsSame(wgs84):
        print("  CRS: already WGS84 geographic — no reprojection needed.")
        return src_path

    auth = srs.GetAuthorityCode(None)
    print(f"  CRS: EPSG:{auth} — reprojecting to WGS84...")
    gdal.Warp(tmp_path, src_path, dstSRS='EPSG:4326',
              resampleAlg='bilinear', format='GTiff')
    print(f"  Reprojected: {tmp_path}")
    return tmp_path


# ── 6. Convert TIF → raw binary + demparms.in ────────────────────────────────
def tif_to_stamps_dem(tif_path, output_dir, raw_filename):
    os.makedirs(output_dir, exist_ok=True)

    tmp_reproj = os.path.join(output_dir, '_tmp_wgs84.tif')
    tif_use    = reproject_if_needed(tif_path, tmp_reproj)

    ds = gdal.Open(tif_use)
    if ds is None:
        raise FileNotFoundError(f"GDAL could not open: {tif_use}")

    gt         = ds.GetGeoTransform()
    dem_width  = ds.RasterXSize
    dem_length = ds.RasterYSize
    dem_lon1   = gt[0]
    dem_lat2   = gt[3]
    lon_posting = gt[1]
    lat_posting = abs(gt[5])
    dem_lon2   = dem_lon1 + (dem_width  - 1) * lon_posting
    dem_lat1   = dem_lat2 - (dem_length - 1) * lat_posting

    print(f"\n  DEM metadata:")
    print(f"    Size          : {dem_width} cols x {dem_length} rows")
    print(f"    UL corner     : lon={dem_lon1:.6f}  lat={dem_lat2:.6f}")
    print(f"    LR corner     : lon={dem_lon2:.6f}  lat={dem_lat1:.6f}")
    print(f"    Posting       : lon={lon_posting:.8f}°  lat={lat_posting:.8f}°")

    band   = ds.GetRasterBand(1)
    data   = band.ReadAsArray().astype(np.float32)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        data[data == nodata] = 0.0
    data = np.nan_to_num(data, nan=0.0)
    ds   = None

    print(f"    Elevation     : {data.min():.1f} m  to  {data.max():.1f} m")

    # ── Write raw binary ──────────────────────────────────────────────────────
    raw_path = os.path.join(output_dir, raw_filename)
    data.tofile(raw_path)
    print(f"\n  Raw binary : {raw_path}  ({os.path.getsize(raw_path)/1e6:.1f} MB)")

    # ── Write demparms.in (filename only, not full path) ─────────────────────
    demparms_path = os.path.join(output_dir, 'demparms.in')
    posting_str   = f"{lon_posting:.10f} {lat_posting:.10f}"

    with open(demparms_path, 'w') as f:
        f.write(f"{raw_filename}\n")          # ← filename only
        f.write(f"{dem_width}\n")
        f.write(f"{dem_length}\n")
        f.write(f"{dem_lon1:.10f}\n")
        f.write(f"{dem_lat2:.10f}\n")
        f.write(f"{posting_str}\n")
        f.write("real4\n")

    print(f"  demparms.in: {demparms_path}")
    print(f"\n  demparms.in contents:")
    print(f"  {'─'*48}")
    labels = ['dem file ', 'width    ', 'length   ',
              'UL lon   ', 'UL lat   ', 'posting  ', 'format   ']
    with open(demparms_path) as f:
        for label, line in zip(labels, f):
            print(f"    [{label}] {line}", end='')
    print(f"\n  {'─'*48}")

    # ── Clean up temp reprojection ────────────────────────────────────────────
    if os.path.exists(tmp_reproj):
        os.remove(tmp_reproj)

    return demparms_path, raw_path


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Prepare DEM for StaMPS ps_load_dem.m from GAMMA .par file')
    parser.add_argument('--geo',     required=True,
                        help='GAMMA DEM parameter file (e.g. projected_dem.par)')
    parser.add_argument('--dem_api', default=DEFAULT_API_KEY,
                        help=f'OpenTopography API key (default: built-in key)')
    parser.add_argument('--outdir',  default='.',
                        help='Output directory for dem.raw and demparms.in (default: .)')
    parser.add_argument('--demtype', default='COP30',
                        choices=['COP30', 'SRTMGL1', 'SRTMGL3'],
                        help='DEM type: COP30=Copernicus 30m, SRTMGL1=SRTM 30m, '
                             'SRTMGL3=SRTM 90m (default: COP30)')
    parser.add_argument('--buffer',  default=0.1, type=float,
                        help='Degree buffer around extent (default: 0.1)')
    args = parser.parse_args()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not os.path.exists(args.geo):
        print(f"Error: parameter file not found: {args.geo}")
        sys.exit(1)

    api_key = args.dem_api if args.dem_api else DEFAULT_API_KEY
    outdir  = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)

    print(f"{'='*55}")
    print(f"  DEM Preparation for StaMPS")
    print(f"{'='*55}")
    print(f"  Par file  : {args.geo}")
    print(f"  DEM type  : {args.demtype}")
    print(f"  Output dir: {outdir}")
    print(f"  Buffer    : {args.buffer}°")

    # ── Step 1: parse .par ────────────────────────────────────────────────────
    print(f"\n[1/4] Parsing {args.geo}...")
    params = parse_gamma_par(args.geo)
    print(f"  Projection : {params.get('DEM_projection')} Zone {params.get('projection_zone')}")
    print(f"  Size       : {params['width']} x {params['nlines']} pixels")
    print(f"  Post       : {params['post_east']} m x {params['post_north']} m")

    # ── Step 2: compute WGS84 extent ─────────────────────────────────────────
    print(f"\n[2/4] Computing WGS84 extent...")
    west, east, south, north = get_wgs84_extent(params, buffer=args.buffer)

    # ── Step 3: download DEM ──────────────────────────────────────────────────
    print(f"\n[3/4] Downloading DEM...")
    tif_path = os.path.join(outdir, f'dem_download_{args.demtype}.tif')
    download_dem(west, east, south, north, api_key, args.demtype, tif_path)

    # ── Step 4: convert to StaMPS format ─────────────────────────────────────
    print(f"\n[4/4] Converting to StaMPS raw binary...")
    timestamp    = datetime.now().strftime("D%Y%m%dT%H%M%S")
    raw_filename = f"{timestamp}.raw"
    print(f"  Output filename: {raw_filename}")

    demparms_path, raw_path = tif_to_stamps_dem(tif_path, outdir, raw_filename)

    print(f"""
{'='*55}
  Done!

  Files written to: {outdir}
    {raw_filename}
    demparms.in

  Next steps:
    cp {demparms_path} <STAMPS_DIR>/SMALL_BASELINES/
    cp {raw_path} <STAMPS_DIR>/SMALL_BASELINES/

  Then in MATLAB (from SMALL_BASELINES/):
    ps_load_dem
    ps_plot('v', 1)
{'='*55}
""")


if __name__ == "__main__":
    main()