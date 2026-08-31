# SNAPxtra — Sentinel-1 InSAR Workflow

**SNAPxtra** builds interferometric stacks from **Sentinel-1** data using [ESA SNAP](http://step.esa.int/main/download/snap-download/), [SNAPHU](https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/), and Python automation.
This is cross platform compatible supported by Windows, Ubuntu and Mac.

This code was successfully tested in PC with SNAP V8 and Ubuntu 20.04 with 64 GB RAM and 2 TB Hard disk. 

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
- [Post Processing Guide (LiCSBAS and StaMPS Instructions)](#post-processing-guide)

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



  # Post Processing Guide

**Time-series analysis and deformation estimation after [SNAPxtra](snapxtra_main.py)**


## LiCSBAS workflow

SNAPxtra already produces a geocoded stack in `GEOC/`, so **LiCSBAS Step 01 (download GeoTIFFs) is not required**. Start from multilooking / preparation onward.

### Install

Follow the official guide: **[LiCSBAS2 on GitHub](https://github.com/yumorishita/LiCSBAS2)**

### Batch example (Ubuntu)

Create `job_batch.sh` in your project directory (the folder that contains `GEOC/`):

```bash
#!/bin/bash
set -e

cd /path/to/your/project/proj_dsc   # folder containing GEOC/

# Optional earlier steps (uncomment and adjust as needed):
# LiCSBAS02_ml_prep.py -h          # read defaults with -h before changing parameters
# LiCSBAS02_ml_prep.py -i GEOC -o GEOCml1 --n_para 6
# LiCSBAS05op_clip_unw.py -i GEOCml1 -o GEOCml1clip -g W/E/S/N --n_para 4
# LiCSBAS11_check_unw.py -d GEOCml1clip -t TS_GEOCml1clip

LiCSBAS12_loop_closure.py -d GEOCml1clip -t TS_GEOCml1clip --n_para 6
# Tighter loop-closure threshold example:
# LiCSBAS12_loop_closure.py -d GEOCml1clip -t TS_GEOCml1clip -l 5 --n_para 6

LiCSBAS13_sb_inv.py -d GEOCml1clip -t TS_GEOCml1clip --n_unw_r_thre 0.5 --n_para 6
LiCSBAS14_vel_std.py -t TS_GEOCml1clip
LiCSBAS15_mask_ts.py -t TS_GEOCml1clip
LiCSBAS16_filt_ts.py -t TS_GEOCml1clip --n_para 6
```

Make it executable and run:

```bash
chmod +x job_batch.sh
./job_batch.sh
```

> **Tip:** Use `-h` on any LiCSBAS script to see accepted parameters and defaults before editing the batch file.  
> LiCSBAS2 also ships `batch_LiCSBAS.sh` — you can adapt that template instead of writing your own.

### Velocity maps & GeoTIFF export

From inside the time-series directory (e.g. `TS_GEOCml1clip/`):

```bash
# Build masked velocity product, then export
LiCSBAS_cum2vel.py
LiCSBAS_disp_img.py -i 20220315_20260411.vel.mskd -p info/EQA.dem_par --kmz asc_vel.kmz
LiCSBAS_flt2geotiff.py -i 20220315_20260411.vel.mskd -p info/EQA.dem_par
```

Replace the date stamp in the filename with your own velocity product name.

---

## StaMPS — PS mode

**Folder:** `Stamps_ps/INSAR_{master}/`  
**Install:** [StaMPS](https://github.com/dbekaert/StaMPS) — see the StaMPS manual for setup.

Helper MATLAB scripts (`insar_time_ps.m`, `fix_stamps_csv.py`, etc.) are available in the StaMPS `matlab/` folder (also bundled in some project exports).

### Check parameters

In MATLAB, inside `INSAR_{master}/`:

```matlab
ps_info

stamps(1,1)
stamps(2,2)
stamps(3,3)
stamps(4,4)
stamps(5,5)
stamps(6,6)
```

### Atmospheric correction (TRAIN) + steps 7–8

```matlab
% Install TRAIN, then:
aps_linear

stamps(7,7)
stamps(6,7)   % re-run step 6 after APS if needed
stamps(8,8)
```

### Batch mode — `insar_time_ps`

Runs steps 1–7 (including APS and re-runs) in one call:

```matlab
insar_time_ps()        % all steps 1–7
insar_time_ps(3)       % from step 3 to end
insar_time_ps(2, 4)    % steps 2 through 4 only
insar_time_ps(1, 1)    % step 1 only
```

### Plot results

```matlab
ps_plot('v')                              % velocity
ps_plot('v-do')                           % velocity − orbital & DEM error
ps_plot('v-dao','a_linear')               % after TRAIN (aps_linear)

plot_sb_baselines                         % baseline plot
```

---

## StaMPS — SBAS mode

**Folder:** `Stamps_sbas/INSAR_{master}/`

### Check parameters

```matlab
sb_info
```

Confirm small-baseline processing is enabled:

```matlab
% small_baseline_flag should be 'y'
setparm('small_baseline_flag','y')
```

### Batch mode — `insar_time_sb`

Same calling convention as PS:

```matlab
insar_time_sb()        % all steps
insar_time_sb(3)       % from step 3 to end
insar_time_sb(2, 4)    % steps 2–4 only
insar_time_sb(1, 1)    % step 1 only
```

### Plot baselines

```matlab
sb_baseline_plot
```

---

## Merging PS and SBAS

To combine PS and SBAS pixels in one StaMPS project:

1. **During SNAPxtra export** — leave `Range` **empty** in the config for StaMPS SBAS so both PS and SBAS stacks stay single-look and grid-compatible.
2. **Before `mt_prep_snap`** — copy contents of `Stamps_ps/INSAR_{master}/` into `Stamps_sbas/INSAR_{master}/`.

---

## Troubleshooting StaMPS

### sb_loadinitial.m error in stamps(1,1) or stamps(2,2)
Replace sb_load_initial.m in original StaMPS matlab folder path with same file provided here

### `stamps(1,1)` or `stamps(2,2)` fails on large scenes

Split the scene into **multiple patches** instead of a single patch:

1. Check file sizes inside each `PATCH` folder.
2. Remove patches with **0-byte** files from `patch.list`, **or**
3. Run `stamps(1,1)` through `stamps(5,5)` on each patch individually and exclude patches that fail from `patch.list`.

---

## Export & visualization

### A. QGIS InSAR Explorer (time series CSV)

After StaMPS **step 8**, select points and export:

**PS mode**

```matlab
ps_plot('v-do','ts')                      % without TRAIN
ps_plot('v-dao','a_linear','ts')          % with TRAIN (aps_linear)
```

Click on the map and set a **radius** large enough that all points of interest fall inside the circle. Increase radius in steps (e.g. `+10000`, `+30000`) until the full area is covered.

```matlab
ps_export_csv_ps('v-dao','filename.csv')
```

**SBAS mode**

```matlab
ps_export_csv_sbas('v-dao','filename.csv')
```

**Reformat for QGIS**

```bash
python fix_stamps_csv.py filename.csv insar_filename.csv
```

**In QGIS**

1. Install the **QGIS InSAR Explorer** plugin
2. *Layer → Add Layer → Add Delimited Text Layer* → select `insar_filename.csv`
3. Use InSAR Explorer to explore; export rasters as GeoTIFF if needed

---

### B. KML export

```matlab
ps_save_kml_ps('v-dao','ps_vel.kml', 50, 1)
%                                      │   └─ opacity (0–1)
%                                      └──── group every N points as one placemark
```

---

### C. Hillshade background

Prepare a shaded DEM for nicer `ps_plot` figures.

Run from inside `INSAR_{master}/`:

```bash
stamps_dem_prep.py --geo projected_dem.par --dem_api 'YOUR_OPENTOPOGRAPHY_API_KEY'
```

> Free API keys: [OpenTopography](https://portal.opentopography.org/)

This creates `*.raw` and `demparms.in`. Then in MATLAB:

```matlab
ps_plot('v-dao', 2)    % velocity with hillshade background
```

---

## Workflow diagram

```text
SNAPxtra (ps_sbas_snapxtra.py)
        │
        ├─ insar_target=1 ──► GEOC/ ──► LiCSBAS12+ ──► velocity / GeoTIFF / KMZ
        │
        └─ insar_target=2 ──┬─ PS  (Stamps_ps/)  ──► stamps / insar_time_ps ──► ps_plot, CSV, KML
                            └─ SBAS (Stamps_sbas/) ──► insar_time_sb ──► sb_baseline_plot, CSV
```

---

## Further reading

| Resource | Description |
|----------|-------------|
| [LiCSBAS2](https://github.com/yumorishita/LiCSBAS2) | LiCSBAS install & documentation |
| [StaMPS](https://github.com/dbekaert/StaMPS) | StaMPS install & manual |
| [StaMPS GIS blog (GitLab)](https://gitlab.com/Rexthor/gis-blog/-/tree/master/StaMPS) | Tutorials, plotting, GIS tips |
| [ESA SNAP](http://step.esa.int/main/download/snap-download/)
| [SNAPHU](https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/)
| [ASF Data Search](https://search.asf.alaska.edu/)

---

## Related SNAPxtra docs

- **Main workflow:** [README.md](README.md)
- **Sample config:** `insar_proj_sample.config`
- **Dependency check:** `python sbas_check_install.py <config>`

