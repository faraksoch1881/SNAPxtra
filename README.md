# SNAPxtra — Sentinel-1 InSAR Workflow

**SNAPxtra** builds interferometric stacks from **Sentinel-1** data using [ESA SNAP](http://step.esa.int/main/download/snap-download/), [SNAPHU](https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/), and Python automation.

| Mode | Output | Post-processing |
|------|--------|-----------------|
| **SBAS** | Multi-pair interferogram network | [LiCSBAS](https://github.com/yumorishita/LiCSBAS) or [StaMPS](https://github.com/dbekaert/StaMPS) |
| **PS** | Master-centred star network | [StaMPS](https://github.com/dbekaert/StaMPS) |

Run **step-by-step** or in **batch mode**. Export products for time-series analysis and deformation measurements.

**Main script:** `stampxtra_main.py`  
**Dependency check:** `check_install.py`  
**Sample config:** `insar_proj_sample.config` or `param_proj.config`

---

## Table of contents

- [What SNAPxtra does](#what-snapxtra-does)
- [Getting started](#getting-started)
- [Workflow overview](#workflow-overview)
- [Configuration](#configuration)
- [How to run](#how-to-run)
- [Processing steps](#processing-steps)
- [Outputs](#outputs)

---

## What SNAPxtra does

- Downloads or uses local Sentinel-1 `*.zip` SLCs
- Builds SBAS or PS interferogram networks
- Coregisters, debursts, merges swaths, subsets to AOI
- Unwraps with SNAPHU and exports GeoTIFFs (**LiCSBAS**) or StaMPS folders (**StaMPS**)

---

## Getting started

### 1. Install SNAP

Download and install from the [ESA SNAP website](http://step.esa.int/main/download/snap-download/).

Note the path to `gpt`, e.g.:

```text
/home/user/esa-snap/bin/gpt
```

### 2. Install SNAPHU

1. Download SNAPHU and unzip  
2. Note the folder that contains `bin/snaphu`  
3. Set `snaphu_bin=` to that folder in your config

### 3. Download SNAPxtra

Clone or unzip this repository. Note the folder containing:

- `stampxtra_main.py
- `sbas_pair_builder.py`
- SNAP graph XML files (set `graph_path=` to this folder)

Put all required `.xml` graphs in **one** directory (`graph_path`). You can merge `sbas_graph/` and `multi_graph/` contents into one folder.

### 4. Create a config file

Copy the sample config and edit paths:

```bash
cp insar_proj_sample.config param_proj.config
```

### 5. Validate installation

```bash
python check_install.py param_proj.config
python check_install.py param_proj.config -y    # auto-install missing Python packages
```

When you see:

```text
✓ All dependencies satisfied!
ℹ Your system is ready for SBAS processing.
```

you can start processing.

---

## Workflow overview

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ Download S1     │────▶│ Edit config      │────▶│ sbas_check_     │
│ (ASF, etc.)     │     │ param_proj.config│     │ install.py      │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                        ┌──────────────────┐              ▼
                        │ Step 0: baselines│◀─── python ps_sbas_snapxtra.py config baselines
                        │ sbas_pairs.txt   │
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │ Batch or step    │
                        │ processing       │
                        └────────┬─────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
         GEOC/ (LiCSBAS)   Stamps_sbas/        Stamps_ps/
```

### Download Sentinel-1 data

- Portal: [ASF Data Search](https://search.asf.alaska.edu/)
- Place `*.zip` files in `input_data` (or set `input_data=` to the download folder)

---

## Configuration

Config files use `key=value` format (see `param_proj.config`).

### Required parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `orbit` | Track direction | `Ascending` or `Descending` |
| `input_data` | Folder with Sentinel-1 `*.zip` files | `/data/s1/downloads/` |
| `output_dir` | Project output root (optional; defaults to `input_data`) | `/data/s1/projects/` |
| `clip_roi` | AOI extent | `west/east/south/north` e.g. `-117.5/-117.0/33.5/34.0` or WKT `POLYGON((...))` |
| `snaphu_bin` | SNAPHU install folder (contains `bin/snaphu`) | `~/.snap/auxdata/snaphu-v2.0.4_linux/` |
| `gpt_loc` | SNAP `gpt` executable | `/home/user/esa-snap/bin/gpt` |
| `graph_path` | Directory with all SNAP workflow `.xml` files | `/path/to/multi_graphs/` |

### Workflow target

| Parameter | Value | Result |
|-----------|-------|--------|
| `insar_target` | `1` | Export for **LiCSBAS** (SBAS only) → `GEOC/` |
| `insar_target` | `2` | Export for **StaMPS** → `Stamps_sbas/` or `Stamps_ps/` |
| `insar_method` | `1` | **SBAS** network (used when `insar_target=2`) |
| `insar_method` | `2` | **PS** star network (only when `insar_target=2`) |

### Optional — quick test run

| Parameter | Default | Description |
|-----------|---------|-------------|
| `check_only` | `false` | If `true`, processes only **3 pairs** (first, middle, last) through all steps as a dry run before the full dataset |

### Optional — AOI & swath auto-detection

| Parameter | Default | Description |
|-----------|---------|-------------|
| `burst_check` | `true` | Uses AOI footprint to find swaths with overlap ≥ `swath_threshold` |
| `swath_threshold` | `10` | Minimum overlap (%) between AOI and swath footprint |
| `swath` | — | IW selection (`1`, `2`, `3`, `12`, `23`, `0` = all). Auto-updated when `burst_check=true` |
| `l_burst` / `u_burst` | — | Burst range (1-based). Auto-updated when `burst_check=true` |

When `burst_check=true`, `swath`, `l_burst`, and `u_burst` are written back to the config before processing. Set `burst_check=false` to use manual values only.

### Optional — unwrap mask (vector AOI)

| Parameter | Default | Description |
|-----------|---------|-------------|
| `msk_shp` | *(empty)* | Path to mask file (shapefile; prepare in QGIS or Google Earth). Limits unwrap to high-coherence areas |
| `invert_mask` | `false` | `false` = mask **outside** the boundary; `true` = mask **inside** |

If `msk_shp` is empty, no vector mask is applied and the full scene is unwrapped.

### Optional — DEM

| Parameter | Default | Description |
|-----------|---------|-------------|
| `demName` | `Copernicus 30m Global DEM` | Built-in DEM (`SRTM 3Sec`, etc.) when no external DEM |
| `externalDEMFile` | *(empty)* | Path to local GeoTIFF DEM. Use with `demName=External DEM` |

### Optional — master date

| Parameter | Default | Description |
|-----------|---------|-------------|
| `master` | *(empty)* | Super-master date `YYYYMMDD`. If empty, SNAP InSAR-Overview selects it at step 0 |

### SBAS pair selection

| Parameter | Default | Description |
|-----------|---------|-------------|
| `temporal` | `90` | Max temporal baseline between pair dates (days) |
| `baseline` | `200` | Max perpendicular baseline (metres) |
| `max_loop` | `6` | Max connections per date (nearest neighbours) |
| `no_loop` | `1` | `1` = pair must belong to at least one triangle (phase closure) |
| `connect_network` | `true` | Bridge disconnected network components |

**`no_loop=1`** — keeps pairs that form triangles (A–B, B–C, A–C) and critical bridge edges.

**`connect_network=true`** — if filtering splits the network, bridging pairs connect component *i* (latest date) to component *i+1* (earliest date). Saved in `connect_sb.txt`.

### Multilook & SNAPHU

| Parameter | Default | Description |
|-----------|---------|-------------|
| `Range` | `20` | Range looks |
| `Azimuth` | `4` | Azimuth looks |
| `phase_coh_mask_th` | `0.1` | Pixels with coherence below this are masked before unwrap |
| `cost_mode` | `SMOOTH` | SNAPHU cost mode (`DEFO`, `SMOOTH`, `TOPO`, …) |
| `init_method` | `MCF` | SNAPHU init (`MCF`, `MST`, …) |

### Parallelization & GPT memory

| Parameter | Default | Description |
|-----------|---------|-------------|
| `parallel` | `true` | Enables GPT internal threading (`-q`) |
| `threads` | `8` | Passed to GPT as `-q <threads>` when `parallel=true` |
| `cache_size` | `16G` | GPT tile cache (`-c`); suffix `G` / `M` / `K` supported |

**GPT flags when `parallel=true`:**

```bash
gpt -q 8 -c 16G <graph-or-operator> ...
```

- **`-q`** — SNAP native threads **inside one** `gpt` job (jobs run **one at a time**)
- **`-c`** — tile cache per job (must fit in available RAM)

When `parallel=false`, jobs run sequentially without `-q`.

### Step range & logging

| Parameter | Default | Description |
|-----------|---------|-------------|
| `start_step` | `0` | First step (used when no `-s` on CLI) |
| `end_step` | *(blank)* | Last step; blank = 24 (LiCSBAS) or 27 (StaMPS) |
| `savelog` | `debug` | `release` = short summary log; `debug` = full verbose log |

---

## How to run

### Step 0 — baselines & network (always run first)

```bash
python ps_sbas_snapxtra.py param_proj.config baselines
```

Creates:

- `metadata_info/baselines`
- `metadata_info/sbas_pairs.txt`
- `metadata_info/network_sbas.png` (or `network_ps.png`)

### Full batch

```bash
python ps_sbas_snapxtra.py param_proj.config
```

Uses `start_step` and `end_step` from the config.

### Single step

```bash
python ps_sbas_snapxtra.py 7 param_proj.config
```

### Override step range (CLI writes config, then runs)

| Command | Behaviour |
|---------|-----------|
| `python ps_sbas_snapxtra.py config -s 3 -e 10` | Steps 3–10 (ignores config `start_step` / `end_step`) |
| `python ps_sbas_snapxtra.py config -s 3` | Step 3 through workflow end |
| `python ps_sbas_snapxtra.py config` | Uses `start_step` / `end_step` from config |

### Override target & method (CLI)

| Command | Mode |
|---------|------|
| `python ps_sbas_snapxtra.py config -t 1 -m 1` | LiCSBAS SBAS |
| `python ps_sbas_snapxtra.py config -t 2 -m 1` | StaMPS SBAS |
| `python ps_sbas_snapxtra.py config -t 2 -m 2` | StaMPS PS |

Example — StaMPS SBAS, steps 3–10:

```bash
python ps_sbas_snapxtra.py param_proj.config -s 3 -e 10 -t 2 -m 1
```

CLI `-t` / `-m` override `insar_target` and `insar_method` in the config.

> `-s`, `-e`, `-t`, `-m` apply to **batch mode only** (not with `baselines` or `<step_number> <config>`).

### Minimal config example

```ini
orbit=Ascending
input_data=/data/s1/zip/
output_dir=/data/s1/project/
clip_roi=-117.5/-117.0/33.5/34.0
snaphu_bin=/home/user/snaphu-v2.0.4_linux/
gpt_loc=/home/user/esa-snap/bin/gpt
graph_path=/home/user/SNAPxtra/multi_graphs/

insar_target=1
insar_method=1
burst_check=true
parallel=true
threads=8
cache_size=16G
temporal=90
baseline=200
```

---

## Processing steps

| Step | Name | Notes |
|------|------|-------|
| 0 | Baselines & SBAS network | `baselines` mode |
| 1 | AOI & swath setup | WKT / w,e,s,n AOI |
| 2 | Prepare slices | Per IW |
| 3 | Coregistered stack | |
| 4 | Land–sea mask | |
| 5 | TOPSAR deburst | |
| 6 | Add elevation | StaMPS path |
| 7 | Multi-interferograms | |
| 8 | Merge swaths | Multi-IW |
| 9 | Subset interferograms | When `clip_roi` set |
| 10–11 | Vector AOI mask | Optional `msk_shp` |
| 12–13 | Bandmath / PS filter | StaMPS |
| 14 | Multilook | `Range`, `Azimuth` |
| 15–17 | StaMPS coreg merge/subset/mask | |
| 18 | Goldstein filter | LiCSBAS |
| 19–22 | SNAPHU export → unwrap → import | |
| 23–24 | Terrain correction → GeoTIFF | LiCSBAS `GEOC/` |
| 25–27 | StaMPS coreg ML + export | `Stamps_sbas/` / `Stamps_ps/` |

**Default end step:** 24 (LiCSBAS), 27 (StaMPS)

---

## Outputs

| Path | Content |
|------|---------|
| `proj_asc/` or `proj_dsc/` | Project root |
| `metadata_info/sbas_pairs.txt` | SBAS pair list |
| `metadata_info/connect_sb.txt` | Bridging pairs (if any) |
| `GEOC/` | LiCSBAS GeoTIFFs (`*.geo.unw.tif`, `*.geo.cc.tif`) |
| `Stamps_sbas/` | StaMPS SBAS export |
| `Stamps_ps/` | StaMPS PS export |
| `log/` | Session logs |
| `snaphu_error.log` | Failed unwrap pairs |

---


## Requirements summary

| Component | Notes |
|-----------|-------|
| Python 3.8+ | `numpy`, `shapely`, `matplotlib`, `gdal`, `psutil`, `rasterio`, `scipy` |
| SNAP + GPT | Sentinel-1 Toolbox |
| SNAPHU | Phase unwrapping |
| GDAL / GMT | GeoTIFF export (LiCSBAS path) |
| Graph XMLs | All under `graph_path` |

Run `python check_install.py <config>` to verify.

---

## Related links

- [ESA SNAP](http://step.esa.int/main/download/snap-download/)
- [SNAPHU](https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/)
- [ASF Data Search](https://search.asf.alaska.edu/)
- [LiCSBAS](https://github.com/yumorishita/LiCSBAS)
- [StaMPS](https://github.com/dbekaert/StaMPS)
