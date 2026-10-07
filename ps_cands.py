#!/usr/bin/env python3
"""Count StaMPS PS candidates from big-endian FCOMPLEX rslc/*.rslc files.

Amplitude dispersion matches mt_prep_snap / selpsc_patch:
  amp_i = |SLC_i| / mean(|SLC_i|)   (mean ignores amplitudes <= 0.001)
  DA    = sigma(amp) / mean(amp)    (population std, divide by N)
Pixels with amplitude <= 0.001 in any SLC are dropped.

The dispersion grid is cached as ps_cands_da.npy next to the rslc directory,
so a later threshold is only a count.

Examples:
  ps_cands.py 0.35
  ps_cands.py 0.4
  ps_cands.py /path/to/INSAR_20241226 0.35 0.4
  ps_cands.py /path/to/rslc 0.42
"""
import argparse
import os
import sys
import glob
import numpy as np

DEFAULT_INSAR = (
    "/home/sagar/sdb/aoi_kml/pak/hcp_dir/surat_mF/2024-2025/"
    "proj_asc/Stamps_ps/INSAR_20241226"
)
AMP_FLOOR = 0.001


def _par_int(par_path, key):
    with open(par_path) as fh:
        for line in fh:
            if line.startswith(key + ":"):
                return int(line.split(":", 1)[1].split()[0])
    raise SystemExit(f"missing {key} in {par_path}")


def resolve_rslc(path):
    path = os.path.abspath(path)
    if os.path.isdir(os.path.join(path, "rslc")):
        return os.path.join(path, "rslc")
    if os.path.isdir(path) and glob.glob(os.path.join(path, "*.rslc")):
        return path
    raise SystemExit(f"no rslc/*.rslc under {path}")


def load_amp(path, nlines, width):
    be = np.fromfile(path, dtype=">f4")
    expect = nlines * width * 2
    if be.size != expect:
        raise SystemExit(f"{path}: {be.size} floats, expected {expect}")
    re = be[0::2].reshape(nlines, width)
    im = be[1::2].reshape(nlines, width)
    return np.hypot(re, im)


def dispersion(rslc_dir):
    files = sorted(glob.glob(os.path.join(rslc_dir, "*.rslc")))
    if not files:
        raise SystemExit(f"no *.rslc in {rslc_dir}")
    par = files[0] + ".par"
    if not os.path.isfile(par):
        raise SystemExit(f"missing {par}")
    width = _par_int(par, "range_samples")
    nlines = _par_int(par, "azimuth_lines")
    n = len(files)
    print(f"SLCs: {n}   grid: {width} x {nlines}", file=sys.stderr)

    means = np.empty(n, np.float64)
    for i, path in enumerate(files):
        amp = load_amp(path, nlines, width)
        nz = amp > AMP_FLOOR
        if not np.any(nz):
            raise SystemExit(f"all amplitudes ~0 in {path}")
        means[i] = float(amp[nz].mean())
        print(f"  mean {i + 1}/{n} {os.path.basename(path)} {means[i]:.3f}", file=sys.stderr)

    sum_a = np.zeros((nlines, width), np.float64)
    sum_a2 = np.zeros((nlines, width), np.float64)
    cnt = np.zeros((nlines, width), np.uint16)
    for i, path in enumerate(files):
        amp = load_amp(path, nlines, width)
        ca = amp / means[i]
        sum_a += ca
        sum_a2 += ca * ca
        cnt += (amp > AMP_FLOOR)
        print(f"  da {i + 1}/{n}", file=sys.stderr)

    with np.errstate(divide="ignore", invalid="ignore"):
        d2 = n * sum_a2 / (sum_a * sum_a) - 1.0
    ok = (cnt == n) & (sum_a > 0) & (d2 >= 0)
    da = np.full((nlines, width), np.nan, np.float32)
    da[ok] = np.sqrt(d2[ok]).astype(np.float32)
    return da


def cache_path(rslc_dir):
    parent = os.path.dirname(os.path.abspath(rslc_dir.rstrip("/")))
    return os.path.join(parent, "ps_cands_da.npy")


def stamp(rslc_dir):
    files = sorted(glob.glob(os.path.join(rslc_dir, "*.rslc")))
    return tuple((os.path.basename(f), os.path.getmtime(f), os.path.getsize(f)) for f in files)


def load_or_build(rslc_dir, recompute):
    cache = cache_path(rslc_dir)
    meta = cache + ".stamp"
    sig = repr(stamp(rslc_dir))
    if (not recompute) and os.path.isfile(cache) and os.path.isfile(meta):
        with open(meta) as fh:
            if fh.read() == sig:
                print(f"using cache {cache}", file=sys.stderr)
                return np.load(cache)
    da = dispersion(rslc_dir)
    np.save(cache, da)
    with open(meta, "w") as fh:
        fh.write(sig)
    print(f"wrote cache {cache}", file=sys.stderr)
    return da


def main():
    p = argparse.ArgumentParser(description="Count PS candidates below an amplitude-dispersion threshold")
    p.add_argument("args", nargs="+", help="threshold(s), and optional rslc or INSAR directory")
    p.add_argument("--recompute", action="store_true", help="ignore ps_cands_da.npy")
    ns = p.parse_args()

    directory = None
    thresholds = []
    for a in ns.args:
        if os.path.isdir(a):
            directory = a
        else:
            thresholds.append(float(a))
    if not thresholds:
        p.error("give at least one threshold, e.g. 0.4")
    rslc = resolve_rslc(directory or DEFAULT_INSAR)
    da = load_or_build(rslc, ns.recompute)
    finite = np.isfinite(da)
    for thr in thresholds:
        n = int(np.sum(finite & (da < thr)))
        print(f"{n:,}")


if __name__ == "__main__":
    main()
