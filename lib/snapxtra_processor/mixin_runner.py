"""Batch runner and step dispatch"""
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


class RunnerMixin:

    def _log_batch_timing_summary(self, total_elapsed_sec, wall_start=None, wall_end=None):
        hours, minutes, secs = self._format_elapsed_hms(total_elapsed_sec)
        print(f"\n{'=' * 39}")
        print("All steps completed!")
        if wall_start is not None and wall_end is not None:
            print(
                f"Started: {wall_start.strftime('%H:%M:%S')}  "
                f"Finished: {wall_end.strftime('%H:%M:%S')}"
            )
        print(f"Total execution time: {hours}h {minutes}m {secs:.0f}s")
        print(f"{'=' * 39}")
    
    def run_all_steps(self):
        """Run all processing steps with optional start_step and end_step from config."""
        steps = [
            (0, "Parse Baselines & Network", self.step00_parse_baseline_metadata),
            (1, "AOI & Swath configuration", self.step01_aoi_and_swath_config),
            (2, "Prepare Slices", self.step02_prepare_slices_outer),
            (3, "Create Coregistered Stack", self.step02_create_coreg_stack),
            (4, "Land-Sea-Mask", self.step03_land_sea_mask),
            (5, "TOPSAR-Deburst", self.step04_topsar_deburst),
            (6, "Add Elevation (StaMPS)", self.step05_add_elevation),
            (7, "Create Multi-Interferograms", self.step06_create_multi_interferograms),
            (8, "Merge Swaths", self.step07_merge_swaths),
            (9, "Subset Interferograms", self.step08_subset_interferograms),
            (10, "Import-Vector AOI (optional)", self.step_import_vector_mask_shp),
            (11, "Land-Sea-Mask AOI (optional)", self.step_landsea_mask_vector_geometry),
            (12, "Bandmath Export (StaMPS)", self.step09_bandmath_export),
            (13, "Filter PS Pairs", self.step10_filter_ps_pairs),
            (14, "Multilook", self.step10_multilook),
            (15, "Merge Coreg (StaMPS)", self.step11_merge_coreg_stamps),
            (16, "Subset Coreg (StaMPS)", self.step12_subset_coreg_stamps),
            (17, "Import-Vector Coreg (StaMPS)", self.step25_import_vector_coreg_stamps),
            (18, "GoldsteinPhaseFiltering", self.step14_goldstein_filtering),
            (19, "SnaphuExport", self.step15_snaphu_export),
            (20, "Filter SNAPHU & Phase Preview", self.step17_filter_snaphu_and_preview),
            (21, "Execute Unwrapping", self.step18_snaphu_unwrap),
            (22, "Import Unwrapped", self.step19_import_unwrapped),
            (23, "Terrain-Correction", self.step20_terrain_correction),
            (24, "Export GeoTIFF", self.step21_export_geotiff),
            (25, "Land-Sea-Mask Coreg (StaMPS)", self.step26_landsea_mask_coreg_stamps),
            (26, "Multilook Coreg (StaMPS)", self.step13_multilook_coreg_stamps),
            (27, "StaMPS Export", self.step22_stamps_export),
        ]
        
        start_val = str(self.start_step).strip()
        end_val = str(self.end_step).strip()
        
        if start_val and start_val.isdigit():
            start = int(start_val)
        else:
            start = 0
        
        if end_val and end_val.isdigit():
            end = int(end_val)
        else:
            end = 27 if self.insar_target == 2 else 24
        
        if start < 0 or start > 27:
            print(f"ERROR: Invalid start_step={start}. Must be between 0 and 27.")
            return False
        if end < 0 or end > 27:
            print(f"ERROR: Invalid end_step={end}. Must be between 0 and 27.")
            return False
        if start > end:
            print(f"ERROR: start_step ({start}) cannot be greater than end_step ({end}).")
            return False
        
        filtered_steps = [(num, name, func) for num, name, func in steps if start <= num <= end]
        
        if self.savelog != 'release':
            print(f"\n{'='*80}")
            print(f"SBAS MULTI-INTERFEROGRAM PROCESSING")
            print(f"{'='*80}")
            print(f"Project root: {self.proj_root}")
            print(f"Swaths: {', '.join(self.swaths_to_process)}")
            print(f"Master date: {self.master_date}")
            if start_val or end_val:
                print(f"Step range: {start} to {end}")
            print(f"{'='*80}\n")
        
        batch_anchor = self._init_batch_wall_start(
            force_reset=getattr(self, '_reset_batch_wall_start', False))
        if self.savelog != 'release':
            print(
                f"\n[{batch_anchor.strftime('%H:%M:%S')}] Batch wall-clock anchor "
                f"(steps {start}–{end})"
            )
        
        for step_num, step_name, step_func in filtered_steps:
            if self.savelog != 'release':
                print(f"\n{'='*80}")
            self._log_step_start_timestamp(step_num, step_name)
            if self.savelog != 'release':
                print(f"{'='*80}")
            
            self._release_progress_reset_step()
            step_start = time.perf_counter()
            step_ok = step_func()
            self._release_progress_finish_step()
            step_elapsed = time.perf_counter() - step_start
            
            if not step_ok:
                self._log_step_timing_batch(
                    step_num, step_elapsed, self._batch_wall_elapsed_sec())
                print(f"\n✗ Step {step_num} failed!")
                return False
            
            self._log_step_timing_batch(
                step_num, step_elapsed, self._batch_wall_elapsed_sec())
            
            if step_num < end and start_val:
                next_step = step_num + 1
                print(f"\n  ✓ Step {step_num} completed. Updating start_step to {next_step}")
                self.update_config_file('start_step', str(next_step))
                self.start_step = str(next_step)
        
        wall_end = datetime.now()
        self._log_batch_timing_summary(
            self._batch_wall_elapsed_sec(), batch_anchor, wall_end)
        self._clear_batch_wall_start()
        
        print(f"\n{'='*80}")
        if start == 0 and end == 24 and self.insar_target != 2:
            print(f"✓ ALL STEPS COMPLETED SUCCESSFULLY")
        elif start == 0 and end == 27:
            print(f"✓ ALL STEPS COMPLETED SUCCESSFULLY")
        else:
            print(f"✓ STEPS {start}-{end} COMPLETED SUCCESSFULLY")
        print(f"{'='*80}\n")
        return True
    
    def run_specific_step(self, step_num):
        """Run a specific processing step"""
        steps = {
            0: ("Parse Baselines & Network", self.step00_parse_baseline_metadata),
            1: ("AOI & Swath configuration", self.step01_aoi_and_swath_config),
            2: ("Prepare Slices", self.step02_prepare_slices_outer),
            3: ("Create Coregistered Stack", self.step02_create_coreg_stack),
            4: ("Land-Sea-Mask", self.step03_land_sea_mask),
            5: ("TOPSAR-Deburst", self.step04_topsar_deburst),
            6: ("Add Elevation (StaMPS)", self.step05_add_elevation),
            7: ("Create Multi-Interferograms", self.step06_create_multi_interferograms),
            8: ("Merge Swaths", self.step07_merge_swaths),
            9: ("Subset Interferograms", self.step08_subset_interferograms),
            10: ("Import-Vector AOI (optional)", self.step_import_vector_mask_shp),
            11: ("Land-Sea-Mask AOI (optional)", self.step_landsea_mask_vector_geometry),
            12: ("Bandmath Export (StaMPS)", self.step09_bandmath_export),
            13: ("Filter PS Pairs", self.step10_filter_ps_pairs),
            14: ("Multilook", self.step10_multilook),
            15: ("Merge Coreg (StaMPS)", self.step11_merge_coreg_stamps),
            16: ("Subset Coreg (StaMPS)", self.step12_subset_coreg_stamps),
            17: ("Import-Vector Coreg (StaMPS)", self.step25_import_vector_coreg_stamps),
            18: ("GoldsteinPhaseFiltering", self.step14_goldstein_filtering),
            19: ("SnaphuExport", self.step15_snaphu_export),
            20: ("Filter SNAPHU & Phase Preview", self.step17_filter_snaphu_and_preview),
            21: ("Execute Unwrapping", self.step18_snaphu_unwrap),
            22: ("Import Unwrapped", self.step19_import_unwrapped),
            23: ("Terrain-Correction", self.step20_terrain_correction),
            24: ("Export GeoTIFF", self.step21_export_geotiff),
            25: ("Land-Sea-Mask Coreg (StaMPS)", self.step26_landsea_mask_coreg_stamps),
            26: ("Multilook Coreg (StaMPS)", self.step13_multilook_coreg_stamps),
            27: ("StaMPS Export", self.step22_stamps_export),
        }
        
        if step_num not in steps:
            print(f"ERROR: Invalid step number: {step_num}")
            print(f"Valid steps: 0-27")
            return False
        
        step_name, step_func = steps[step_num]
        
        if self.savelog != 'release':
            print(f"\n{'='*80}")
        self._log_step_start_timestamp(step_num, step_name)
        if self.savelog != 'release':
            print(f"{'='*80}")
        
        self._release_progress_reset_step()
        step_start = time.perf_counter()
        step_ok = step_func()
        self._release_progress_finish_step()
        self._log_step_timing_single(step_num, time.perf_counter() - step_start)
        return step_ok


