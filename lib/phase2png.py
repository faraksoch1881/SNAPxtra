"""
Render SNAP Phase*.img rasters to PNG previews.

Each pair folder under --dir contains a *.data directory with Phase*.img
(ENVI float32). Wrapped phase (values inside ±π) is drawn with a cyclic hsv
colormap. Phase outside ±π is drawn as unwrapped phase with turbo.

Images are written to --out, or to <parent of --dir>/preview_unw/ when --out is omitted.

--size n (default) writes the original PNG preview.
--size y writes a small JPEG (long side about 1200 px) for a quick look.

Example
-------
python phase2png.py --dir /project/wang/sagar/proj_insar/houston_ps/2024_2026/proj_dsc/merge_IW1_IW2/multi_intf_deb_mrg_bandmath_ps_20250315/ --size y

writes
  .../proj_dsc/merge_IW1_IW2/preview_unw/Phase_ifg_VV_*.jpg
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

PREVIEW_MAX_SIDE = 1200
JPEG_QUALITY = 40

ENVI_DTYPE = {
    "1": "u1",
    "2": "i2",
    "3": "i4",
    "4": "f4",
    "5": "f8",
    "12": "u2",
    "13": "u4",
    "14": "i8",
    "15": "u8",
}


def read_envi_hdr(hdr_path):
    """Parse a simple ENVI header into a dict of string values."""
    meta = {}
    for raw in Path(hdr_path).read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.upper() == "ENVI" or "=" not in line:
            continue
        key, value = line.split("=", 1)
        meta[key.strip().lower()] = value.strip().strip("{}").strip()
    samples = int(meta["samples"])
    lines = int(meta["lines"])
    dtype_code = meta.get("data type", "4")
    if dtype_code not in ENVI_DTYPE:
        raise ValueError(f"unsupported ENVI data type {dtype_code} in {hdr_path}")
    byte_order = meta.get("byte order", "1")
    endian = ">" if byte_order == "1" else "<"
    dtype = np.dtype(endian + ENVI_DTYPE[dtype_code])
    return lines, samples, dtype


def find_phase_imgs(root):
    """Phase*.img inside each pair-folder *.data directory."""
    imgs = []
    for pair_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        data_dirs = sorted(
            d for d in pair_dir.iterdir() if d.is_dir() and d.suffix == ".data"
        )
        for data_dir in data_dirs:
            imgs.extend(sorted(data_dir.glob("Phase*.img")))
    return imgs


def load_phase(img_path, stride):
    hdr_path = img_path.with_suffix(".hdr")
    if not hdr_path.is_file():
        raise FileNotFoundError(f"missing ENVI header: {hdr_path}")
    lines, samples, dtype = read_envi_hdr(hdr_path)
    mm = np.memmap(img_path, dtype=dtype, mode="r", shape=(lines, samples))
    data = np.array(mm[::stride, ::stride], dtype=np.float32)
    del mm
    return data


def stride_for_size(img_path, stride, small):
    """Keep --stride when --size n. When --size y, coarsen to a brief preview."""
    if not small:
        return stride
    lines, samples, _ = read_envi_hdr(img_path.with_suffix(".hdr"))
    need = max(1, int(np.ceil(max(lines, samples) / PREVIEW_MAX_SIDE)))
    return max(stride, need)


def render_png(data, out_path, small=False):
    nodata = ~np.isfinite(data) | (data == 0.0)
    valid = data[~nodata]
    if valid.size == 0:
        raise ValueError(f"no valid phase pixels in {out_path.name}")

    wrapped = float(np.max(np.abs(valid))) <= (np.pi + 1e-3)
    masked = np.ma.array(data, mask=nodata)
    if wrapped:
        cmap = plt.get_cmap("hsv").copy()
        vmin, vmax = -np.pi, np.pi
        label = "wrapped phase (rad)"
        ticks = [-np.pi, -np.pi / 2, 0, np.pi / 2, np.pi]
        ticklabels = [r"$-\pi$", r"$-\pi/2$", "0", r"$\pi/2$", r"$\pi$"]
    else:
        cmap = plt.get_cmap("turbo").copy()
        lo, hi = np.percentile(valid, [2, 98])
        vmin, vmax = float(lo), float(hi)
        if vmin == vmax:
            vmin, vmax = float(valid.min()), float(valid.max())
        label = "unwrapped phase (rad)"
        ticks = None
        ticklabels = None
    cmap.set_bad((1, 1, 1, 1))

    h, w = data.shape
    dpi = 100
    fig, ax = plt.subplots(figsize=(w / dpi, h / dpi), dpi=dpi)
    im = ax.imshow(
        masked,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        interpolation="nearest",
        aspect="equal",
        origin="upper",
    )
    ax.set_axis_off()
    cbar = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cbar.set_label(label)
    if ticks is not None:
        cbar.set_ticks(ticks)
        cbar.set_ticklabels(ticklabels)
    save_kw = {"bbox_inches": "tight", "pad_inches": 0.15}
    if small:
        save_kw["pil_kwargs"] = {"quality": JPEG_QUALITY, "optimize": True}
    fig.savefig(out_path, **save_kw)
    plt.close(fig)
    return "wrapped" if wrapped else "unwrapped"


def main():
    parser = argparse.ArgumentParser(
        description="Convert Phase*.img rasters under --dir to PNG previews."
    )
    parser.add_argument(
        "--dir",
        required=True,
        help="folder whose children are date-pair directories containing *.data/Phase*.img",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=4,
        help="nearest-neighbor subsample step (default 4; do not average wrapped phase)",
    )
    parser.add_argument(
        "--size",
        choices=("y", "n"),
        default="n",
        help="n: original PNG (default). y: small low-quality JPEG preview",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output folder (default: <parent of --dir>/preview_unw)",
    )
    args = parser.parse_args()
    if args.stride < 1:
        parser.error("--stride must be >= 1")

    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        out_dir = root.parent / "preview_unw"
    out_dir.mkdir(parents=True, exist_ok=True)

    imgs = find_phase_imgs(root)
    if not imgs:
        raise SystemExit(f"no Phase*.img found under {root}")

    small = args.size == "y"
    suffix = ".jpg" if small else ".png"
    print(f"output: {out_dir}  ({'small jpg' if small else 'png'})")
    for img_path in imgs:
        stride = stride_for_size(img_path, args.stride, small)
        out_path = out_dir / (img_path.stem + suffix)
        data = load_phase(img_path, stride)
        kind = render_png(data, out_path, small=small)
        print(f"wrote {out_path.name}  ({kind}, {data.shape[1]}x{data.shape[0]})")


if __name__ == "__main__":
    main()
