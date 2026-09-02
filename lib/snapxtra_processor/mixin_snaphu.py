"""SNAPHU export through import"""
import os
import sys
import glob
import re
import math
import subprocess
import shutil
import signal
import time
import json
import multiprocessing as mp
from datetime import datetime
from pathlib import Path
import xml.etree.ElementTree as ET

from lib.snapxtra_log import TeeStream, ReleaseFilterStream, release_line_allowed
from lib.snapxtra_var import APP_NAME, VERSION


class SnaphuMixin:

    def snaphu_stack_subfolder_prefix_only(self, swath_name):
        """Stack subfolder basename under pair dir (matches SnaphuExport ``input_basename`` without ``.dim``)."""
        return self.snaphu_stack_subfolder_prefix_candidates(swath_name)[0]

    def resolve_snaphu_stack_subfolder(self, pair_folder_path, pair_folder, swath_name, lay=None):
        """Locate Step 19 SNAPHU stack subfolder; try merged/single-swath basename variants."""
        for prefix in self.snaphu_stack_subfolder_prefix_candidates(swath_name, lay):
            name = f"{pair_folder}{prefix}"
            path = os.path.join(pair_folder_path, name)
            if os.path.isdir(path):
                return path, name
        if os.path.isdir(pair_folder_path):
            for item in sorted(os.listdir(pair_folder_path)):
                if not item.startswith(pair_folder):
                    continue
                if '_Stack_esd_mask' not in item:
                    continue
                path = os.path.join(pair_folder_path, item)
                if os.path.isdir(path):
                    return path, item
        return None, None

    def resolve_snaphu_stack_dim_path(self, ml_flt_dir, pair_folder, swath_name, lay=None):
        """Resolve Step 19/22 ``-Pstack`` / ``-Ssource`` .dim (merged or single-swath suffix variants)."""
        lay = lay or self._post_goldstein_ml_flt_layout()
        _, primary_suffix = self.snaphu_source_base_dir_and_suffix(swath_name, lay)
        suffixes = [primary_suffix]
        for prefix in self.snaphu_stack_subfolder_prefix_candidates(swath_name, lay)[1:]:
            sfx = f"{prefix}.dim"
            if sfx not in suffixes:
                suffixes.append(sfx)
        for sfx in suffixes:
            path = os.path.join(ml_flt_dir, pair_folder, f"{pair_folder}{sfx}")
            if os.path.isfile(path):
                return path, sfx
        return None, None
    
    def _eligible_pairs_liicsbas_skip_master_involved(self, found_pairs):
        eligible = []
        m = self.master_date
        for pair_name in sorted(found_pairs):
            if self.insar_target == 1:
                parts = pair_name.split('_')
                if len(parts) == 2 and (parts[0] == m or parts[1] == m):
                    continue
            eligible.append(pair_name)
        return eligible
    
    def _validate_geom_name_in_import_dim_xml(self, dim_path, geometry_name):
        if not geometry_name:
            return True
        if not os.path.exists(dim_path):
            print(f"  ⚠ Geometry check skipped (no DIM yet): {dim_path}")
            return True
        try:
            with open(dim_path, 'r', encoding='utf-8', errors='ignore') as fh:
                blob = fh.read()
        except OSError as e:
            print(f"  ⚠ Could not read DIM for geometry check: {dim_path}: {e}")
            return True
        if '<Masks>' not in blob:
            print(f"  ⚠ Imported DIM has no `<Masks>` section: {dim_path}")
            return True
        if f'<NAME value="{geometry_name}"' not in blob and f'<NAME value=\'{geometry_name}\'' not in blob:
            print(f"  ⚠ Expected mask NAME `{geometry_name}` not found in {dim_path}")
            return False
        print(f"  ✓ Import-Vector mask NAME matches `{geometry_name}`")
        return True
    
    
    def step14_goldstein_filtering(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 18: GoldsteinPhaseFiltering - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 18: GoldsteinPhaseFiltering")
        print(f"{'='*80}")
        
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        
        use_subset = False
        if use_merged:
            merge_folder_name = self.get_merge_folder_name(swath_config)
            merge_ml_subset_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_ml_subset_{self.master_date}')
            merge_ml_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_ml_{self.master_date}')
            
            if os.path.exists(merge_ml_subset_dir) and os.listdir(merge_ml_subset_dir):
                use_subset = True
                use_merged = True
                print(f"  Using merged subset multilook inputs from {merge_folder_name}/multi_intf_deb_mrg_ml_subset_{self.master_date}/")
            elif os.path.exists(merge_ml_dir) and os.listdir(merge_ml_dir):
                use_merged = True
                print(f"  Using merged multilook inputs from {merge_folder_name}/multi_intf_deb_mrg_ml_{self.master_date}/")
            else:
                use_merged = False
        
        if not use_merged and not use_subset:
            for swath_name in self.swaths_to_process:
                subset_ml_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_ml_subset_{self.master_date}')
                if os.path.exists(subset_ml_dir) and os.listdir(subset_ml_dir):
                    use_subset = True
                    print(f"  Detected subset multilook layout (subset ML folders)")
                    break
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            if use_merged:
                if use_subset:
                    input_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_ml_subset_{self.master_date}')
                    input_suffix = "_mrg_ml_subset"
                    output_suffix = "_mrg_ml_flt_subset"
                    output_base_name = f'multi_intf_deb_mrg_ml_flt_subset_{self.master_date}'
                else:
                    input_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_ml_{self.master_date}')
                    input_suffix = "_mrg_ml"
                    output_suffix = "_mrg_ml_flt"
                    output_base_name = f'multi_intf_deb_mrg_ml_flt_{self.master_date}'
            else:
                if use_subset:
                    input_base_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_ml_subset_{self.master_date}')
                    input_suffix = "_ml_subset"
                    output_suffix = "_ml_flt_subset"
                    output_base_name = f'multi_intf_deb_ml_flt_subset_{self.master_date}'
                else:
                    input_base_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_ml_{self.master_date}')
                    input_suffix = "_ml"
                    output_suffix = "_ml_flt"
                    output_base_name = f'multi_intf_deb_ml_flt_{self.master_date}'
            
            if not os.path.exists(input_base_dir):
                print(f"  ⚠ Input directory not found: {input_base_dir}")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(input_base_dir)
            
            if not found_pairs:
                print(f"  ⚠ No pairs found in previous step output")
                continue
            
            print(f"  Found {len(found_pairs)} pair(s)")
            
            def goldstein_io_paths(pair_name):
                input_file = os.path.join(input_base_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb_mmifg{input_suffix}.dim")
                if use_merged:
                    output_dir = os.path.join(self.proj_root, merge_folder_name, output_base_name, pair_name)
                else:
                    output_dir = os.path.join(self.proj_root, swath_name, output_base_name, pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg{output_suffix}.dim")
                return input_file, output_file
            
            eligible = []
            for pair_name in sorted(found_pairs):
                if self.insar_target == 1:
                    parts = pair_name.split('_')
                    if len(parts) == 2 and (parts[0] == self.master_date or parts[1] == self.master_date):
                        continue
                eligible.append(pair_name)
            
            if not eligible:
                print(f"  ⚠ No eligible pairs (LiCSBAS folder involves epoch master)")
                continue
            
            commands_data = []
            for pair_name in eligible:
                input_file, output_file = goldstein_io_paths(pair_name)
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input file not found")
                    continue
                if self.check_output_file_valid(output_file):
                    continue
                cmd = [
                    *self.gpt_base_cmd(),
                    'GoldsteinPhaseFiltering',
                    f"-SsourceProduct={input_file}",
                    '-t', output_file
                ]
                commands_data.append((cmd, f"GoldsteinFilter {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All eligible Goldstein outputs already exist for this swath")
            else:
                print(f"\n  Processing {len(commands_data)} Goldstein filtering operation(s)...")
                gf_exec_data = [(cmd, desc, name) for cmd, desc, name, _ in commands_data]
                success_list, failed_list = self.run_commands_parallel(
                    gf_exec_data,
                    f"Step 18 - GoldsteinPhaseFiltering ({swath_name})"
                )
                if failed_list:
                    print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                    return False
            
            if use_merged:
                break
        
        return True
    
    
    def step_import_vector_mask_shp(self):
        """Import-Vector AOI: runs on subset interferograms (after Step 9), before bandmath / multilook."""
        vec_path = self.get_msk_shp_path()
        if not vec_path:
            print(f"\n{'='*80}")
            print(f"STEP 10: Import-Vector (AOI mask) - SKIPPED")
            print(f"{'='*80}")
            print(f"  No msk_shp / shp_mask in configuration.\n")
            return True
        
        if not os.path.isfile(vec_path):
            print(f"\n{'='*80}")
            print(f"ERROR: AOI vector file not found:\n  {vec_path}")
            print(f"{'='*80}\n")
            return False
        
        print(f"\n{'='*80}")
        print(f"STEP 10: Import-Vector (AOI mask)")
        print(f"{'='*80}")
        print(f"  Vector: {vec_path}")
        print(
            "  Input: subset / merged interferogram stacks "
            "(multi_intf_deb_*subset* or multi_intf_deb_mrg_*); LiCSBAS → Step 14 multilook, "
            "StaMPS → Step 12 bandmath"
        )
        
        lay = self._post_subset_interferogram_layout()
        commands_data = []
        
        for swath_name in self.swaths_to_process:
            intf_base = self._deburst_intf_base_for_swath(swath_name, lay)
            if not intf_base:
                if lay['use_merged']:
                    hint = os.path.join(
                        self.proj_root, lay['merge_folder_name'],
                        f'multi_intf_deb_mrg_subset_{self.master_date}')
                else:
                    hint = os.path.join(
                        self.proj_root, swath_name,
                        f'multi_intf_deb_subset_{self.master_date}')
                print(f"  ⚠ Interferogram folder not found: {hint}")
                continue

            intf_suffix = self._stack_suffix_from_pre_ml_intf_basename(os.path.basename(intf_base))
            parent = os.path.dirname(intf_base)
            shp_base_name = self._intf_folder_basename_intf_to_ml_flt_shp(os.path.basename(intf_base))
            out_base_dir = os.path.join(parent, shp_base_name)
            shp_suffix = self._stack_suffix_intf_mmifg_to_ml_flt_shp(intf_suffix)
            
            found_pairs = self.list_pair_dirs_check_only(intf_base)
            if self.insar_target == 1:
                eligible = self._eligible_pairs_liicsbas_skip_master_involved(found_pairs)
            else:
                eligible = sorted(found_pairs)
            
            print(f"\nProcessing swath: {swath_name}")
            os.makedirs(out_base_dir, exist_ok=True)
            
            for pair_name in sorted(eligible):
                input_file = os.path.join(intf_base, pair_name, f"{pair_name}{intf_suffix}")
                output_file = os.path.join(out_base_dir, pair_name, f"{pair_name}{shp_suffix}")
                if not os.path.isfile(input_file):
                    print(f"  ⚠ Skipping {pair_name}: missing input {input_file}")
                    continue
                
                os.makedirs(os.path.dirname(output_file), exist_ok=True)
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (Import-Vector product exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'Import-Vector',
                    f'-Ssource={input_file}',
                    f'-PvectorFile={vec_path}',
                    '-t',
                    output_file,
                ]
                commands_data.append((cmd, f"Import-Vector {pair_name}", pair_name, output_file))
            
            if lay['use_merged']:
                break
        
        if not commands_data:
            print(f"\n  All Import-Vector products already exist or no pairs to process.\n")
            return True
        
        print(f"\n  Processing {len(commands_data)} Import-Vector operation(s)...")
        succ, fail = self.run_commands_parallel(commands_data, "Step 10 - Import-Vector AOI")
        if fail:
            print(f"\n  ✗ Import-Vector failed for {len(fail)} pair(s)")
            return False
        print(f"\n  ✓ Step 10 completed\n")
        return True
    
    
    def step_landsea_mask_vector_geometry(self):
        """Apply vector geometry mask to stacks produced by Import-Vector (subset-based)."""
        if not self.get_msk_shp_path():
            print(f"\n{'='*80}")
            print(f"STEP 11: Land-Sea-Mask (AOI geometry) - SKIPPED")
            print(f"{'='*80}\n")
            return True
        
        geometry_name = self.geometry_name_from_msk_shp()
        lay = self._post_subset_interferogram_layout()
        
        geom_probe_done = False
        commands_data = []
        invert_geom = self.config_invert_mask()
        
        print(f"\n{'='*80}")
        print(f"STEP 11: Land-Sea-Mask (AOI geometry)")
        print(f"{'='*80}")
        print(f"  geometry={geometry_name}, landMask=false, invertGeometry={invert_geom}")
        
        for swath_name in self.swaths_to_process:
            intf_base = self._deburst_intf_base_for_swath(swath_name, lay)
            if not intf_base:
                if lay['use_merged']:
                    hint = os.path.join(
                        self.proj_root, lay['merge_folder_name'],
                        f'multi_intf_deb_mrg_subset_{self.master_date}')
                else:
                    hint = os.path.join(
                        self.proj_root, swath_name,
                        f'multi_intf_deb_subset_{self.master_date}')
                print(f"  ⚠ Import-Vector folder not found: {hint}")
                continue

            intf_suffix = self._stack_suffix_from_pre_ml_intf_basename(os.path.basename(intf_base))
            parent = os.path.dirname(intf_base)
            shp_bn = self._intf_folder_basename_intf_to_ml_flt_shp(os.path.basename(intf_base))
            mask_bn = self._intf_folder_basename_shp_to_mask(shp_bn)
            shp_base = os.path.join(parent, shp_bn)
            mask_base = os.path.join(parent, mask_bn)

            shp_suffix = self._stack_suffix_intf_mmifg_to_ml_flt_shp(intf_suffix)
            mask_suffix = self._stack_suffix_shp_to_mask(shp_suffix)
            
            if not os.path.isdir(shp_base):
                print(f"  ⚠ Step 10 output folder not found: {shp_base}")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(shp_base)
            if self.insar_target == 1:
                eligible = sorted(self._eligible_pairs_liicsbas_skip_master_involved(found_pairs))
            else:
                eligible = sorted(found_pairs)
            
            if eligible and not geom_probe_done:
                probe_pair = eligible[0]
                probe_dim = os.path.join(shp_base, probe_pair, f"{probe_pair}{shp_suffix}")
                self._validate_geom_name_in_import_dim_xml(probe_dim, geometry_name)
                geom_probe_done = True
            
            print(f"\nProcessing swath: {swath_name}")
            os.makedirs(mask_base, exist_ok=True)
            
            for pair_name in eligible:
                src = os.path.join(shp_base, pair_name, f"{pair_name}{shp_suffix}")
                dst = os.path.join(mask_base, pair_name, f"{pair_name}{mask_suffix}")
                if not os.path.isfile(src):
                    print(f"  ⚠ Skipping {pair_name}: missing shp-stack {src}")
                    continue
                
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if self.check_output_file_valid(dst):
                    print(f"  Skipping {pair_name} (masked stack exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'Land-Sea-Mask',
                    f'-Ssource={src}',
                ]
                if invert_geom:
                    cmd.append('-PinvertGeometry=true')
                cmd.extend([
                    f'-Pgeometry={geometry_name}',
                    '-PlandMask=false',
                    '-t',
                    dst,
                ])
                commands_data.append((cmd, f"Land-Sea-Mask AOI {pair_name}", pair_name, dst))
            
            if lay['use_merged']:
                break
        
        if not commands_data:
            print(f"\n  All masked stacks already exist or nothing to process.\n")
            return True
        
        print(f"\n  Processing {len(commands_data)} Land-Sea-Mask operation(s)...")
        succ, fail = self.run_commands_parallel(commands_data, "Step 11 - Land-Sea-Mask AOI")
        if fail:
            print(f"\n  ✗ Land-Sea-Mask failed for {len(fail)} pair(s)")
            return False
        print(f"\n  ✓ Step 11 completed\n")
        return True
    
    
    def step15_snaphu_export(self):
        """Step 19: SnaphuExport (LiCSBAS: Step 18 Goldstein stacks; same tree/suffix as Step 22 `-Pstack`)."""
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 19: SnaphuExport - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 19: SnaphuExport")
        print(f"{'='*80}")
        
        lay = self._post_goldstein_ml_flt_layout()
        _, suf = self.snaphu_source_base_dir_and_suffix(
            self.swaths_to_process[0], lay)
        print(
            f"  SNAPHU -Ssource (LiCSBAS): Step 18 Goldstein-filtered stacks "
            f"(`{os.path.basename(self._ml_flt_input_base_dir(self.swaths_to_process[0], lay))}/`); "
            f"filename suffix `{suf}`"
        )
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            input_base_dir, file_suffix = self.snaphu_source_base_dir_and_suffix(swath_name, lay)
            
            if lay['use_merged']:
                output_base_dir = os.path.join(
                    self.proj_root, lay['merge_folder_name'], f'multi_snaphu_{self.master_date}')
            else:
                output_base_dir = os.path.join(self.proj_root, swath_name, f'multi_snaphu_{self.master_date}')
            
            if not os.path.exists(input_base_dir):
                print(f"  ⚠ Input directory not found: {input_base_dir}")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(input_base_dir)
            if not found_pairs:
                print(f"  ⚠ No pairs found in previous step output")
                continue
            
            print(f"  Found {len(found_pairs)} pair(s)")
            commands_data = []
            
            for pair_name in found_pairs:
                input_file = os.path.join(input_base_dir, pair_name, f"{pair_name}{file_suffix}")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input file not found")
                    continue
                
                output_dir = os.path.join(output_base_dir, pair_name)
                input_basename = os.path.splitext(os.path.basename(input_file))[0]
                target_folder = os.path.join(output_dir, input_basename)
                
                if os.path.exists(target_folder) and os.path.isdir(target_folder):
                    existing_files = os.listdir(target_folder)
                    if existing_files:
                        print(f"  Skipping {pair_name}: Output folder exists and not empty")
                        print(f"    Path: {target_folder}")
                        print(f"    Files found: {len(existing_files)}")
                        continue
                
                os.makedirs(output_dir, exist_ok=True)
                
                print(f"\n  Calculating tile parameters for {pair_name}...")
                tile_params = self.calculate_snaphu_tile_parameters(input_file)
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'SnaphuExport',
                    f"-Ssource={input_file}",
                    f"-PtargetFolder={output_dir}",
                    f"-PstatCostMode={self.cost_mode}",
                    f"-PinitMethod={self.init_method}",
                    f"-PnumberOfTileRows={tile_params['numberOfTileRows_tile']}",
                    f"-PnumberOfTileCols={tile_params['numberOfTileCols_tile']}",
                    "-PnumberOfProcessors=4",
                    f"-ProwOverlap={tile_params['rowOverlap']}",
                    f"-PcolOverlap={tile_params['colOverlap']}",
                    "-PtileCostThreshold=500",
                ]
                commands_data.append((cmd, f"SnaphuExport {pair_name} ({tile_params['numberOfTileRows_tile']}x{tile_params['numberOfTileCols_tile']} tiles)", pair_name))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} SnaphuExport operations...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 19 - SnaphuExport ({swath_name})",
            )
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
            
            if lay['use_merged']:
                break
        
        return True
    
    
    def preprocess_snaphu_conf_files(self):
        print(f"\n{'='*80}")
        print(f"PREPROCESSING: Filtering SNAPHU conf and hdr files (with supermaster bridging)")
        print(f"{'='*80}")
        
        try:
            all_pair_rows = self.read_sbas_pair_rows()
        except FileNotFoundError as e:
            print(f"\n  ✗ {e}")
            return False
        
        if self.is_check_only():
            print(f"\n  Step 20: Loaded sbas_pairs.txt ({len(all_pair_rows)} rows); SNAPHU bridge retention disabled (check_only=True).")
        else:
            print(f"\n  Step 20: Bridge classification uses full sbas_pairs.txt ({len(all_pair_rows)} pair rows; ignores check_only subset)")
        
        processed_pairs = []
        skipped_pairs = []
        
        if self.is_check_only():
            print(f"\n  check_only=True: supermaster SNAPHU bridging disabled (subset workflow).")
            skipped_pairs_coverage = {}
            processed_pairs = [
                {'date1': p['date1'], 'date2': p['date2'],
                 'pair_name': f"{p['date1']}_{p['date2']}"}
                for p in self.sbas_pairs
            ]
            print(f"\nSupermaster date: {self.master_date}")
            print(f"\n{'─'*80}")
            print(f"PAIR CLASSIFICATION (subset only; no bridge targets):")
            print(f"{'─'*80}")
            for pi in processed_pairs:
                print(f"  PROCESS: {pi['date1']} {pi['date2']}")
            print(f"\nProcessed pairs: {len(processed_pairs)}")
            print(f"Skipped pairs (bridging): 0")
        else:
            print(f"\nSupermaster date: {self.master_date}")
            print(f"\n{'─'*80}")
            print(f"PAIR CLASSIFICATION:")
            print(f"{'─'*80}")
            
            for row in all_pair_rows:
                date1 = row['date1']
                date2 = row['date2']
                pair_info = {'date1': date1, 'date2': date2, 'pair_name': row['pair_name']}
                
                if self._skip_pair_step03_rule(date1, date2):
                    skipped_pairs.append(pair_info)
                    print(f"  SKIP: {date1} {date2} (SBAS pair contains epoch master — bridged IFGs only)")
                else:
                    processed_pairs.append(pair_info)
                    print(f"  PROCESS: {date1} {date2}")
            
            print(f"\nProcessed pairs: {len(processed_pairs)}")
            print(f"Skipped pairs: {len(skipped_pairs)}")
            
            skipped_pairs_coverage = {sp['pair_name']: False for sp in skipped_pairs}
        
        lay = self._post_goldstein_ml_flt_layout()
        self._corr_postprocess_conf_paths = []
        
        total_deleted = 0
        total_kept = 0
        
        for swath_name in self.swaths_to_process:
            print(f"\n{'='*80}")
            print(f"Processing swath: {swath_name}")
            print(f"{'='*80}")
            
            if lay['use_merged']:
                multi_snaphu_dir = os.path.join(self.proj_root, lay['merge_folder_name'], f'multi_snaphu_{self.master_date}')
            else:
                multi_snaphu_dir = os.path.join(self.proj_root, swath_name, f'multi_snaphu_{self.master_date}')
            
            if not os.path.exists(multi_snaphu_dir):
                print(f"  ⚠ Multi-snaphu directory not found: {multi_snaphu_dir}")
                continue
            
            pair_folders = [d for d in os.listdir(multi_snaphu_dir) 
                          if os.path.isdir(os.path.join(multi_snaphu_dir, d))]
            
            bridge_conf_claimed = {}
            
            for pair_folder in sorted(pair_folders):
                pair_folder_path = os.path.join(multi_snaphu_dir, pair_folder)
                
                stack_prefix = self.snaphu_stack_subfolder_prefix_only(swath_name)
                subfolder_path, subfolder_name = self.resolve_snaphu_stack_subfolder(
                    pair_folder_path, pair_folder, swath_name, lay)
                
                if not subfolder_path:
                    print(f"\n  ⚠ SNAPHU subfolder not found for {pair_folder} "
                          f"(tried: {pair_folder}{stack_prefix}, merged/single-swath variants)")
                    continue
                if subfolder_name != f"{pair_folder}{stack_prefix}":
                    print(f"  Using SNAPHU subfolder: {subfolder_name}")
                
                folder_parts = pair_folder.split('_')
                if len(folder_parts) != 2:
                    print(f"\n  ⚠ Invalid folder name format: {pair_folder}")
                    continue
                
                folder_date1 = folder_parts[0]
                folder_date2 = folder_parts[1]
                
                print(f"\n{'─'*80}")
                print(f"Folder: {pair_folder}")
                print(f"{'─'*80}")
                
                all_conf_files = [f for f in os.listdir(subfolder_path) 
                                 if f.endswith('snaphu.conf') and f != 'snaphu.conf']
                
                if not all_conf_files:
                    print(f"  No conf files found")
                    continue
                
                print(f"  Found {len(all_conf_files)} conf files")
                
                conf_files_to_keep = []
                
                folder_date1_conf = self.convert_date_to_conf_format(folder_date1)
                folder_date2_conf = self.convert_date_to_conf_format(folder_date2)
                primary_conf = f"{folder_date1_conf}_{folder_date2_conf}snaphu.conf"
                
                if primary_conf in all_conf_files:
                    conf_files_to_keep.append(primary_conf)
                    print(f"  ✓ PRIMARY: {primary_conf}")
                else:
                    print(f"  ⚠ Primary conf not found: {primary_conf}")
                
                print(f"\n  Checking for bridges to skipped pairs...")
                bridges_added_here = 0
                
                for skipped_pair in skipped_pairs:
                    skip_date1 = skipped_pair['date1']
                    skip_date2 = skipped_pair['date2']
                    skip_pair_name = skipped_pair['pair_name']
                    
                    bridge_confs = []
                    
                    if folder_date1 == skip_date1:
                        date1_conf = self.convert_date_to_conf_format(folder_date1)
                        date2_conf = self.convert_date_to_conf_format(skip_date2)
                        bridge_conf = f"{date1_conf}_{date2_conf}snaphu.conf"
                        if bridge_conf in all_conf_files and bridge_conf not in conf_files_to_keep:
                            bridge_confs.append(bridge_conf)
                    
                    if folder_date1 == skip_date2:
                        date1_conf = self.convert_date_to_conf_format(skip_date1)
                        date2_conf = self.convert_date_to_conf_format(folder_date1)
                        bridge_conf = f"{date1_conf}_{date2_conf}snaphu.conf"
                        if bridge_conf in all_conf_files and bridge_conf not in conf_files_to_keep:
                            bridge_confs.append(bridge_conf)
                    
                    if folder_date2 == skip_date1:
                        date1_conf = self.convert_date_to_conf_format(folder_date2)
                        date2_conf = self.convert_date_to_conf_format(skip_date2)
                        bridge_conf = f"{date1_conf}_{date2_conf}snaphu.conf"
                        if bridge_conf in all_conf_files and bridge_conf not in conf_files_to_keep:
                            bridge_confs.append(bridge_conf)
                    
                    if folder_date2 == skip_date2:
                        date1_conf = self.convert_date_to_conf_format(skip_date1)
                        date2_conf = self.convert_date_to_conf_format(folder_date2)
                        bridge_conf = f"{date1_conf}_{date2_conf}snaphu.conf"
                        if bridge_conf in all_conf_files and bridge_conf not in conf_files_to_keep:
                            bridge_confs.append(bridge_conf)
                    
                    for bridge_conf in bridge_confs:
                        conf_files_to_keep.append(bridge_conf)
                        skipped_pairs_coverage[skip_pair_name] = True
                        bridges_added_here += 1
                        print(f"    ✓ BRIDGE to {skip_pair_name}: {bridge_conf}")
                
                if skipped_pairs and bridges_added_here == 0:
                    print(f"    (no bridge .conf matched any skipped SBAS pair for this folder)")
                
                deduped_keep = []
                duplicate_bridge_drop = {}
                primary_in_list = primary_conf in conf_files_to_keep
                for c in conf_files_to_keep:
                    if c == primary_conf:
                        if primary_in_list and primary_conf not in deduped_keep:
                            deduped_keep.append(c)
                        continue
                    if c in bridge_conf_claimed:
                        duplicate_bridge_drop[c] = bridge_conf_claimed[c]
                        continue
                    bridge_conf_claimed[c] = pair_folder
                    deduped_keep.append(c)
                conf_files_to_keep = deduped_keep
                
                deleted_conf = 0
                deleted_hdr = 0
                
                print(f"\n  Processing {len(all_conf_files)} conf files...")
                for conf_file in all_conf_files:
                    if conf_file in conf_files_to_keep:
                        total_kept += 1
                        self._corr_postprocess_conf_paths.append(os.path.join(subfolder_path, conf_file))
                        print(f"    ✓ KEEP: {conf_file}")
                    else:
                        conf_path = os.path.join(subfolder_path, conf_file)
                        try:
                            os.remove(conf_path)
                            deleted_conf += 1
                            if conf_file in duplicate_bridge_drop:
                                dup_note = (
                                    f"  (duplicate bridge; kept in folder "
                                    f"{duplicate_bridge_drop[conf_file]})"
                                )
                                print(f"    ✗ DELETE conf: {conf_file}{dup_note}")
                            else:
                                print(f"    ✗ DELETE conf: {conf_file}")
                            hdr_filename = self.resolve_hdr_filename_from_conf(
                                conf_file, swath_name, subfolder_path)
                            if hdr_filename:
                                hdr_path = os.path.join(subfolder_path, hdr_filename)
                                if os.path.exists(hdr_path):
                                    os.remove(hdr_path)
                                    deleted_hdr += 1
                                    print(f"    ✗ DELETE hdr:  {hdr_filename}")
                        except Exception as e:
                            print(f"    ⚠ Failed to delete {conf_file}: {e}")
                
                remaining_conf_files = [f for f in os.listdir(subfolder_path) 
                                       if f.endswith('snaphu.conf') and f != 'snaphu.conf']
                
                all_hdr_files = [f for f in os.listdir(subfolder_path) 
                                if f.startswith('UnwPhase_ifg_') and f.endswith('.snaphu.hdr')]
                
                orphaned_hdr = 0
                for hdr_file in all_hdr_files:
                    has_matching_conf = False
                    for conf_file in remaining_conf_files:
                        if hdr_file in self._hdr_candidates_from_conf(conf_file, swath_name):
                            has_matching_conf = True
                            break
                    
                    if not has_matching_conf:
                        hdr_path = os.path.join(subfolder_path, hdr_file)
                        try:
                            os.remove(hdr_path)
                            orphaned_hdr += 1
                            print(f"    ✗ DELETE orphaned hdr: {hdr_file}")
                        except Exception as e:
                            print(f"    ⚠ Failed to delete {hdr_file}: {e}")
                
                total_deleted += deleted_conf + deleted_hdr + orphaned_hdr
                
                if deleted_conf > 0 or deleted_hdr > 0 or orphaned_hdr > 0:
                    print(f"\n  Summary: Deleted {deleted_conf} conf, {deleted_hdr} hdr, {orphaned_hdr} orphaned hdr")
            
            if lay['use_merged']:
                break
        
        uncovered_pairs = [pair_name for pair_name, covered in skipped_pairs_coverage.items() if not covered]
        
        print(f"\n{'='*80}")
        print(f"PREPROCESSING COMPLETE")
        print(f"{'='*80}")
        print(f"  Total kept: {total_kept} conf files")
        print(f"  Total deleted: {total_deleted} files")
        
        if uncovered_pairs:
            print(f"\n{'─'*80}")
            print(f"WARNING: Skipped SBAS pairs (involving master) with no .conf files found:")
            print(f"{'─'*80}")
            for pair_name in uncovered_pairs:
                print(f"  ⚠ {pair_name}")
            print(f"\nThese pairs have no dedicated stack; ensure at least one processed folder exports a bridge .conf.")
        
        print(f"{'='*80}\n")
        return True
    
    def _corrfile_img_name_from_snaphu_conf(self, conf_path):
        """Return coherence `.img` filename from `CORRFILE` line (second token, ends with `.img`)."""
        try:
            with open(conf_path, 'r', errors='ignore') as fh:
                for line in fh:
                    s = line.strip()
                    if not s.startswith('CORRFILE'):
                        continue
                    parts = s.split()
                    if len(parts) >= 2 and parts[1].endswith('.img'):
                        return parts[1]
        except OSError:
            return None
        return None
    
    def _hdr_path_for_flat_img(self, img_path):
        stem = os.path.splitext(img_path)[0]
        for c in (stem + '.hdr', img_path + '.hdr'):
            if os.path.isfile(c):
                return c
        return None

    def _read_envi_hdr_samples_lines(self, hdr_path):
        samples = lines = None
        try:
            with open(hdr_path, 'r', errors='ignore') as fh:
                for line in fh:
                    key, _, val = line.partition('=')
                    key = key.strip().lower()
                    if key == 'samples':
                        samples = int(val.strip())
                    elif key == 'lines':
                        lines = int(val.strip())
        except (OSError, ValueError):
            return None, None
        return samples, lines

    def _parse_snaphu_conf_phase_corr(self, conf_path):
        phase_img = corr_img = None
        try:
            with open(conf_path, 'r', errors='ignore') as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith('CORRFILE'):
                        parts = s.split()
                        if len(parts) >= 2 and parts[1].endswith('.img'):
                            corr_img = parts[1]
                    elif 'Phase_ifg_' in s and '.snaphu.img' in s:
                        for part in s.split():
                            if part.startswith('Phase_ifg_') and part.endswith('.snaphu.img'):
                                phase_img = part
                                break
        except OSError:
            return None, None
        return phase_img, corr_img

    def _validate_snaphu_conf_phase_coh_dims(self, conf_path, subfolder_path):
        """Ensure Phase and CORRFILE ENVI rasters share samples/lines before SNAPHU."""
        phase_img, corr_img = self._parse_snaphu_conf_phase_corr(conf_path)
        if not phase_img or not corr_img:
            return False, (
                f"Cannot find Phase/CORRFILE entries in {os.path.basename(conf_path)}"
            )

        phase_hdr = self._hdr_path_for_flat_img(os.path.join(subfolder_path, phase_img))
        corr_hdr = self._hdr_path_for_flat_img(os.path.join(subfolder_path, corr_img))
        if not phase_hdr or not corr_hdr:
            return False, (
                f"Missing .hdr for Phase or CORRFILE next to {os.path.basename(conf_path)}"
            )

        ps, pl = self._read_envi_hdr_samples_lines(phase_hdr)
        cs, cl = self._read_envi_hdr_samples_lines(corr_hdr)
        if None in (ps, pl, cs, cl):
            return False, (
                f"Cannot parse samples/lines from Phase/CORRFILE .hdr files for "
                f"{os.path.basename(conf_path)}"
            )

        if ps == cs and pl == cl:
            return True, None

        master = self.master_date or 'MASTER'
        return False, (
            f"Phase/CORRFILE dimension mismatch in {os.path.basename(conf_path)}:\n"
            f"  Phase: {os.path.basename(phase_hdr)}  samples={ps}, lines={pl}\n"
            f"  Coherence: {os.path.basename(corr_hdr)}  samples={cs}, lines={cl}\n"
            f"\n"
            f"  SNAPHU cannot unwrap when Phase and coherence raster sizes differ.\n"
            f"  ACTION REQUIRED:\n"
            f"    1. Change the 'Range' value in your config file.\n"
            f"    2. Delete folder: multi_snaphu_{master}\n"
            f"    3. Re-run processing from Step 14 onward.\n"
        )
    
    def postprocess_corrfile_coherence_nan2zero(self):
        """Apply phase_coh_mask_th to coherence `.img` files listed as CORRFILE in kept snaphu confs."""
        conf_paths = list(dict.fromkeys(getattr(self, '_corr_postprocess_conf_paths', []) or []))
        
        print(f"\n{'='*80}")
        print(f"CORRFILE coherence post-processing (phase_coh_mask_th)")
        print(f"{'='*80}")
        
        try:
            th = float(self.phase_coh_mask_th)
        except (TypeError, ValueError):
            th = float('nan')
        
        if not (th > 0 and th == th):
            print(f"  Skipped: phase_coh_mask_th is not a positive number (disable with 0 or omit).\n")
            return True
        
        if not conf_paths:
            print(f"  No kept .conf paths recorded; nothing to do.\n")
            return True
        
        try:
            import numpy as np
        except ImportError:
            print(f"  ✗ numpy is required for CORRFILE post-processing.\n")
            return False
        
        plt_mod = None
        mcolors_mod = None
        MpPatch = None
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt_mod
            import matplotlib.colors as mcolors_mod
            from matplotlib.patches import Patch as MpPatch
        except ImportError:
            print(f"  ⚠ matplotlib not installed — .img will be updated without diagnostic PNGs")
        
        n_done = 0
        for conf_path in conf_paths:
            base = os.path.basename(conf_path)
            rel_img = self._corrfile_img_name_from_snaphu_conf(conf_path)
            if not rel_img:
                print(f"  ⚠ No CORRFILE … .img in {conf_path}")
                continue
            
            img_path = os.path.join(os.path.dirname(conf_path), rel_img)
            if not os.path.isfile(img_path):
                print(f"  ⚠ CORRFILE image missing: {img_path}")
                continue
            
            hdr_path = self._hdr_path_for_flat_img(img_path)
            if not hdr_path:
                print(f"  ⚠ No ENVI header for {img_path}")
                continue
            
            wh = self.parse_envi_hdr_samples_lines(hdr_path)
            if not wh:
                print(f"  ⚠ Could not parse samples/lines from {hdr_path}")
                continue
            ncols, nrows = wh
            
            raw = np.fromfile(img_path, dtype=np.float32)
            expected = nrows * ncols
            if raw.size != expected:
                print(f"  ⚠ Size mismatch {img_path}: got {raw.size} floats, expect {expected}")
                continue
            
            arr = raw.reshape(nrows, ncols)
            
            nan_mask = ~np.isfinite(arr)
            nan_ct = int(nan_mask.sum())
            arr[~np.isfinite(arr)] = 0.0
            below_mask = (arr != 0) & (arr < th)
            below_ct = int(below_mask.sum())
            arr[below_mask] = 0.0
            
            arr.astype(np.float32).tofile(img_path)
            n_done += 1
            print(f"  ✓ {os.path.basename(img_path)} — NaN/Inf→0: {nan_ct} px,  <{th}→0: {below_ct} px")
            
            if plt_mod is not None and mcolors_mod is not None and MpPatch is not None:
                try:
                    data = arr
                    total_px = int(data.size)
                    vmin = float(np.min(data))
                    vmax = float(np.max(data))
                    vmean = float(np.mean(data))
                    cmap_obj = plt_mod.get_cmap('viridis').copy()
                    norm = mcolors_mod.Normalize(vmin=vmin, vmax=vmax)
                    category_map = np.zeros(data.shape, dtype=np.uint8)
                    category_map[nan_mask] = 1
                    category_map[below_mask] = 2
                    zeroed_total = nan_ct + below_ct
                    any_zeroed = zeroed_total > 0
                    aspect_ratio = (nrows / ncols) if ncols else 1.0
                    fig_w = 7
                    fig_h = max(5.0, fig_w * aspect_ratio + 1.5)
                    fig, axes = plt_mod.subplots(
                        1, 2, figsize=(fig_w * 2 + 0.5, fig_h), dpi=130,
                        gridspec_kw={'width_ratios': [1, 1], 'wspace': 0.35}
                    )
                    fig.patch.set_facecolor('#1c1c1c')
                    bn = os.path.basename(img_path)
                    fn_low = bn.lower()
                    if 'coh' in fn_low:
                        cbar_lbl = 'Coherence'
                    elif 'phase' in fn_low:
                        cbar_lbl = 'Phase (radians)'
                    else:
                        cbar_lbl = 'Pixel value'
                    ax = axes[0]
                    ax.set_facecolor('#1c1c1c')
                    im = ax.imshow(data, cmap=cmap_obj, norm=norm,
                                   interpolation='nearest', aspect='auto')
                    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.03)
                    cb.set_label(cbar_lbl, fontsize=9, color='white')
                    cb.ax.yaxis.set_tick_params(color='white', labelcolor='white')
                    cb.outline.set_edgecolor('white')
                    title_parts = []
                    if nan_ct > 0:
                        title_parts.append(f"NaN->0: {nan_ct:,} px")
                    if below_ct > 0:
                        title_parts.append(f"<{th}->0: {below_ct:,} px")
                    left_title = ("After cleaning  (" + "  |  ".join(title_parts) + ")"
                                  if title_parts else "Data (no changes needed)")
                    ax.set_title(left_title, fontsize=8.5, color='white', pad=7)
                    ax.set_xlabel('Range (pixels)', fontsize=8, color='white')
                    ax.set_ylabel('Azimuth (pixels)', fontsize=8, color='white')
                    ax.tick_params(colors='white')
                    for sp in ax.spines.values():
                        sp.set_edgecolor('white')
                    stats_txt = (
                        f"Size : {ncols} x {nrows} px\n"
                        f"Min  : {vmin:.4g}\n"
                        f"Max  : {vmax:.4g}\n"
                        f"Mean : {vmean:.4g}"
                    )
                    ax.text(0.02, 0.98, stats_txt, transform=ax.transAxes,
                            fontsize=7, va='top', ha='left', color='white', fontfamily='monospace',
                            bbox=dict(boxstyle='round,pad=0.4', fc='black', alpha=0.65, ec='grey'))
                    ax2 = axes[1]
                    ax2.set_facecolor('#1c1c1c')
                    if any_zeroed:
                        cat_cmap = mcolors_mod.ListedColormap(['#1a1a2e', '#e94560', '#f5a623'])
                        ax2.imshow(category_map, cmap=cat_cmap, vmin=0, vmax=2,
                                   interpolation='nearest', aspect='auto')
                        legend_handles = [
                            MpPatch(facecolor='#1a1a2e', edgecolor='grey', label='Valid pixel'),
                        ]
                        if nan_ct > 0:
                            pct = 100 * nan_ct / total_px
                            legend_handles.append(
                                MpPatch(
                                    facecolor='#e94560', edgecolor='grey',
                                    label=f'Was NaN/Inf  ({nan_ct:,} px / {pct:.1f}%)'))
                        if below_ct > 0:
                            pct = 100 * below_ct / total_px
                            legend_handles.append(
                                MpPatch(
                                    facecolor='#f5a623', edgecolor='grey',
                                    label=f'Below thresh <{th}  ({below_ct:,} px / {pct:.1f}%)'))
                        ax2.legend(handles=legend_handles, loc='lower left', fontsize=7,
                                   facecolor='#2a2a2a', edgecolor='grey', labelcolor='white')
                    else:
                        ax2.imshow(np.ones((nrows, ncols), dtype=np.uint8),
                                   cmap=mcolors_mod.ListedColormap(['#4dac26']),
                                   interpolation='nearest', aspect='auto')
                        ax2.text(0.5, 0.5, 'OK  No NaN / bad pixels found\nFile is clean',
                                 transform=ax2.transAxes, fontsize=12, color='white',
                                 ha='center', va='center',
                                 bbox=dict(boxstyle='round,pad=0.5', fc='#1c1c1c',
                                           alpha=0.7, ec='grey'))
                    ax2.set_title('Zeroed pixel location map', fontsize=9, color='white', pad=7)
                    ax2.set_xlabel('Range (pixels)', fontsize=8, color='white')
                    ax2.set_ylabel('Azimuth (pixels)', fontsize=8, color='white')
                    ax2.tick_params(colors='white')
                    for sp in ax2.spines.values():
                        sp.set_edgecolor('white')
                    fig.suptitle(bn, fontsize=10, color='white', y=1.01)
                    plt_mod.tight_layout()
                    png_path = os.path.join(os.path.dirname(img_path), os.path.basename(img_path) + '_corr_nan2zero.png')
                    fig.savefig(png_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
                    plt_mod.close(fig)
                    print(f"    PNG: {png_path}")
                except Exception as e:
                    print(f"    ⚠ PNG preview failed: {e}")
        
        print(f"\n  Processed CORRFILE images for {n_done} kept conf(s).")
        print(f"{'='*80}\n")
        return True
    
    def export_phase_png_previews(self):
        print(f"\n  Exporting Phase bands for preview...")
        
        phase_img_dir = os.path.join(self.proj_root, f'phase_img_{self.master_date}')
        os.makedirs(phase_img_dir, exist_ok=True)
        print(f"  Output directory: {phase_img_dir}")
        
        phase_preview_dir = os.path.join(self.proj_root, f'phase_preview_{self.master_date}')
        if os.path.exists(phase_preview_dir):
            import shutil
            shutil.rmtree(phase_preview_dir)
        os.makedirs(phase_preview_dir, exist_ok=True)
        print(f"  Preview directory: {phase_preview_dir}")
        
        lay = self._post_goldstein_ml_flt_layout()
        
        total_exported = 0
        bandmath_graph = self.get_graph_file('step10_phase_preview_bandmath.xml')
        
        if not os.path.exists(bandmath_graph):
            print(f"  ✗ BandMath graph not found: {bandmath_graph}")
            return False
        
        for swath_name in self.swaths_to_process:
            if lay['use_merged']:
                multi_snaphu_dir = os.path.join(self.proj_root, lay['merge_folder_name'], f'multi_snaphu_{self.master_date}')
            else:
                multi_snaphu_dir = os.path.join(self.proj_root, swath_name, f'multi_snaphu_{self.master_date}')
            multi_source_dir = self.snaphu_source_base_dir_and_suffix(swath_name, lay)[0]
            
            if not os.path.exists(multi_snaphu_dir) or not os.path.exists(multi_source_dir):
                continue
            
            for root, dirs, files in os.walk(multi_snaphu_dir):
                for filename in files:
                    if filename.startswith('UnwPhase_ifg_') and filename.endswith('.snaphu.hdr'):
                        
                        base_name = filename.replace('UnwPhase_ifg_', '').replace('.snaphu.hdr', '')
                        
                        parts = base_name.split('_')
                        if len(parts) < 4:
                            continue
                        
                        swath_id = parts[0]
                        pol = parts[1]
                        date_pair = '_'.join(parts[2:])
                        
                        pair_folder = os.path.basename(os.path.dirname(root))
                        
                        source_pair_dir = os.path.join(multi_source_dir, pair_folder)
                        if not os.path.exists(source_pair_dir):
                            continue
                        
                        dim_files = glob.glob(os.path.join(source_pair_dir, '*.dim'))
                        if not dim_files:
                            continue
                        
                        input_band_phase_preview = sorted(dim_files)[0]
                        
                        phase_band_expression = f"atan2(q_ifg_{swath_id}_{pol}_{date_pair},i_ifg_{swath_id}_{pol}_{date_pair})"
                        new_band_name = f"phase_preview_{date_pair}"
                        
                        preview_pair_dir = os.path.join(phase_preview_dir, pair_folder)
                        os.makedirs(preview_pair_dir, exist_ok=True)
                        
                        dim_basename = os.path.basename(input_band_phase_preview).replace('.dim', '_preview.dim')
                        output_phase_band_preview = os.path.join(preview_pair_dir, dim_basename)
                        
                        output_phase_name = f"Phase_ifg_{swath_id}_{pol}_{date_pair}"
                        output_tif = os.path.join(phase_img_dir, f"{output_phase_name}.tif")
                        output_png = os.path.join(phase_img_dir, f"{output_phase_name}.png")
                        
                        if os.path.exists(output_png):
                            continue
                        
                        print(f"  Processing {output_phase_name}...")
                        cmd_bandmath = [
                            *self.gpt_base_cmd(),
                            bandmath_graph,
                            f"-Pinput_band_phase_preview={input_band_phase_preview}",
                            f"-Pphase_band_expression={phase_band_expression}",
                            f"-Pnew_band_name={new_band_name}",
                            f"-Poutput_phase_band_preview={output_phase_band_preview}"
                        ]
                        
                        success_bandmath = self.run_command(cmd_bandmath, f"BandMath phase calculation")
                        
                        if not success_bandmath or not os.path.exists(output_phase_band_preview):
                            print(f"    ✗ BandMath failed for {output_phase_name}")
                            continue
                        
                        cmd_export = [
                            *self.gpt_base_cmd(),
                            'Subset',
                            f"-Ssource={output_phase_band_preview}",
                            f"-PsourceBands={new_band_name}",
                            '-t', output_tif,
                            '-f', 'GeoTIFF'
                        ]
                        
                        success_export = self.run_command(cmd_export, f"Export {new_band_name}")
                        
                        if success_export and os.path.exists(output_tif):
                            png_path = self.generate_png_from_geotiff(output_tif)
                            
                            if png_path and os.path.exists(output_tif):
                                os.remove(output_tif)
                                total_exported += 1
                                print(f"    ✓ {output_phase_name}.png")
            
            if lay['use_merged']:
                break
        
        if total_exported > 0:
            print(f"\n  ✓ Exported {total_exported} Phase band PNGs to {phase_img_dir}")
        else:
            print(f"  No new Phase bands to export")
        
        if os.path.exists(phase_preview_dir):
            import shutil
            shutil.rmtree(phase_preview_dir)
            print(f"  ✓ Cleaned up temporary preview directory: {phase_preview_dir}")
        
        return True
    
    def execute_snaphu_unwrapping(self, snaphu_executable):
        print(f"\n{'='*80}")
        print(f"EXECUTING SNAPHU UNWRAPPING")
        print(f"{'='*80}")
        
        failed_conf_files = self.get_snaphu_failed_conf_files()
        
        if failed_conf_files:
            print(f"\n{'─'*80}")
            print(f"⚠️  Found {len(failed_conf_files)} previously failed .conf files (will be skipped):")
            print(f"{'─'*80}")
            for conf_name in sorted(failed_conf_files):
                print(f"  ✗ {conf_name}")
            print(f"{'─'*80}\n")
        
        lay = self._post_goldstein_ml_flt_layout()
        
        total_unwrapped = 0
        total_skipped = 0
        total_failed = 0
        failed_conf_list = []
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            if lay['use_merged']:
                multi_snaphu_dir = os.path.join(self.proj_root, lay['merge_folder_name'], f'multi_snaphu_{self.master_date}')
            else:
                multi_snaphu_dir = os.path.join(self.proj_root, swath_name, f'multi_snaphu_{self.master_date}')
            
            if not os.path.exists(multi_snaphu_dir):
                print(f"  ⚠ Multi-snaphu directory not found: {multi_snaphu_dir}")
                continue
            
            pair_folders = [d for d in os.listdir(multi_snaphu_dir) 
                          if os.path.isdir(os.path.join(multi_snaphu_dir, d))]
            
            for pair_folder in sorted(pair_folders):
                pair_folder_path = os.path.join(multi_snaphu_dir, pair_folder)
                subfolder_path, subfolder_name = self.resolve_snaphu_stack_subfolder(
                    pair_folder_path, pair_folder, swath_name, lay)
                
                if not subfolder_path:
                    continue
                
                conf_files = [f for f in os.listdir(subfolder_path) 
                             if f.endswith('snaphu.conf') and f != 'snaphu.conf']
                
                if not conf_files:
                    continue
                
                print(f"\n  Pair: {pair_folder}")
                
                pair_success_count = 0
                pair_fail_count = 0
                pair_errors = []
                
                for conf_file in sorted(conf_files):
                    if conf_file in failed_conf_files:
                        total_skipped += 1
                        print(f"    ⊘ SKIP {conf_file} (previously failed - see snaphu_error.log)")
                        continue
                    
                    conf_path = os.path.join(subfolder_path, conf_file)
                    
                    phase_img_file = None
                    line_length = None
                    phase_img_file, _corr_img = self._parse_snaphu_conf_phase_corr(conf_path)
                    try:
                        with open(conf_path, 'r') as f:
                            for line in f:
                                if 'Phase_ifg_' in line and '.snaphu.img' in line:
                                    parts = line.strip().split()
                                    for i, part in enumerate(parts):
                                        if part.startswith('Phase_ifg_') and part.endswith('.snaphu.img'):
                                            if i + 1 < len(parts):
                                                try:
                                                    line_length = parts[i + 1]
                                                except Exception:
                                                    pass
                                            break
                                    if phase_img_file:
                                        break
                    except Exception as e:
                        print(f"    ✗ Failed to read {conf_file}: {e}")
                        continue
                    
                    if not phase_img_file:
                        print(f"    ⚠ Could not extract Phase_ifg filename from {conf_file}")
                        continue

                    ok_dims, dim_msg = self._validate_snaphu_conf_phase_coh_dims(
                        conf_path, subfolder_path)
                    if not ok_dims:
                        self._print_block_always(
                            f"\n{'='*80}\n"
                            f"STEP 21 STOPPED — Phase/CORRFILE dimension mismatch\n"
                            f"{'='*80}\n"
                            f"{dim_msg.rstrip()}\n"
                        )
                        return False
                    
                    phase_img_path = os.path.join(subfolder_path, phase_img_file)
                    if not os.path.exists(phase_img_path):
                        print(f"    ⚠ Phase image not found: {phase_img_file}")
                        continue
                    
                    unwphase_img_file = phase_img_file.replace('Phase_ifg_', 'UnwPhase_ifg_')
                    unwphase_img_path = os.path.join(subfolder_path, unwphase_img_file)
                    
                    if os.path.exists(unwphase_img_path):
                        total_skipped += 1
                        print(f"    ✓ SKIP {conf_file} (already unwrapped)")
                        continue
                    
                    print(f"    → UNWRAP {conf_file}")
                    print(f"      Phase: {phase_img_file}")
                    
                    cmd = [
                        snaphu_executable,
                        '-f', conf_file,
                        phase_img_file
                    ]
                    
                    if line_length:
                        cmd.append(line_length)
                    
                    cmd_str = ' '.join(cmd)
                    print(f"      {cmd_str}")
                    
                    try:
                        original_cwd = os.getcwd()
                        os.chdir(subfolder_path)
                        
                        result = subprocess.run(
                            cmd,
                            capture_output=True,
                            text=True,
                            timeout=1800
                        )
                        
                        os.chdir(original_cwd)
                        
                        if os.path.exists(unwphase_img_path):
                            total_unwrapped += 1
                            pair_success_count += 1
                            print(f"      ✓ SUCCESS: {unwphase_img_file}")
                        else:
                            total_failed += 1
                            pair_fail_count += 1
                            error_msg = f"UnwPhase file not created"
                            print(f"      ✗ FAILED: {error_msg}")
                            if result.stderr:
                                stderr_preview = result.stderr[:200]
                                print(f"      Error: {stderr_preview}")
                                error_msg += f" - {stderr_preview}"
                            self.log_snaphu_failure(conf_file, error_msg)
                            failed_conf_list.append(conf_file)
                            pair_errors.append(error_msg)
                    
                    except subprocess.TimeoutExpired:
                        os.chdir(original_cwd)
                        total_failed += 1
                        pair_fail_count += 1
                        error_msg = "Unwrapping took >30 minutes"
                        print(f"      ✗ TIMEOUT: {error_msg}")
                        self.log_snaphu_failure(conf_file, error_msg)
                        failed_conf_list.append(conf_file)
                        pair_errors.append(error_msg)
                    
                    except Exception as e:
                        os.chdir(original_cwd)
                        total_failed += 1
                        pair_fail_count += 1
                        error_msg = str(e)
                        print(f"      ✗ ERROR: {error_msg}")
                        self.log_snaphu_failure(conf_file, error_msg)
                        failed_conf_list.append(conf_file)
                        pair_errors.append(error_msg)
                
                if pair_fail_count > 0 and pair_success_count == 0:
                    print(f"    ⚠️  All {pair_fail_count} .conf file(s) failed for pair {pair_folder}")
                elif pair_fail_count > 0:
                    print(f"    ⚠ {pair_fail_count} .conf file(s) failed, but {pair_success_count} succeeded")
                elif pair_success_count > 0:
                    print(f"    ✓ At least {pair_success_count} .conf file(s) succeeded - pair NOT logged to snaphu_error.log")
            
            if lay['use_merged']:
                break
        
        print(f"\n{'='*80}")
        print(f"STEP 21 UNWRAPPING SUMMARY")
        print(f"{'='*80}")
        print(f"  ✓ Successful unwraps: {total_unwrapped} .conf files")
        print(f"  ⊙ Already unwrapped (skipped): {total_skipped} .conf files")
        if total_failed > 0:
            print(f"  ✗ Failed unwraps: {total_failed} .conf files")
        if failed_conf_list:
            print(f"\n  ⚠️  Failed .conf files logged to snaphu_error.log:")
            for conf_name in failed_conf_list[:10]:
                print(f"    - {conf_name}")
            if len(failed_conf_list) > 10:
                print(f"    ... and {len(failed_conf_list) - 10} more")
        print(f"{'='*80}\n")
        
        if total_unwrapped == 0:
            if total_skipped > 0:
                return True
            return False
        return total_failed == 0 or total_unwrapped > 0
    
    def step17_filter_snaphu_and_preview(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 20: Filter SNAPHU & Phase Preview - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 20: Filter SNAPHU Configuration Files & Phase Preview")
        print(f"{'='*80}")
        
        snaphu_bin = self.config.get('snaphu_bin', '')
        
        if not snaphu_bin:
            print(f"\n{'='*80}")
            print(f"ERROR: SNAPHU binary path not configured")
            print(f"{'='*80}")
            print(f"  Please add 'snaphu_bin' parameter to your configuration file")
            print(f"  Example: snaphu_bin=/home/user/.snap/auxdata/snaphu-v2.0.4_linux/")
            print(f"\n  ✗ Step 20 terminated\n")
            return False
        
        snaphu_executable = os.path.join(snaphu_bin, 'bin', 'snaphu')
        
        if not os.path.exists(snaphu_executable):
            print(f"\n{'='*80}")
            print(f"ERROR: SNAPHU binary not found")
            print(f"{'='*80}")
            print(f"  Configured path: {snaphu_bin}")
            print(f"  Expected executable: {snaphu_executable}")
            print(f"  File exists: {os.path.exists(snaphu_executable)}")
            print(f"\n  Please configure correct SNAPHU path in your config file")
            print(f"  Example: snaphu_bin=/home/user/.snap/auxdata/snaphu-v2.0.4_linux/")
            print(f"\n  ✗ Step 20 terminated\n")
            return False
        
        try:
            result = subprocess.run([snaphu_executable], 
                                   capture_output=True, 
                                   text=True, 
                                   timeout=5)
            if 'snaphu v' in result.stdout or 'snaphu v' in result.stderr:
                version_line = (result.stdout + result.stderr).split('\n')[0]
                print(f"\n  ✓ SNAPHU validation successful: {version_line.strip()}")
                print(f"  Path: {snaphu_executable}\n")
            else:
                print(f"\n{'='*80}")
                print(f"ERROR: SNAPHU binary exists but may not be executable")
                print(f"{'='*80}")
                print(f"  Path: {snaphu_executable}")
                print(f"  Try running: chmod +x {snaphu_executable}")
                print(f"\n  ✗ Step 20 terminated\n")
                return False
        except subprocess.TimeoutExpired:
            print(f"\n  ✗ SNAPHU validation timed out")
            return False
        except Exception as e:
            print(f"\n{'='*80}")
            print(f"ERROR: Failed to execute SNAPHU binary")
            print(f"{'='*80}")
            print(f"  Path: {snaphu_executable}")
            print(f"  Error: {e}")
            print(f"\n  ✗ Step 20 terminated\n")
            return False
        
        success = self.preprocess_snaphu_conf_files()
        
        if not success:
            print(f"\n  ✗ Step 20 (conf filter) failed\n")
            return False
        
        self.postprocess_corrfile_coherence_nan2zero()
        
        self.export_phase_png_previews()
        
        return True
    
    
    def step18_snaphu_unwrap(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 21: Execute Unwrapping - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 21: Execute SNAPHU Unwrapping")
        print(f"{'='*80}")
        
        snaphu_bin = self.config.get('snaphu_bin', '')
        
        if not snaphu_bin:
            print(f"\n{'='*80}")
            print(f"ERROR: SNAPHU binary path not configured")
            print(f"{'='*80}")
            print(f"  Please add 'snaphu_bin' parameter to your configuration file")
            print(f"  Example: snaphu_bin=/home/user/.snap/auxdata/snaphu-v2.0.4_linux/")
            print(f"\n  ✗ Step 21 terminated\n")
            return False
        
        snaphu_executable = os.path.join(snaphu_bin, 'bin', 'snaphu')
        
        if not os.path.exists(snaphu_executable):
            print(f"\n{'='*80}")
            print(f"ERROR: SNAPHU binary not found")
            print(f"{'='*80}")
            print(f"  Configured path: {snaphu_bin}")
            print(f"  Expected executable: {snaphu_executable}")
            print(f"  File exists: {os.path.exists(snaphu_executable)}")
            print(f"\n  Please configure correct SNAPHU path in your config file")
            print(f"  Example: snaphu_bin=/home/user/.snap/auxdata/snaphu-v2.0.4_linux/")
            print(f"\n  ✗ Step 21 terminated\n")
            return False
        
        try:
            result = subprocess.run([snaphu_executable], 
                                   capture_output=True, 
                                   text=True, 
                                   timeout=5)
            if 'snaphu v' in result.stdout or 'snaphu v' in result.stderr:
                version_line = (result.stdout + result.stderr).split('\n')[0]
                print(f"\n  ✓ SNAPHU validation successful: {version_line.strip()}")
                print(f"  Path: {snaphu_executable}\n")
            else:
                print(f"\n{'='*80}")
                print(f"ERROR: SNAPHU binary exists but may not be executable")
                print(f"{'='*80}")
                print(f"  Path: {snaphu_executable}")
                print(f"  Try running: chmod +x {snaphu_executable}")
                print(f"\n  ✗ Step 21 terminated\n")
                return False
        except Exception as e:
            print(f"\n{'='*80}")
            print(f"ERROR: Failed to validate SNAPHU binary")
            print(f"{'='*80}")
            print(f"  Path: {snaphu_executable}")
            print(f"  Error: {e}")
            print(f"\n  ✗ Step 21 terminated\n")
            return False
        
        snaphu_success = self.execute_snaphu_unwrapping(snaphu_executable)
        
        if not snaphu_success:
            print(f"\n{'='*80}")
            print("STEP 21 STOPPED — no successful SNAPHU unwraps")
            print(f"{'='*80}")
            print("  Review snaphu_error.log and fix SNAPHU configuration before continuing.")
            print("  Later steps (import, terrain correction, export) will not run.")
            print(f"{'='*80}\n")
            return False
        
        return True
    
    
    def step19_import_unwrapped(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 22: Import Unwrapped - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 22: Import Unwrapped Interferograms")
        print(f"{'='*80}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        bad_pairs = self.get_bad_pairs()
        
        if bad_pairs:
            print(f"\n{'─'*80}")
            print(f"⚠️  Found {len(bad_pairs)} bad pairs in bad_pair.txt (will be excluded):")
            print(f"{'─'*80}")
            for pair_name in sorted(bad_pairs):
                print(f"  ⊗ {pair_name}")
            print(f"{'─'*80}\n")
        
        failed_pairs = self.get_snaphu_failed_pairs()
        
        if failed_pairs:
            print(f"\n{'─'*80}")
            print(f"⚠️  Found {len(failed_pairs)} previously failed SNAPHU pairs (will also be excluded):")
            print(f"{'─'*80}")
            for pair_name in sorted(failed_pairs):
                print(f"  ✗ {pair_name}")
            print(f"{'─'*80}\n")
        
        excluded_pairs = bad_pairs | failed_pairs
        
        total_success = 0
        total_failed = 0
        
        lay = self._post_goldstein_ml_flt_layout()
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            multi_intf_deb_ml_flt_dir, intf_stack_suffix_dim = self.snaphu_source_base_dir_and_suffix(swath_name, lay)
            
            print(
                "  Step 22: `-Pstack` references the same product tree as SnaphuExport Step 19 "
                f"(here: `{os.path.basename(multi_intf_deb_ml_flt_dir)}/`; suffix `{intf_stack_suffix_dim}`)"
            )
            print(
                "    SNAPHU subfolders use the basename of Step 19 input under each pair directory."
            )
            if lay['use_merged']:
                multi_snaphu_dir = os.path.join(self.proj_root, lay['merge_folder_name'], f'multi_snaphu_{self.master_date}')
                output_base_dir = os.path.join(self.proj_root, lay['merge_folder_name'], f'multi_unw_{self.master_date}')
            else:
                multi_snaphu_dir = os.path.join(self.proj_root, swath_name, f'multi_snaphu_{self.master_date}')
                output_base_dir = os.path.join(self.proj_root, swath_name, f'multi_unw_{self.master_date}')
            
            if not os.path.exists(multi_intf_deb_ml_flt_dir):
                print(f"  ⚠ Input stack folder not found: {multi_intf_deb_ml_flt_dir}")
                print(f"    Run Step 19 (SnaphuExport prerequisite) pipeline first.")
                continue
            
            if not os.path.exists(multi_snaphu_dir):
                print(f"  ⚠ Multi-snaphu directory not found: {multi_snaphu_dir}")
                print(f"    Run Step 19 (SnaphuExport) before import.")
                continue
            
            print(f"  Scanning for parent pair folders in {multi_snaphu_dir}...")
            parent_to_children = {}
            
            for parent_folder in os.listdir(multi_snaphu_dir):
                parent_path = os.path.join(multi_snaphu_dir, parent_folder)
                if not os.path.isdir(parent_path):
                    continue

                subfolder_path, subfolder_name = self.resolve_snaphu_stack_subfolder(
                    parent_path, parent_folder, swath_name, lay)

                has_unw = False
                if subfolder_path:
                    has_unw = any(
                        fn.startswith('UnwPhase_ifg_') and fn.endswith('.snaphu.img')
                        for fn in os.listdir(subfolder_path)
                    )

                if parent_folder in excluded_pairs:
                    if has_unw:
                        print(
                            f"  Note: {parent_folder} listed in snaphu_error.log/bad_pair but "
                            f"UnwPhase files exist in {subfolder_name} — attempting import"
                        )
                    elif parent_folder in bad_pairs:
                        print(f"  Skipping parent folder {parent_folder} (bad pair - see bad_pair.txt)")
                        continue
                    else:
                        print(f"  Skipping parent folder {parent_folder} (previously failed SNAPHU - see snaphu_error.log)")
                        continue
                
                if not subfolder_path:
                    continue
                
                if not has_unw:
                    continue
                child_pairs = []
                try:
                    for filename in os.listdir(subfolder_path):
                        if filename.startswith('UnwPhase_ifg_') and filename.endswith('.snaphu.img'):
                            try:
                                date_part = filename.replace('UnwPhase_ifg_', '').replace('.snaphu.img', '')
                                parts = date_part.split('_')
                                if len(parts) >= 2:
                                    date1_fmt = parts[-2]
                                    date2_fmt = parts[-1]
                                    
                                    date1_yyyymmdd = self.convert_display_to_yyyymmdd(date1_fmt)
                                    date2_yyyymmdd = self.convert_display_to_yyyymmdd(date2_fmt)
                                    
                                    if date1_yyyymmdd and date2_yyyymmdd:
                                        if int(date1_yyyymmdd) <= int(date2_yyyymmdd):
                                            pair_name = f"{date1_yyyymmdd}_{date2_yyyymmdd}"
                                        else:
                                            pair_name = f"{date2_yyyymmdd}_{date1_yyyymmdd}"
                                        
                                        child_pairs.append(pair_name)
                            except Exception as e:
                                print(f"    ⚠ Could not parse date from {filename}: {e}")
                except Exception as e:
                    print(f"    ⚠ Error scanning {subfolder_path}: {e}")
                
                if child_pairs:
                    parent_to_children[parent_folder] = {
                        'children': sorted(list(set(child_pairs))),
                        'snaphu_path': subfolder_path,
                        'snaphu_name': subfolder_name,
                    }
            
            if not parent_to_children:
                print(f"  ⚠ No UnwPhase_*.img files found in {multi_snaphu_dir}")
                continue
            
            total_child_pairs = sum(len(v['children']) for v in parent_to_children.values())
            print(f"  Found {len(parent_to_children)} parent folder(s) with {total_child_pairs} child pair(s):")
            for parent in sorted(parent_to_children.keys()):
                children = parent_to_children[parent]['children']
                print(f"    Parent: {parent}  (SNAPHU: {parent_to_children[parent]['snaphu_name']})")
                for child in children:
                    print(f"      • {child}")
            
            commands_data = []
            
            for parent_folder in sorted(parent_to_children.keys()):
                entry = parent_to_children[parent_folder]
                child_pairs = entry['children']
                snaphu_processing_location = entry['snaphu_path']
                
                deburst_file, stack_suffix_used = self.resolve_snaphu_stack_dim_path(
                    multi_intf_deb_ml_flt_dir, parent_folder, swath_name, lay)
                if not deburst_file:
                    print(f"  ⚠ Skipping parent {parent_folder}: stack .dim not found under "
                          f"{os.path.basename(multi_intf_deb_ml_flt_dir)}/")
                    continue
                if stack_suffix_used != intf_stack_suffix_dim:
                    print(f"  Using -Pstack: {os.path.basename(deburst_file)}")
                
                if not os.path.isdir(snaphu_processing_location):
                    print(f"  ⚠ Skipping parent {parent_folder}: SNAPHU folder missing")
                    continue
                
                print(f"\nProcessing parent pair: {parent_folder}")
                
                for pair_name in child_pairs:
                    output_dir = os.path.join(output_base_dir, pair_name)
                    output_file = os.path.join(output_dir, f"{pair_name}{stack_suffix_used}")
                    
                    if os.path.exists(output_file):
                        if self.check_output_file_valid(output_file):
                            print(f"  Skipping {pair_name} (already exists in multi_unw_{self.master_date})")
                            continue
                        else:
                            print(f"  Re-processing {pair_name} (existing file is incomplete/invalid)")
                            try:
                                os.remove(output_file)
                                data_dir = output_file.replace('.dim', '.data')
                                if os.path.exists(data_dir):
                                    shutil.rmtree(data_dir)
                            except Exception as e:
                                print(f"    Warning: Could not remove incomplete files: {e}")
                    
                    date1_yyyymmdd, date2_yyyymmdd = pair_name.split('_')
                    date1_fmt = self.convert_date_format(date1_yyyymmdd)
                    date2_fmt = self.convert_date_format(date2_yyyymmdd)
                    
                    unwphase_img_file = None
                    try:
                        for filename in os.listdir(snaphu_processing_location):
                            if (filename.startswith('UnwPhase_ifg_') and 
                                filename.endswith('.snaphu.img') and
                                date1_fmt in filename and date2_fmt in filename):
                                unwphase_img_file = os.path.join(snaphu_processing_location, filename)
                                break
                    except Exception as e:
                        print(f"  ⚠ Error scanning for UnwPhase file: {e}")
                        continue
                    
                    if not unwphase_img_file:
                        print(f"  ⚠ UnwPhase .img file not found for {pair_name} in {snaphu_processing_location}")
                        continue
                    
                    if not os.path.exists(unwphase_img_file):
                        print(f"  ⚠ UnwPhase .img file does not exist: {unwphase_img_file}")
                        continue
                    
                    graph_xml = self.get_graph_file('snaphu_import.xml')
                    
                    cmd = [
                        *self.gpt_base_cmd(),
                        graph_xml,
                        f"-Pstack={deburst_file}",
                        f"-PunwImg={unwphase_img_file}",
                        f"-PoutFile={output_file}"
                    ]
                    
                    commands_data.append((cmd, f"SNAPHU Unwrap+Import {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} import operations...")
            
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 22 - Import Unwrapped ({swath_name})"
            )
            
            total_success += len(success_list)
            total_failed += len(failed_list)
            
            if failed_list:
                print(f"\n{'='*80}")
                print(f"WARNING: {len(failed_list)} pairs failed SNAPHU unwrapping in {swath_name}")
                print(f"{'='*80}")
                for pair_name in failed_list:
                    print(f"  Skip {pair_name} (Unwrap Error) - logged in snaphu_error.log")
                    self.log_snaphu_failure(pair_name, "SNAPHU unwrap or import failed")
                print(f"\nFailed pairs will be excluded from subsequent processing (Steps 23-24)")
                print(f"{'='*80}")
            
            print(f"\n  Continuing with remaining pairs...")
            
            if lay['use_merged']:
                break
        
        print(f"\n{'='*80}")
        print(f"STEP 22 SUMMARY")
        print(f"{'='*80}")
        if bad_pairs:
            print(f"  ⊗ Excluded (bad pairs): {len(bad_pairs)} pairs")
        if failed_pairs:
            print(f"  ⊘ Excluded (pre-existing failures): {len(failed_pairs)} pairs")
        print(f"  ✓ Successful: {total_success} pairs")
        if total_failed > 0:
            print(f"  ✗ Failed: {total_failed} pairs (logged to snaphu_error.log)")
        print(f"{'='*80}\n")
        
        return True
    
    
    def step20_terrain_correction(self):
        if self.insar_target == 2:
            print(f"\n{'='*80}")
            print(f"STEP 23: Terrain-Correction - SKIPPED")
            print(f"{'='*80}")
            print(f"  StaMPS modes (insar_target=2) skip Steps 18-24 and go directly to Steps 25–27 (StaMPS Export)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 23: Terrain-Correction")
        print(f"{'='*80}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        bad_pairs = self.get_bad_pairs()
        
        failed_pairs = self.get_snaphu_failed_pairs()
        
        excluded_pairs = bad_pairs | failed_pairs
        
        if excluded_pairs:
            print(f"\n⚠️  Excluding {len(excluded_pairs)} pairs from terrain correction:")
            if bad_pairs:
                print(f"    - {len(bad_pairs)} bad pairs (bad_pair.txt)")
            if failed_pairs:
                print(f"    - {len(failed_pairs)} failed SNAPHU pairs (snaphu_error.log)")
        
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        
        first_pair_processed = False
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            if use_merged:
                multi_unw_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_unw_{self.master_date}')
                output_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_tc_{self.master_date}')
                input_file_suffix = '_Stack_esd_mask_deb_mmifg_mrg_ml_flt.dim'
            else:
                multi_unw_dir = os.path.join(self.proj_root, swath_name, f'multi_unw_{self.master_date}')
                output_base_dir = os.path.join(self.proj_root, swath_name, f'multi_tc_{self.master_date}')
                input_file_suffix = '_Stack_esd_mask_deb_mmifg_ml_flt.dim'
            
            if not os.path.exists(multi_unw_dir):
                print(f"  ⚠ Multi-unw directory not found: {multi_unw_dir}")
                continue
            
            commands_data = []
            pair_idx = 0
            
            for pair in self.sbas_pairs:
                date1 = pair['date1']
                date2 = pair['date2']
                pair_name = f"{date1}_{date2}"
                
                
                if pair_name in excluded_pairs:
                    if pair_name in bad_pairs:
                        print(f"  Skipping {pair_name} (bad pair - see bad_pair.txt)")
                    else:
                        print(f"  Skipping {pair_name} (SNAPHU unwrap failed - see snaphu_error.log)")
                    continue
                
                input_file = os.path.join(multi_unw_dir, pair_name, f"{pair_name}{input_file_suffix}")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input not found")
                    continue
                
                unwphase_bands = []
                coh_bands = []
                intensity_bands = []
                try:
                    import xml.etree.ElementTree as ET
                    tree = ET.parse(input_file)
                    root = tree.getroot()
                    for band in root.findall('.//BAND_NAME'):
                        band_name = band.text
                        if band_name.startswith('UnwPhase_'):
                            unwphase_bands.append(band_name)
                        elif band_name.startswith('coh_'):
                            coh_bands.append(band_name)
                        elif band_name.startswith('Intensity_'):
                            intensity_bands.append(band_name)
                except Exception as e:
                    print(f"  ⚠ Failed to parse bands from {pair_name}: {e}")
                    continue
                
                if not unwphase_bands:
                    print(f"  ⚠ No UnwPhase bands found in {pair_name}")
                    continue
                
                print(f"  Found {len(unwphase_bands)} UnwPhase bands in {pair_name}")
                
                is_first_pair = (pair_idx == 0 and not first_pair_processed)
                
                band_idx = 0
                
                for unwphase_band in unwphase_bands:
                    band_dt1, band_dt2 = self.parse_date_from_band_name(unwphase_band)
                    
                    if not band_dt1 or not band_dt2:
                        print(f"    ⚠ Failed to parse dates from band: {unwphase_band}")
                        continue
                    
                    band_date1 = self.convert_datetime_to_yyyymmdd(band_dt1)
                    band_date2 = self.convert_datetime_to_yyyymmdd(band_dt2)
                    band_pair_name = f"{band_date1}_{band_date2}"
                    
                    print(f"    Processing band pair: {band_pair_name} (from {unwphase_band})")
                    
                    coh_band = self.get_coh_band_from_unwphase(unwphase_band)
                    
                    if coh_band not in coh_bands:
                        print(f"      ⚠ Coherence band not found: {coh_band}")
                        continue
                    
                    output_dir = os.path.join(output_base_dir, band_pair_name)
                    os.makedirs(output_dir, exist_ok=True)
                    output_file = os.path.join(output_dir, f"{band_pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_TC.dim")
                    
                    needs_reprocessing = False
                    if is_first_pair and band_idx == 0 and os.path.exists(output_file):
                        tc_data_dir = output_file.replace('.dim', '.data')
                        if os.path.isdir(tc_data_dir):
                            tc_files = [f for f in os.listdir(tc_data_dir) if f.endswith(('.img', '.hdr'))]
                            tc_basenames = sorted(set([os.path.splitext(f)[0] for f in tc_files]))
                            has_intensity = any(b.startswith('Intensity_') for b in tc_basenames)
                            if not has_intensity:
                                print(f"      ⟳ Reprocessing {band_pair_name} (adding intensity bands)")
                                needs_reprocessing = True
                    
                    if self.check_output_file_valid(output_file) and not needs_reprocessing:
                        print(f"      Skipping {band_pair_name}: Output already exists")
                        band_idx += 1
                        continue
                    
                    if is_first_pair and band_idx == 0 and intensity_bands:
                        source_bands_param = f"{unwphase_band},{coh_band},{intensity_bands[0]}"
                        print(f"      Including intensity band: {intensity_bands[0]}")
                    else:
                        source_bands_param = f"{unwphase_band},{coh_band}"
                    
                    cmd = [
                        *self.gpt_base_cmd(cache_size='16G'),
                        'Terrain-Correction',
                        f"-Ssource={input_file}",
                        f"-PsourceBands={source_bands_param}",
                        "-PdemName=SRTM 3Sec",
                        "-PpixelSpacingInDegree=0.00099999921",
                        "-PimgResamplingMethod=BILINEAR_INTERPOLATION",
                        "-PsaveLocalIncidenceAngle=true",
                        "-PsaveDEM=true",
                        "-PnodataValueAtSea=true",
                        '-t', output_file
                    ]
                    
                    commands_data.append((cmd, f"Terrain-Correction {band_pair_name}", band_pair_name, output_file))
                    
                    if is_first_pair and band_idx == 0:
                        first_pair_processed = True
                    
                    band_idx += 1
                
                pair_idx += 1
            
            if not commands_data:
                print(f"  All {swath_name} bands already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} Terrain-Correction operations...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 23 - Terrain-Correction ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} bands failed")
                return False
            
            if use_merged:
                break
        
        return True
    
    
    def parse_date_from_band_name(self, band_name):
        try:
            parts = band_name.split('_')
            date1_str = None
            date2_str = None
            for i, part in enumerate(parts):
                if len(part) >= 9 and part[0].isdigit() and part[2].isalpha():
                    if not date1_str:
                        date1_str = part
                    else:
                        date2_str = part
                        break
            
            if not date1_str or not date2_str:
                return None, None
            
            date1 = datetime.strptime(date1_str, '%d%b%Y')
            date2 = datetime.strptime(date2_str, '%d%b%Y')
            return date1, date2
        except:
            return None, None
    
