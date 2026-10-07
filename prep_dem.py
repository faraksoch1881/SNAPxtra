#!/usr/bin/env python3
"""
LiCSBAS-style prep_dem workflow (SNAPxtra).

- master date = latest ZIP acquisition (path naming only; no master-based filtering)
- exactly 2 ZIPs: auto-create one later_earlier pair in sbas_pairs.txt
- more than 2 ZIPs: create empty sbas_pairs.txt and ask the user to add pairs
- each processing step loops over all pairs in sbas_pairs.txt (like ps_sbas_snapxtra)
- Terrain-Correction final product goes under multi_tc_dem_{master}/
"""
import glob
import os
import shutil
import signal
import sys
from datetime import datetime

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.sbas_pair_builder import SBASPairBuilder
from lib.snapxtra_cli import (
    _insar_mode_label,
    _parse_cli_argv,
    _write_config_key,
    write_config_batch_overrides,
)
from lib.snapxtra_processor import SBASMultiProcessor


class PrepDemProcessor(SBASMultiProcessor):
    def __init__(self, config_file, skip_master_check=False):
        super().__init__(config_file, skip_master_check=skip_master_check)
        self.insar_target = 1
        self.insar_method = 1
        self.config["insar_target"] = 1
        self.config["insar_method"] = 1
        self._zip_cache = None
        self._awaiting_user_pairs = False

    def _zip_files_from_config(self):
        if self._zip_cache is None:
            zip_pattern = os.path.join(self.config["input_data"], "*.zip")
            self._zip_cache = sorted(glob.glob(zip_pattern))
        return self._zip_cache

    def _all_zip_dates(self):
        dates = sorted({self.extract_date_from_zip(zf) for zf in self._zip_files_from_config()})
        if len(dates) < 2:
            raise RuntimeError(
                f"Found {len(dates)} unique acquisition date(s) in input_data; need at least 2."
            )
        return dates

    def _pair_info(self, date1, date2, pair_name=None):
        """Build pair dict. later/older used for coreg FILE_LIST only."""
        if date1 <= date2:
            older, later = date1, date2
        else:
            older, later = date2, date1
        return {
            "date1": date1,
            "date2": date2,
            "older": older,
            "later": later,
            "pair_name": pair_name or f"{date1}_{date2}",
        }

    def _iter_prep_pairs(self):
        """Yield pair info dicts from loaded sbas_pairs (loads if needed)."""
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return
        for p in self.sbas_pairs:
            yield self._pair_info(p["date1"], p["date2"])

    def _set_master_from_zips(self):
        """Master = latest acquisition date (path naming only)."""
        dates = self._all_zip_dates()
        self.master_date = dates[-1]
        if str(self.config.get("master", "")).strip() != self.master_date:
            self.update_config_file("master", self.master_date)
            self.config["master"] = self.master_date
        return self.master_date

    def _skip_pair_step03_rule(self, date1, date2):
        return False

    def read_sbas_pair_rows(self):
        sbas_pairs_file = os.path.join(self.proj_root, "metadata_info", "sbas_pairs.txt")
        if not os.path.exists(sbas_pairs_file):
            raise FileNotFoundError(f"SBAS pairs file not found: {sbas_pairs_file}")

        rows = []
        with open(sbas_pairs_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) == 1 and "_" in parts[0]:
                    d1, d2 = parts[0].split("_", 1)
                    rows.append({
                        "date1": d1,
                        "date2": d2,
                        "temp_diff": 0.0,
                        "baseline_diff": 0.0,
                        "pair_name": parts[0],
                    })
                    continue
                if len(parts) >= 2:
                    rows.append({
                        "date1": parts[0],
                        "date2": parts[1],
                        "temp_diff": float(parts[2]) if len(parts) > 2 else 0.0,
                        "baseline_diff": float(parts[3]) if len(parts) > 3 else 0.0,
                        "pair_name": f"{parts[0]}_{parts[1]}",
                    })
        return rows

    def load_sbas_pairs(self):
        self._set_master_from_zips()
        rows = self.read_sbas_pair_rows()
        if not rows:
            sbas_pairs_file = os.path.join(self.proj_root, "metadata_info", "sbas_pairs.txt")
            print(f"\n{'='*80}")
            print("  ✗ sbas_pairs.txt is empty")
            print(f"{'='*80}")
            print(f"  File: {sbas_pairs_file}")
            print("  Add one pair per line, e.g.:")
            print("    20260129_20260117")
            print("    20260127_20260116")
            print(f"{'='*80}\n")
            return False
        self.sbas_pairs = [{
            "date1": row["date1"],
            "date2": row["date2"],
            "temp_diff": row["temp_diff"],
            "baseline_diff": row["baseline_diff"],
        } for row in rows]
        self.master_second_pairs = []
        dates = {self.master_date}
        for r in rows:
            dates.add(r["date1"])
            dates.add(r["date2"])
        self.selected_dates = sorted(dates)
        print(f"  Loaded {len(self.sbas_pairs)} SBAS pair(s)")
        for p in self.sbas_pairs:
            print(f"    • {p['date1']}_{p['date2']}")
        print(f"  Master date (path naming): {self.master_date}")
        return True

    def _write_baselines_for_dates(self, dates, master):
        meta_dir = os.path.join(self.proj_root, "metadata_info")
        os.makedirs(meta_dir, exist_ok=True)
        master_dt = datetime.strptime(master, "%Y%m%d")
        baselines_path = os.path.join(meta_dir, "baselines")
        with open(baselines_path, "w", encoding="utf-8") as f:
            f.write(f"{master} {master} 0 0\n")
            for d in dates:
                if d == master:
                    continue
                temporal_days = (datetime.strptime(d, "%Y%m%d") - master_dt).days
                f.write(f"{master} {d} {temporal_days} 0\n")
        shutil.copy2(baselines_path, os.path.join(meta_dir, "baselines_ps"))

        center_time = "00:00:00"
        for zf in self._zip_files_from_config():
            if master in os.path.basename(zf):
                for token in os.path.basename(zf).split("_"):
                    if len(token) == 15 and token[8] == "T":
                        center_time = f"{token[9:11]}:{token[11:13]}:{token[13:15]}"
                        break
                break

        orbit = self.config.get("orbit", "Ascending")
        heading = "-1.268759891620288e+01" if str(orbit).lower() == "ascending" else "190"
        metadata_path = os.path.join(meta_dir, "metadata")
        with open(metadata_path, "w", encoding="utf-8") as f:
            f.write(f"master = {master}\n")
            f.write(f"Orbit: {orbit}\n")
            f.write(f"heading={heading}\n")
            f.write(f"center_time={center_time}\n")
            f.write(f"Total images: {len(dates)}\n")
            f.write("\nBaseline data:\n")
            f.write("Date\t\tTemporal\tPerpendicular\n")
            f.write(f"{master}\t0\t\t0\n")
            for d in dates:
                if d == master:
                    continue
                temporal_days = (datetime.strptime(d, "%Y%m%d") - master_dt).days
                f.write(f"{d}\t{temporal_days}\t\t0\n")
        return baselines_path

    def _ensure_placeholder_network_plots(self, meta_dir):
        for name in ("network_sbas.png", "network_ps.png"):
            path = os.path.join(meta_dir, name)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                continue
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                for out in (path, path.replace(".png", ".pdf")):
                    fig, ax = plt.subplots(figsize=(4, 2))
                    ax.set_title("No pairs yet")
                    fig.savefig(out)
                    plt.close(fig)
            except Exception:
                open(path, "wb").close()

    def _write_simple_metadata_files(self):
        zip_files = self._zip_files_from_config()
        if len(zip_files) < 2:
            raise RuntimeError(
                f"Found {len(zip_files)} zip files in input_data; need at least 2."
            )

        dates = self._all_zip_dates()
        master = self._set_master_from_zips()
        meta_dir = os.path.join(self.proj_root, "metadata_info")
        os.makedirs(meta_dir, exist_ok=True)
        baselines_path = self._write_baselines_for_dates(dates, master)
        sbas_pairs_path = os.path.join(meta_dir, "sbas_pairs.txt")

        existing_rows = []
        if os.path.isfile(sbas_pairs_path):
            try:
                existing_rows = self.read_sbas_pair_rows()
            except Exception:
                existing_rows = []

        if len(zip_files) == 2 and not existing_rows:
            older, later = dates[0], dates[1]
            pair_name = f"{later}_{older}"
            with open(sbas_pairs_path, "w", encoding="utf-8") as f:
                f.write(f"{pair_name}\n")
            print(f"  ✓ Auto-created single pair for 2 ZIPs: {pair_name}")
            self._awaiting_user_pairs = False
        elif existing_rows:
            print(f"  ✓ Keeping existing {len(existing_rows)} pair(s) in sbas_pairs.txt")
            self._awaiting_user_pairs = False
        else:
            with open(sbas_pairs_path, "w", encoding="utf-8") as f:
                f.write("# prep_dem: add one pair per line as later_earlier or date1_date2\n")
                f.write("# Example:\n")
                f.write("# 20260129_20260117\n")
                f.write("# 20260127_20260116\n")
            print(f"\n{'='*80}")
            print(f"  Found {len(zip_files)} ZIP files / {len(dates)} dates")
            print(f"  Created empty pair list: {sbas_pairs_path}")
            print(f"  Master (latest date, path naming only): {master}")
            print("  ACTION REQUIRED: add pairs to sbas_pairs.txt, then re-run.")
            print(f"{'='*80}\n")
            self._awaiting_user_pairs = True

        builder = SBASPairBuilder(
            baselines_path, self.proj_root, self.config, insar_method=1, insar_target=1)
        builder.load_baselines()
        builder.master_date = master
        if not self._awaiting_user_pairs:
            try:
                builder.sbas_pairs = [
                    {
                        "date1": r["date1"], "date2": r["date2"],
                        "temp_diff": r.get("temp_diff", 0.0),
                        "baseline_diff": r.get("baseline_diff", 0.0),
                    }
                    for r in self.read_sbas_pair_rows()
                ]
            except Exception:
                builder.sbas_pairs = []
        else:
            builder.sbas_pairs = []
        builder.connect_pairs = []
        if builder.sbas_pairs:
            builder.generate_both_network_plots()
        else:
            self._ensure_placeholder_network_plots(meta_dir)

    def step00_parse_baseline_metadata(self):
        print(f"\n{'='*80}")
        print("STEP 00: Create Baselines and Pair List")
        print(f"{'='*80}")
        self._write_simple_metadata_files()
        self.load_baseline_data()
        self._log_release_step00_summary()
        print("\n  ✓ Created baselines")
        print(f"  ✓ Master date: {self.master_date}")
        if self._awaiting_user_pairs:
            print("  ⚠ sbas_pairs.txt is empty — add pairs before continuing")
            return False
        print("  ✓ sbas_pairs.txt ready")
        return True

    def step00_create_baselines_and_network(self):
        ok = self.step00_parse_baseline_metadata()
        if ok:
            self.update_config_file("start_step", "1")
        return ok

    def step02_create_coreg_stack(self):
        print(f"\n{'='*80}")
        print("STEP 3: Create Coregistered Stack")
        print(f"{'='*80}")

        if not self.validate_metadata_info():
            return False
        if not self.load_sbas_pairs():
            return False

        dem_model_backgeo = self.get_backgeo_dem_model()
        swath_raw = str(self.config.get("swath", ""))
        l_burst_raw = str(self.config.get("l_burst", self.config.get("lburst", 1)))
        u_burst_raw = str(self.config.get("u_burst", self.config.get("uburst", 3)))
        is_multiframe = "," in swath_raw or "," in l_burst_raw or "," in u_burst_raw
        if is_multiframe:
            graph_filename = "02_backgeo_esd_mF.xml"
            processing_mode = "Multi-Frame BackGeo ESD"
        else:
            try:
                lb = int(l_burst_raw.strip())
                ub = int(u_burst_raw.strip())
            except (ValueError, TypeError):
                lb = 1
                ub = 3
            if lb == ub:
                graph_filename = "02_backgeo_single_burst.xml"
                processing_mode = "Single Burst"
            else:
                graph_filename = "02_backgeo_esd.xml"
                processing_mode = "Multi-Burst ESD"

        graph_xml = self.get_graph_file(graph_filename)
        for pair in self._iter_prep_pairs():
            for swath_name in self.swaths_to_process:
                # FILE_LIST uses the two pair dates only (later, older); path master is earliest ZIP
                later_dim = self.find_slice_dim(swath_name, pair["later"])
                older_dim = self.find_slice_dim(swath_name, pair["older"])
                if not later_dim or not older_dim:
                    print(f"  ✗ Missing slice DIM for {swath_name} ({pair['pair_name']})")
                    return False
                output_dir = os.path.join(
                    self.proj_root, swath_name, f"coreg_stack_{self.master_date}", pair["pair_name"])
                output_file = os.path.join(output_dir, f"{pair['pair_name']}_Stack_esd.dim")
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair['pair_name']} (already exists)")
                    continue
                file_list = f"{later_dim},{older_dim}"
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_xml,
                    f"-PFILE_LIST_FROM_STEP01={file_list}",
                    f"-Pdem_name_model={dem_model_backgeo}",
                    f"-POUTPUT_STACK_FILE={output_file}",
                ]
                if not self.run_command(
                        cmd, f"{processing_mode} {pair['pair_name']}",
                        expected_output_file=output_file):
                    return False
        return True

    def _extend_optional_dem_args(self, cmd):
        """DEM args for Interferogram / PhaseToElevation (-PdemName, optional -PexternalDEMFile)."""
        dem_name = self.get_backgeo_dem_model()
        external_dem = str(self.config.get("externalDEMFile", "")).strip()
        cmd.append(f"-PdemName={dem_name}")
        if external_dem:
            cmd.append(f"-PexternalDEMFile={external_dem}")
        return cmd

    def step04_topsar_deburst(self):
        print(f"\n{'='*80}")
        print("STEP 5: TOPSAR-Deburst")
        print(f"{'='*80}")

        if not self.load_sbas_pairs():
            return False

        for pair in self._iter_prep_pairs():
            for swath_name in self.swaths_to_process:
                intf_input = os.path.join(
                    self.proj_root, swath_name, f"multi_intf_deb_{self.master_date}",
                    pair["pair_name"], f"{pair['pair_name']}_Stack_esd_mask_deb_mmifg.dim"
                )
                if not os.path.isfile(intf_input):
                    print(f"  ⚠ Step 7 output not found for {pair['pair_name']}: {intf_input}")
                    continue
                output_dir = os.path.join(
                    self.proj_root, swath_name, f"coreg_stack_mask_deb_{self.master_date}",
                    pair["pair_name"])
                output_file = os.path.join(
                    output_dir, f"{pair['pair_name']}_Stack_esd_mask_deb.dim")
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair['pair_name']} (already exists)")
                    continue
                cmd = [
                    *self.gpt_base_cmd(),
                    "TOPSAR-Deburst",
                    f"-Ssource={intf_input}",
                    "-t", output_file,
                ]
                if not self.run_command(
                        cmd, f"Deburst {pair['pair_name']}", expected_output_file=output_file):
                    return False
        return True

    def step05_add_elevation(self):
        print(f"\n{'='*80}")
        print("STEP 6: Add Elevation - SKIPPED")
        print(f"{'='*80}")
        print("  prep_dem.py skips old Step 6.\n")
        return True

    def step06_create_multi_interferograms(self):
        print(f"\n{'='*80}")
        print("STEP 7: Create Multi-Interferograms")
        print(f"{'='*80}")

        if not self.load_sbas_pairs():
            return False

        for pair in self._iter_prep_pairs():
            for swath_name in self.swaths_to_process:
                input_file = os.path.join(
                    self.proj_root, swath_name, f"coreg_stack_mask_{self.master_date}",
                    pair["pair_name"], f"{pair['pair_name']}_Stack_esd_mask.dim"
                )
                if not os.path.isfile(input_file):
                    print(f"  ✗ Input not found: {input_file}")
                    return False
                output_dir = os.path.join(
                    self.proj_root, swath_name, f"multi_intf_deb_{self.master_date}",
                    pair["pair_name"])
                output_file = os.path.join(
                    output_dir, f"{pair['pair_name']}_Stack_esd_mask_deb_mmifg.dim")
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair['pair_name']} (already exists)")
                    continue
                cmd = [
                    *self.gpt_base_cmd(),
                    "Interferogram",
                    f"-SsourceProduct={input_file}",
                ]
                self._extend_optional_dem_args(cmd)
                cmd.extend(["-t", output_file])
                if not self.run_command(
                        cmd, f"Multi-Interferogram {pair['pair_name']}",
                        expected_output_file=output_file):
                    return False
        return True

    def step07_merge_swaths(self):
        """Step 8: merge debursted IFGs (runs after Step 5 in prep_dem)."""
        swath_config = self.get_swath_config_value()
        if swath_config in [1, 2, 3]:
            print(f"\n{'='*80}")
            print("STEP 8: Merge Swaths - SKIPPED")
            print(f"{'='*80}")
            print(f"  Single swath selected (swath={swath_config}), merging not necessary\n")
            return True

        print(f"\n{'='*80}")
        print("STEP 8: Merge Swaths")
        print(f"{'='*80}")

        if swath_config == 0:
            swaths = ["IW1", "IW2", "IW3"]
        elif swath_config == 12:
            swaths = ["IW1", "IW2"]
        elif swath_config == 23:
            swaths = ["IW2", "IW3"]
        else:
            print(f"  ✗ Invalid swath config: {swath_config}")
            return False

        if not self.load_sbas_pairs():
            return False

        merge_folder_name = self.get_merge_folder_name(swath_config)
        merge_output_dir = os.path.join(
            self.proj_root, merge_folder_name, f"multi_intf_deb_mrg_{self.master_date}")
        os.makedirs(merge_output_dir, exist_ok=True)
        print(f"  Merging debursted IFGs: {', '.join(swaths)}")
        print(f"  Output directory: {merge_folder_name}/multi_intf_deb_mrg_{self.master_date}/")
        merge_graph = self.get_graph_file("05_merge_swath.xml")

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            deb_file_list = []
            for swath in swaths:
                deb_file = os.path.join(
                    self.proj_root, swath, f"coreg_stack_mask_deb_{self.master_date}",
                    pair_name, f"{pair_name}_Stack_esd_mask_deb.dim")
                if not os.path.isfile(deb_file):
                    print(f"  ✗ Missing deburst product: {deb_file}")
                    print("  Run Step 7 then Step 5 (TOPSAR-Deburst) before merge.")
                    return False
                deb_file_list.append(deb_file)

            output_dir = os.path.join(merge_output_dir, pair_name)
            output_file = os.path.join(
                output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg.dim")
            if self.check_output_file_valid(output_file):
                print(f"  Skipping {pair_name} (already exists)")
                continue

            cmd = [
                *self.gpt_base_cmd(),
                merge_graph,
                f"-Pdeb_file_list={','.join(deb_file_list)}",
                f"-Poutput_merged_file={output_file}",
            ]
            if not self.run_command(
                    cmd, f"Merge {pair_name}", expected_output_file=output_file):
                return False
        return True

    def step08_subset_interferograms(self):
        print(f"\n{'='*80}")
        print("STEP 9: Subset Interferograms")
        print(f"{'='*80}")

        aoi_region = self.config.get("clip_roi", "")
        if not aoi_region:
            print("  ⚠ No clip_roi defined in config, skipping subset")
            return True
        aoi_region = self.normalize_clip_roi(aoi_region)
        subset_graph = self.get_graph_file("subset_intf.xml")
        if not self.load_sbas_pairs():
            return False

        merge_folder_name = self.get_merge_folder_name(self.get_swath_config_value())

        for pair in self._iter_prep_pairs():
            merged_input = None
            if merge_folder_name:
                candidate = os.path.join(
                    self.proj_root, merge_folder_name, f"multi_intf_deb_mrg_{self.master_date}",
                    pair["pair_name"], f"{pair['pair_name']}_Stack_esd_mask_deb_mmifg_mrg.dim"
                )
                if os.path.isfile(candidate):
                    merged_input = candidate

            for swath_name in self.swaths_to_process:
                if merged_input:
                    output_dir = os.path.join(
                        self.proj_root, merge_folder_name,
                        f"multi_intf_deb_mrg_subset_{self.master_date}",
                        pair["pair_name"]
                    )
                    output_file = os.path.join(
                        output_dir,
                        f"{pair['pair_name']}_Stack_esd_mask_deb_mmifg_mrg_subset.dim")
                    input_file = merged_input
                else:
                    input_file = os.path.join(
                        self.proj_root, swath_name, f"coreg_stack_mask_deb_{self.master_date}",
                        pair["pair_name"], f"{pair['pair_name']}_Stack_esd_mask_deb.dim"
                    )
                    output_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_intf_deb_subset_{self.master_date}",
                        pair["pair_name"]
                    )
                    output_file = os.path.join(
                        output_dir,
                        f"{pair['pair_name']}_Stack_esd_mask_deb_mmifg_subset.dim")

                if not os.path.isfile(input_file):
                    print(f"  ✗ Input not found: {input_file}")
                    return False
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair['pair_name']} (already exists)")
                    if merged_input:
                        break
                    continue
                cmd = [
                    *self.gpt_base_cmd(),
                    subset_graph,
                    f"-Pintf_source={input_file}",
                    f"-Paoi_region_subset={aoi_region}",
                    f"-Poutput_subset_dir={output_file}",
                ]
                if not self.run_command(
                        cmd, f"Subset {pair['pair_name']}", expected_output_file=output_file):
                    return False
                if merged_input:
                    break
        return True

    def _step10_multilook_interferograms(self):
        print("  Mode: Multilooking interferograms")

        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        use_subset = False
        if use_merged:
            merge_subset_dir = os.path.join(self.proj_root, merge_folder_name, f"multi_intf_deb_mrg_subset_{self.master_date}")
            merge_deb_dir = os.path.join(self.proj_root, merge_folder_name, f"multi_intf_deb_mrg_{self.master_date}")
            if os.path.isdir(merge_subset_dir) and os.listdir(merge_subset_dir):
                use_subset = True
            elif not (os.path.isdir(merge_deb_dir) and os.listdir(merge_deb_dir)):
                use_merged = False

        if not self.load_sbas_pairs():
            return False

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            for swath_name in self.swaths_to_process:
                if use_merged:
                    input_base_dir = os.path.join(
                        self.proj_root, merge_folder_name,
                        f"{'multi_intf_deb_mrg_subset' if use_subset else 'multi_intf_deb_mrg'}_{self.master_date}"
                    )
                    input_file = os.path.join(
                        input_base_dir, pair_name,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg{'_mrg_subset' if use_subset else '_mrg'}.dim"
                    )
                    output_dir = os.path.join(
                        self.proj_root, merge_folder_name,
                        f"multi_intf_deb_mrg_ml_{self.master_date}", pair_name)
                    output_file = os.path.join(
                        output_dir,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg{'_mrg_subset' if use_subset else '_mrg'}_ml.dim")
                else:
                    input_base_dir = os.path.join(
                        self.proj_root, swath_name,
                        f"{'multi_intf_deb_subset' if os.path.isdir(os.path.join(self.proj_root, swath_name, f'multi_intf_deb_subset_{self.master_date}')) else 'multi_intf_deb'}_{self.master_date}"
                    )
                    suffix = "_subset" if "subset" in os.path.basename(input_base_dir) else ""
                    input_file = os.path.join(
                        input_base_dir, pair_name,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg{suffix}.dim")
                    output_dir = os.path.join(
                        self.proj_root, swath_name,
                        f"multi_intf_deb_ml_{self.master_date}", pair_name)
                    output_file = os.path.join(
                        output_dir,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg{suffix}_ml.dim")

                if not os.path.isfile(input_file):
                    print(f"  ⚠ Input file not found: {input_file}")
                    continue
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already processed)")
                    if use_merged:
                        break
                    continue
                cmd = self.build_step14_multilook_gpt_cmd(input_file, output_file)
                if not self.run_command(
                        cmd, f"Multilook {pair_name}", expected_output_file=output_file):
                    return False
                if use_merged:
                    break
        return True

    def _resolve_multilook_product(self, pair_name, swath_name=None, merge_folder_name=None):
        """Locate Step 14 multilook .dim; return (input_dim, output_dir_basename, output_suffix)."""
        candidates = []
        if merge_folder_name:
            base = os.path.join(
                self.proj_root, merge_folder_name,
                f"multi_intf_deb_mrg_ml_{self.master_date}", pair_name)
            out_base = f"multi_intf_deb_mrg_ml_flt_{self.master_date}"
            candidates.extend([
                (os.path.join(base, f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg_subset_ml.dim"),
                 out_base, "_mrg_ml_flt"),
                (os.path.join(base, f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg_ml.dim"),
                 out_base, "_mrg_ml_flt"),
            ])
        if swath_name:
            base = os.path.join(
                self.proj_root, swath_name,
                f"multi_intf_deb_ml_{self.master_date}", pair_name)
            out_base = f"multi_intf_deb_ml_flt_{self.master_date}"
            candidates.extend([
                (os.path.join(base, f"{pair_name}_Stack_esd_mask_deb_mmifg_subset_ml.dim"),
                 out_base, "_ml_flt"),
                (os.path.join(base, f"{pair_name}_Stack_esd_mask_deb_mmifg_ml.dim"),
                 out_base, "_ml_flt"),
            ])
        for input_file, out_base_name, out_suffix in candidates:
            if os.path.isfile(input_file):
                return input_file, out_base_name, out_suffix
        return None, None, None

    def step14_goldstein_filtering(self):
        print(f"\n{'='*80}")
        print("STEP 18: GoldsteinPhaseFiltering")
        print(f"{'='*80}")

        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        if not self.load_sbas_pairs():
            return False

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            for swath_name in self.swaths_to_process:
                input_file, output_base_name, output_suffix = self._resolve_multilook_product(
                    pair_name,
                    swath_name=None if use_merged else swath_name,
                    merge_folder_name=merge_folder_name if use_merged else None,
                )
                if not input_file and use_merged:
                    input_file, output_base_name, output_suffix = self._resolve_multilook_product(
                        pair_name, swath_name=swath_name, merge_folder_name=None)
                if not input_file:
                    print(f"  ⚠ Multilook product not found for {pair_name} ({swath_name})")
                    continue

                if use_merged and merge_folder_name and output_base_name.startswith("multi_intf_deb_mrg"):
                    output_dir = os.path.join(
                        self.proj_root, merge_folder_name, output_base_name, pair_name)
                else:
                    output_dir = os.path.join(
                        self.proj_root, swath_name, output_base_name, pair_name)
                output_file = os.path.join(
                    output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg{output_suffix}.dim")

                print(f"  Using multilook input: {input_file}")
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already processed)")
                    if use_merged:
                        break
                    continue
                cmd = [
                    *self.gpt_base_cmd(),
                    "GoldsteinPhaseFiltering",
                    f"-SsourceProduct={input_file}",
                    "-t", output_file,
                ]
                if not self.run_command(
                        cmd, f"GoldsteinFilter {pair_name}", expected_output_file=output_file):
                    return False
                if use_merged:
                    break
        return True

    def step17_filter_snaphu_and_preview(self):
        print(f"\n{'='*80}")
        print("STEP 20: CORRFILE coherence post-processing")
        print(f"{'='*80}")

        lay = self._post_goldstein_ml_flt_layout()
        self._corr_postprocess_conf_paths = []
        for swath_name in self.swaths_to_process:
            if lay["use_merged"]:
                multi_snaphu_dir = os.path.join(
                    self.proj_root, lay["merge_folder_name"], f"multi_snaphu_{self.master_date}")
            else:
                multi_snaphu_dir = os.path.join(
                    self.proj_root, swath_name, f"multi_snaphu_{self.master_date}")
            if not os.path.isdir(multi_snaphu_dir):
                continue
            for pair_folder in sorted(os.listdir(multi_snaphu_dir)):
                pair_folder_path = os.path.join(multi_snaphu_dir, pair_folder)
                if not os.path.isdir(pair_folder_path):
                    continue
                subfolder_path, _subfolder_name = self.resolve_snaphu_stack_subfolder(
                    pair_folder_path, pair_folder, swath_name, lay)
                if not subfolder_path:
                    continue
                # prep_dem / Interferogram export produces only snaphu.conf
                generic_conf = os.path.join(subfolder_path, "snaphu.conf")
                if os.path.isfile(generic_conf):
                    self._corr_postprocess_conf_paths.append(generic_conf)
                    print(f"  Using: {generic_conf}")
                    continue
                for conf_name in sorted(os.listdir(subfolder_path)):
                    if conf_name.endswith("snaphu.conf"):
                        self._corr_postprocess_conf_paths.append(
                            os.path.join(subfolder_path, conf_name))
            if lay["use_merged"]:
                break
        return self.postprocess_corrfile_coherence_nan2zero()

    def execute_snaphu_unwrapping(self, snaphu_executable):
        """Unwrap with the single snaphu.conf produced by SnaphuExport (Interferogram mode)."""
        import subprocess

        print(f"\n{'='*80}")
        print("EXECUTING SNAPHU UNWRAPPING")
        print(f"{'='*80}")

        lay = self._post_goldstein_ml_flt_layout()
        total_unwrapped = 0
        total_skipped = 0
        total_failed = 0

        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            if lay["use_merged"]:
                multi_snaphu_dir = os.path.join(
                    self.proj_root, lay["merge_folder_name"], f"multi_snaphu_{self.master_date}")
            else:
                multi_snaphu_dir = os.path.join(
                    self.proj_root, swath_name, f"multi_snaphu_{self.master_date}")
            if not os.path.isdir(multi_snaphu_dir):
                print(f"  ⚠ Multi-snaphu directory not found: {multi_snaphu_dir}")
                continue

            for pair_folder in sorted(os.listdir(multi_snaphu_dir)):
                pair_folder_path = os.path.join(multi_snaphu_dir, pair_folder)
                if not os.path.isdir(pair_folder_path):
                    continue
                subfolder_path, _subfolder_name = self.resolve_snaphu_stack_subfolder(
                    pair_folder_path, pair_folder, swath_name, lay)
                if not subfolder_path:
                    continue

                conf_file = "snaphu.conf"
                conf_path = os.path.join(subfolder_path, conf_file)
                if not os.path.isfile(conf_path):
                    dated = [
                        f for f in os.listdir(subfolder_path)
                        if f.endswith("snaphu.conf") and f != "snaphu.conf"
                    ]
                    if not dated:
                        print(f"  ⚠ No snaphu.conf for {pair_folder}")
                        continue
                    conf_file = sorted(dated)[0]
                    conf_path = os.path.join(subfolder_path, conf_file)

                print(f"\n  Pair: {pair_folder}")
                phase_img_file, _corr_img = self._parse_snaphu_conf_phase_corr(conf_path)
                line_length = None
                try:
                    with open(conf_path, "r", encoding="utf-8", errors="ignore") as f:
                        for line in f:
                            if "Phase_ifg_" in line and ".snaphu.img" in line:
                                parts = line.strip().lstrip("#").split()
                                for i, part in enumerate(parts):
                                    if part.startswith("Phase_ifg_") and part.endswith(".snaphu.img"):
                                        if not phase_img_file:
                                            phase_img_file = part
                                        if i + 1 < len(parts):
                                            try:
                                                int(parts[i + 1])
                                                line_length = parts[i + 1]
                                            except ValueError:
                                                pass
                                        break
                except OSError as e:
                    print(f"    ✗ Failed to read {conf_file}: {e}")
                    total_failed += 1
                    continue

                if not phase_img_file:
                    phase_candidates = sorted(
                        f for f in os.listdir(subfolder_path)
                        if f.startswith("Phase_ifg_") and f.endswith(".snaphu.img")
                    )
                    if phase_candidates:
                        phase_img_file = phase_candidates[0]

                if not phase_img_file:
                    print(f"    ⚠ Could not find Phase_ifg image for {pair_folder}")
                    total_failed += 1
                    continue

                if not line_length:
                    hdr = os.path.join(subfolder_path, phase_img_file.replace(".img", ".hdr"))
                    if not os.path.isfile(hdr):
                        hdr = self._hdr_path_for_flat_img(os.path.join(subfolder_path, phase_img_file))
                    if hdr:
                        samples, _lines = self._read_envi_hdr_samples_lines(hdr)
                        if samples:
                            line_length = str(samples)

                phase_img_path = os.path.join(subfolder_path, phase_img_file)
                if not os.path.isfile(phase_img_path):
                    print(f"    ⚠ Phase image not found: {phase_img_file}")
                    total_failed += 1
                    continue

                ok_dims, dim_msg = self._validate_snaphu_conf_phase_coh_dims(conf_path, subfolder_path)
                if not ok_dims:
                    self._print_block_always(
                        f"\n{'='*80}\n"
                        f"STEP 21 STOPPED — Phase/CORRFILE dimension mismatch\n"
                        f"{'='*80}\n"
                        f"{dim_msg.rstrip()}\n"
                    )
                    return False

                unwphase_img_file = phase_img_file.replace("Phase_ifg_", "UnwPhase_ifg_")
                unwphase_img_path = os.path.join(subfolder_path, unwphase_img_file)
                if os.path.isfile(unwphase_img_path) and os.path.getsize(unwphase_img_path) > 0:
                    total_skipped += 1
                    print(f"    ✓ SKIP {conf_file} (already unwrapped)")
                    continue

                print(f"    → UNWRAP {conf_file}")
                print(f"      Phase: {phase_img_file}")
                cmd = [snaphu_executable, "-f", conf_file, phase_img_file]
                if line_length:
                    cmd.append(str(line_length))
                print(f"      {' '.join(cmd)}")

                try:
                    original_cwd = os.getcwd()
                    os.chdir(subfolder_path)
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
                    os.chdir(original_cwd)
                    if os.path.isfile(unwphase_img_path) and os.path.getsize(unwphase_img_path) > 0:
                        total_unwrapped += 1
                        print(f"      ✓ SUCCESS: {unwphase_img_file}")
                    else:
                        total_failed += 1
                        error_msg = "UnwPhase file not created"
                        if result.stderr:
                            error_msg += f" - {result.stderr[:200]}"
                            print(f"      Error: {result.stderr[:200]}")
                        print(f"      ✗ FAILED: {error_msg}")
                        self.log_snaphu_failure(conf_file, error_msg)
                except subprocess.TimeoutExpired:
                    os.chdir(original_cwd)
                    total_failed += 1
                    print("      ✗ TIMEOUT: Unwrapping took >30 minutes")
                    self.log_snaphu_failure(conf_file, "Unwrapping took >30 minutes")
                except Exception as e:
                    try:
                        os.chdir(original_cwd)
                    except Exception:
                        pass
                    total_failed += 1
                    print(f"      ✗ ERROR: {e}")
                    self.log_snaphu_failure(conf_file, str(e))

            if lay["use_merged"]:
                break

        print(f"\n{'='*80}")
        print("STEP 21 UNWRAPPING SUMMARY")
        print(f"{'='*80}")
        print(f"  ✓ Successful unwraps: {total_unwrapped}")
        print(f"  ⊙ Already unwrapped (skipped): {total_skipped}")
        if total_failed > 0:
            print(f"  ✗ Failed unwraps: {total_failed}")
        print(f"{'='*80}\n")
        if total_unwrapped == 0 and total_skipped == 0:
            return False
        return total_failed == 0 or total_unwrapped > 0 or total_skipped > 0

    def step19_import_unwrapped(self):
        """Import unwrapped IFG using pair name as listed (no chronological flip)."""
        print(f"\n{'='*80}")
        print("STEP 22: Import Unwrapped Interferograms")
        print(f"{'='*80}")

        if not self.load_sbas_pairs():
            return False
        lay = self._post_goldstein_ml_flt_layout()
        graph_xml = self.get_graph_file("snaphu_import.xml")
        imported = []

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            for swath_name in self.swaths_to_process:
                print(f"\nProcessing swath: {swath_name} / pair: {pair_name}")
                multi_intf_deb_ml_flt_dir, intf_stack_suffix_dim = self.snaphu_source_base_dir_and_suffix(
                    swath_name, lay)
                if lay["use_merged"]:
                    multi_snaphu_dir = os.path.join(
                        self.proj_root, lay["merge_folder_name"], f"multi_snaphu_{self.master_date}")
                    output_base_dir = os.path.join(
                        self.proj_root, lay["merge_folder_name"], f"multi_unw_{self.master_date}")
                else:
                    multi_snaphu_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_snaphu_{self.master_date}")
                    output_base_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_unw_{self.master_date}")

                print(
                    "  Step 22: `-Pstack` references the same product tree as SnaphuExport Step 19 "
                    f"(here: `{os.path.basename(multi_intf_deb_ml_flt_dir)}/`; "
                    f"suffix `{intf_stack_suffix_dim}`)"
                )

                pair_folder_path = os.path.join(multi_snaphu_dir, pair_name)
                if not os.path.isdir(pair_folder_path):
                    print(f"  ✗ SNAPHU pair folder not found: {pair_folder_path}")
                    return False

                subfolder_path, subfolder_name = self.resolve_snaphu_stack_subfolder(
                    pair_folder_path, pair_name, swath_name, lay)
                if not subfolder_path:
                    print(f"  ✗ SNAPHU stack subfolder not found under {pair_folder_path}")
                    return False

                deburst_file, stack_suffix_used = self.resolve_snaphu_stack_dim_path(
                    multi_intf_deb_ml_flt_dir, pair_name, swath_name, lay)
                if not deburst_file:
                    print(
                        f"  ✗ Stack .dim not found under "
                        f"{os.path.basename(multi_intf_deb_ml_flt_dir)}/{pair_name}/"
                    )
                    return False

                later_fmt = self.convert_date_format(pair["later"])
                older_fmt = self.convert_date_format(pair["older"])
                unwphase_img_file = None
                for filename in sorted(os.listdir(subfolder_path)):
                    if not (filename.startswith("UnwPhase_ifg_") and filename.endswith(".snaphu.img")):
                        continue
                    if later_fmt in filename and older_fmt in filename:
                        unwphase_img_file = os.path.join(subfolder_path, filename)
                        break
                if not unwphase_img_file:
                    for filename in sorted(os.listdir(subfolder_path)):
                        if filename.startswith("UnwPhase_ifg_") and filename.endswith(".snaphu.img"):
                            unwphase_img_file = os.path.join(subfolder_path, filename)
                            break
                if not unwphase_img_file:
                    print(f"  ✗ UnwPhase .img not found in {subfolder_path}")
                    return False

                output_dir = os.path.join(output_base_dir, pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}{stack_suffix_used}")
                print(f"  Parent/SNAPHU: {pair_name}  ({subfolder_name})")
                print(f"  Output: {output_file}")

                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    imported.append(pair_name)
                    if lay["use_merged"]:
                        break
                    continue

                cmd = [
                    *self.gpt_base_cmd(),
                    graph_xml,
                    f"-Pstack={deburst_file}",
                    f"-PunwImg={unwphase_img_file}",
                    f"-PoutFile={output_file}",
                ]
                if not self.run_command(
                        cmd, f"SNAPHU Unwrap+Import {pair_name}",
                        expected_output_file=output_file):
                    return False
                imported.append(pair_name)
                if lay["use_merged"]:
                    break

        print(f"\n{'='*80}")
        print("STEP 22 SUMMARY")
        print(f"{'='*80}")
        print(f"  ✓ Imported pair(s): {', '.join(dict.fromkeys(imported)) or '(none)'}")
        print(f"{'='*80}\n")
        return True

    def _find_unw_and_coh_bands(self, band_names):
        """Resolve Unw/Coh bands from SnaphuImport products.

        Interferogram + SnaphuImport names the unwrap band ``Unw_Phase_ifg_*``
        (underscore after Unw), unlike MultiMasterInSAR's ``UnwPhase_ifg_*``.
        """
        unw = next(
            (b for b in band_names
             if b.startswith("UnwPhase_") or b.startswith("Unw_Phase_")),
            None,
        )
        coh = next((b for b in band_names if b.startswith("coh_")), None)
        return unw, coh

    def _resolve_coh_band_name_from_unw_data(self, unw_dim):
        """Return coh_* band basename from Step 22 ``.data/coh*.img`` (no extension)."""
        data_dir = unw_dim.replace(".dim", ".data")
        if not os.path.isdir(data_dir):
            return None
        coh_imgs = sorted(glob.glob(os.path.join(data_dir, "coh*.img")))
        if not coh_imgs:
            return None
        return os.path.splitext(os.path.basename(coh_imgs[0]))[0]

    def step20_terrain_correction(self):
        print(f"\n{'='*80}")
        print("STEP 23: Terrain-Correction")
        print(f"{'='*80}")

        if not self.load_sbas_pairs():
            return False
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        dem_merge_graph = self.get_graph_file("dem_merge.xml")

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            for swath_name in self.swaths_to_process:
                if use_merged:
                    multi_unw_dir = os.path.join(
                        self.proj_root, merge_folder_name, f"multi_unw_{self.master_date}")
                    phase_elev_base = os.path.join(
                        self.proj_root, merge_folder_name, f"multi_tc_{self.master_date}")
                    merge_dem_base = os.path.join(
                        self.proj_root, merge_folder_name, f"multi_tc_merge_dem_{self.master_date}")
                    tc_base_dir = os.path.join(
                        self.proj_root, merge_folder_name, f"multi_tc_dem_{self.master_date}")
                    input_suffix = "_Stack_esd_mask_deb_mmifg_mrg_ml_flt.dim"
                else:
                    multi_unw_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_unw_{self.master_date}")
                    phase_elev_base = os.path.join(
                        self.proj_root, swath_name, f"multi_tc_{self.master_date}")
                    merge_dem_base = os.path.join(
                        self.proj_root, swath_name, f"multi_tc_merge_dem_{self.master_date}")
                    tc_base_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_tc_dem_{self.master_date}")
                    input_suffix = "_Stack_esd_mask_deb_mmifg_ml_flt.dim"

                unw_band = os.path.join(multi_unw_dir, pair_name, f"{pair_name}{input_suffix}")
                if not os.path.isfile(unw_band):
                    print(f"  ⚠ Input not found: {unw_band}")
                    continue

                extract_band_name = self._resolve_coh_band_name_from_unw_data(unw_band)
                if not extract_band_name:
                    print(f"  ✗ No coh*.img found next to Step 22 product: {unw_band}")
                    return False
                print(f"  Coherence band for BandMerge: {extract_band_name}")

                phase_elev_file = os.path.join(
                    phase_elev_base, pair_name,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_phase_elev.dim")
                merge_dem_file = os.path.join(
                    merge_dem_base, pair_name,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_merge_dem.dim")
                tc_file = os.path.join(
                    tc_base_dir, pair_name,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_TC.dim")

                if not self.check_output_file_valid(phase_elev_file):
                    cmd_phase = [
                        *self.gpt_base_cmd(cache_size="16G"),
                        "PhaseToElevation",
                        f"-Ssource={unw_band}",
                    ]
                    self._extend_optional_dem_args(cmd_phase)
                    cmd_phase.extend(["-t", phase_elev_file])
                    if not self.run_command(
                            cmd_phase, f"PhaseToElevation {pair_name}",
                            expected_output_file=phase_elev_file):
                        return False

                if not self.check_output_file_valid(merge_dem_file):
                    os.makedirs(os.path.dirname(merge_dem_file), exist_ok=True)
                    cmd_merge = [
                        *self.gpt_base_cmd(cache_size="16G"),
                        dem_merge_graph,
                        f"-Pelevation_band={phase_elev_file}",
                        f"-Pextract_band_name={extract_band_name}",
                        f"-Punw_band={unw_band}",
                        "-Pnew_band_name=coherence",
                        f"-Pdem_band_merge={merge_dem_file}",
                    ]
                    if not self.run_command(
                            cmd_merge, f"BandMerge {pair_name}",
                            expected_output_file=merge_dem_file):
                        return False

                if self.check_output_file_valid(tc_file):
                    print(f"  Skipping {pair_name}: Terrain-Correction output already exists")
                    if use_merged:
                        break
                    continue

                dem_name_tc = self.get_backgeo_dem_model()
                print(f"  Terrain-Correction DEM (-PdemName): {dem_name_tc}")
                print(f"  Terrain-Correction output: {tc_file}")
                cmd_tc = [
                    *self.gpt_base_cmd(cache_size="16G"),
                    "Terrain-Correction",
                    f"-Ssource={merge_dem_file}",
                    "-PsourceBands=coherence,elevation",
                    f"-PdemName={dem_name_tc}",
                    "-PsaveDEM=true",
                    "-PnodataValueAtSea=true",
                    "-t", tc_file,
                ]
                if not self.run_command(
                        cmd_tc, f"Terrain-Correction {pair_name}",
                        expected_output_file=tc_file):
                    return False
                if use_merged:
                    break
        return True

    def step21_export_geotiff(self):
        print(f"\n{'='*80}")
        print("STEP 24: Export DEM / Coherence GeoTIFF")
        print(f"{'='*80}")

        if not self.load_sbas_pairs():
            return False
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None

        for pair in self._iter_prep_pairs():
            pair_name = pair["pair_name"]
            for swath_name in self.swaths_to_process:
                if use_merged:
                    multi_tc_dir = os.path.join(
                        self.proj_root, merge_folder_name, f"multi_tc_dem_{self.master_date}")
                    dem_dir = os.path.join(self.proj_root, merge_folder_name, "DEM")
                else:
                    multi_tc_dir = os.path.join(
                        self.proj_root, swath_name, f"multi_tc_dem_{self.master_date}")
                    dem_dir = os.path.join(self.proj_root, swath_name, "DEM")

                tc_dim = os.path.join(
                    multi_tc_dir, pair_name,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_TC.dim")
                if not os.path.isfile(tc_dim):
                    print(f"  ⚠ Terrain-corrected DIM not found: {tc_dim}")
                    continue

                output_pair_dir = os.path.join(dem_dir, pair_name)
                os.makedirs(output_pair_dir, exist_ok=True)

                dem_output = os.path.join(output_pair_dir, f"{pair_name}.geo.dem.tif")
                if not os.path.exists(dem_output):
                    cmd_dem = [
                        *self.gpt_base_cmd(),
                        "Subset",
                        f"-Ssource={tc_dim}",
                        "-PsourceBands=elevation_VV",
                        "-t", dem_output,
                        "-f", "GeoTIFF",
                    ]
                    if not self.run_command(cmd_dem, f"Export DEM {pair_name}"):
                        return False
                    if os.path.exists(dem_output):
                        print(f"    ✓ Exported: {pair_name}.geo.dem.tif")
                        png_path = self.generate_png_from_geotiff(dem_output)
                        if png_path:
                            print(f"    ✓ Generated PNG: {os.path.basename(png_path)}")

                coh_output = os.path.join(output_pair_dir, f"{pair_name}.geo.cc.tif")
                if not os.path.exists(coh_output):
                    cmd_coh = [
                        *self.gpt_base_cmd(),
                        "Subset",
                        f"-Ssource={tc_dim}",
                        "-PsourceBands=coherence_VV",
                        "-t", coh_output,
                        "-f", "GeoTIFF",
                    ]
                    if not self.run_command(cmd_coh, f"Export Coherence {pair_name}"):
                        return False
                    if os.path.exists(coh_output):
                        print(f"    ✓ Exported: {pair_name}.geo.cc.tif")

                print(f"  ✓ DEM export complete for {pair_name}")
                if use_merged:
                    break
        return True

    def _prep_dem_pipeline(self):
        """Ordered prep_dem workflow (Step 7 → 5 Deburst → 8 Merge)."""
        return [
            (0, "Create Baselines and Pair List", self.step00_parse_baseline_metadata),
            (1, "AOI & Swath configuration", self.step01_aoi_and_swath_config),
            (2, "Prepare Slices", self.step02_prepare_slices_outer),
            (3, "Create Coregistered Stack", self.step02_create_coreg_stack),
            (4, "Land-Sea-Mask", self.step03_land_sea_mask),
            (7, "Create Multi-Interferograms", self.step06_create_multi_interferograms),
            (5, "TOPSAR-Deburst", self.step04_topsar_deburst),
            (8, "Merge Swaths", self.step07_merge_swaths),
            (9, "Subset Interferograms", self.step08_subset_interferograms),
            (10, "Import-Vector AOI (optional)", self.step_import_vector_mask_shp),
            (11, "Land-Sea-Mask AOI (optional)", self.step_landsea_mask_vector_geometry),
            (14, "Multilook", self.step10_multilook),
            (18, "GoldsteinPhaseFiltering", self.step14_goldstein_filtering),
            (19, "SnaphuExport", self.step15_snaphu_export),
            (20, "CORRFILE coherence post-processing", self.step17_filter_snaphu_and_preview),
            (21, "Execute Unwrapping", self.step18_snaphu_unwrap),
            (22, "Import Unwrapped", self.step19_import_unwrapped),
            (23, "Terrain-Correction", self.step20_terrain_correction),
            (24, "Export GeoTIFF", self.step21_export_geotiff),
        ]

    def _select_pipeline_steps(self, steps, start, end):
        """Select by pipeline order (not numeric sort), and keep Step 5 as 7.1 after Step 7."""
        start_idxs = [i for i, (n, _, _) in enumerate(steps) if n == start]
        end_idxs = [i for i, (n, _, _) in enumerate(steps) if n == end]
        if not start_idxs:
            start_idxs = [i for i, (n, _, _) in enumerate(steps) if n >= start][:1]
        if not end_idxs:
            end_idxs = [i for i, (n, _, _) in enumerate(steps) if n <= end]
        if not start_idxs or not end_idxs:
            return []
        i0, i1 = start_idxs[0], end_idxs[-1]
        if i1 < i0:
            return []
        selected = list(steps[i0:i1 + 1])
        selected_nums = {n for n, _, _ in selected}
        # Step 7 always includes following Deburst (Step 5 / 7.1)
        if 7 in selected_nums and 5 not in selected_nums:
            for i, (n, _, _) in enumerate(steps):
                if n == 7 and i + 1 < len(steps) and steps[i + 1][0] == 5:
                    j = next(k for k, s in enumerate(selected) if s[0] == 7)
                    selected.insert(j + 1, steps[i + 1])
                    break
        return selected

    def run_all_steps(self):
        steps = self._prep_dem_pipeline()
        start_val = str(self.start_step).strip()
        end_val = str(self.end_step).strip()
        start = int(start_val) if start_val.isdigit() else 0
        end = int(end_val) if end_val.isdigit() else 24

        selected = self._select_pipeline_steps(steps, start, end)
        if not selected:
            print(f"No steps selected for range {start}..{end}")
            return False

        print(f"\n{'='*80}")
        print("PREP_DEM PROCESSING")
        print(f"{'='*80}")
        print(f"Project root: {self.proj_root}")
        print(f"Swaths: {', '.join(self.swaths_to_process)}")
        print(f"Step range: {start} to {end}")
        print(f"Pipeline order: {' → '.join(str(n) for n, _, _ in selected)}")
        print(f"{'='*80}\n")

        for idx, (step_num, step_name, step_func) in enumerate(selected):
            if step_num == 5 and idx > 0 and selected[idx - 1][0] == 7:
                header = "STEP 5 (7.1): TOPSAR-Deburst"
            else:
                header = f"STEP {step_num}: {step_name}"
            print(f"\n{'='*80}")
            print(header)
            print(f"{'='*80}")
            if not step_func():
                print(f"\n✗ Step {step_num} failed!")
                return False
        print(f"\n{'='*80}")
        print(f"✓ STEPS {start}-{end} COMPLETED SUCCESSFULLY")
        print(f"{'='*80}\n")
        return True


def _print_prep_dem_usage():
    print("Usage:")
    print("  python prep_dem.py <config_file>                # Batch: use start_step/end_step in config")
    print("  python prep_dem.py <config_file> baselines      # Step 0 only (baselines + pair list)")
    print("  python prep_dem.py <step_number> <config_file>  # Run one supported step")
    print("  python prep_dem.py <config_file> -s 3           # Batch: start at 3, run to step 24")
    print("  python prep_dem.py <config_file> -s 3 -e 10     # Batch: set start_step/end_step, then run")
    print("")
    print("Notes:")
    print("  - Forces LiCSBAS mode (insar_target=1, insar_method=1)")
    print("  - Master = latest ZIP date (path naming only; no master-based filtering)")
    print("  - Exactly 2 ZIPs: auto-create one later_earlier pair")
    print("  - More than 2 ZIPs: write empty sbas_pairs.txt and ask you to add pairs")
    print("  - Each step loops over all pairs in sbas_pairs.txt")
    print("  - Terrain-Correction writes multi_tc_dem_{master}/")
    print("  - Pipeline around IFG: Step 7 → Step 5 Deburst (7.1) → Step 8 Merge")
    print("  - -s 7 always includes Step 5 (Deburst) after Multi-Interferograms")


def main():
    def signal_handler(signum, frame):
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = str(signum)
        print(f"\n! Received signal {signum} ({signal_name}) - continuing processing...")

    signal.signal(signal.SIGTERM, signal_handler)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_prep_dem_usage()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    try:
        (config_file, step_num, baselines_mode, cli_start, cli_end,
         cli_target, cli_method) = _parse_cli_argv(sys.argv[1:])
    except ValueError as e:
        print(f"ERROR: {e}")
        _print_prep_dem_usage()
        sys.exit(1)

    config_file = os.path.abspath(config_file)
    if not os.path.exists(config_file):
        print(f"ERROR: Config file not found: {config_file}")
        sys.exit(1)

    if cli_target is not None:
        _write_config_key(config_file, "insar_target", 1)
    if cli_method is not None:
        _write_config_key(config_file, "insar_method", 1)
    if cli_start is not None or cli_end is not None:
        clear_end = cli_start is not None and cli_end is None
        write_config_batch_overrides(
            config_file,
            start_step=cli_start,
            end_step=cli_end,
            clear_end_step=clear_end,
            insar_target=1,
            insar_method=1,
        )
        print(f"  Mode: {_insar_mode_label(1, 1)}")

    processor = None
    try:
        processor = PrepDemProcessor(config_file, skip_master_check=True)
        if baselines_mode:
            ok = processor.step00_create_baselines_and_network()
        elif step_num is None:
            ok = processor.run_all_steps()
        else:
            step_map = {
                0: processor.step00_parse_baseline_metadata,
                1: processor.step01_aoi_and_swath_config,
                2: processor.step02_prepare_slices_outer,
                3: processor.step02_create_coreg_stack,
                4: processor.step03_land_sea_mask,
                5: processor.step04_topsar_deburst,
                6: processor.step05_add_elevation,
                7: processor.step06_create_multi_interferograms,
                8: processor.step07_merge_swaths,
                9: processor.step08_subset_interferograms,
                10: processor.step_import_vector_mask_shp,
                11: processor.step_landsea_mask_vector_geometry,
                14: processor.step10_multilook,
                18: processor.step14_goldstein_filtering,
                19: processor.step15_snaphu_export,
                20: processor.step17_filter_snaphu_and_preview,
                21: processor.step18_snaphu_unwrap,
                22: processor.step19_import_unwrapped,
                23: processor.step20_terrain_correction,
                24: processor.step21_export_geotiff,
            }
            if step_num not in step_map:
                print(f"ERROR: Unsupported step for prep_dem.py: {step_num}")
                sys.exit(1)
            ok = step_map[step_num]()
            # Single-step 7 also runs Deburst as 7.1
            if ok and step_num == 7:
                print(f"\n{'='*80}")
                print("STEP 5 (7.1): TOPSAR-Deburst")
                print(f"{'='*80}")
                ok = processor.step04_topsar_deburst()
        if not ok:
            sys.exit(1)
    except Exception as e:
        print("\nERROR: Processing failed")
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if processor is not None:
            processor._teardown_session_logging()


if __name__ == "__main__":
    main()
