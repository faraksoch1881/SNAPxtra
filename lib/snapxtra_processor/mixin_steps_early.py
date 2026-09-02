"""Steps 0-8 and step 10 entry"""
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


class StepsEarlyMixin:

    def step02_prepare_slices_remaining(self, swath_name):
        """Process remaining files after first file burst check (for multi-swath)."""
        swath_root = os.path.join(self.proj_root, swath_name)
        data_root = os.path.join(swath_root, 'data')
        
        date_groups = self.get_zip_date_groups()
        if self.selected_dates:
            date_groups = {d: files for d, files in date_groups.items() if d in self.selected_dates}
        
        sorted_dates = sorted(date_groups.keys())
        if len(sorted_dates) <= 1:
            print(f"  No remaining files to process for {swath_name}")
            return True
        
        remaining_dates = {d: date_groups[d] for d in sorted_dates[1:]}
        
        for date, files in remaining_dates.items():
            date_dir = os.path.join(data_root, str(date))
            os.makedirs(date_dir, exist_ok=True)
            output_dim = os.path.join(date_dir, f"{date}_Slice.dim")

            if os.path.exists(output_dim):
                print(f"  Skipping {date} (already exists)")
                continue

            l_burst_config = str(self.config.get('l_burst', 1))
            is_multiframe_config = ',' in l_burst_config
            is_singleframe_data = len(files) == 1
            
            if is_multiframe_config and is_singleframe_data:
                print(f"\n{'='*80}")
                print(f"CONFIGURATION MISMATCH DETECTED")
                print(f"{'='*80}")
                print(f"  ✗ Config has multi-frame settings:")
                print(f"    swath={self.config['swath']}")
                print(f"    l_burst={l_burst_config}")
                print(f"    u_burst={self.config.get('u_burst')}")
                print(f"\n  ✗ But data for date {date} is single-frame (1 ZIP file)")
                print(f"\n  ACTION REQUIRED:")
                print(f"  1. Set burst_check=True in config file")
                print(f"  2. Re-run Steps 1–2 (AOI config, then Prepare Slices) to auto-detect swath and bursts")
                print(f"\n  Expected outcomes after re-run:")
                print(f"  - For single swath overlap: swath=1/2/3, l_burst=X, u_burst=Y")
                print(f"  - For multi-swath overlap: swath=12/23, l_burst=X, u_burst=Y (unified range)")
                print(f"{'='*80}\n")
                return False


            if len(files) > 1:
                swaths_list, l_bursts_list, u_bursts_list = self.parse_multiframe_config()
                
                if swaths_list is None:
                    raise ValueError(
                        f"ERROR: Multi-frame acquisition but config not defined.\n"
                        f"This should not happen in remaining files processing."
                    )
                
                frame_params = self.assign_frames_by_time(files, swaths_list, l_bursts_list, u_bursts_list)
                
                graph_file = self.get_graph_file('slice_assembly.xml')
                polarization = self.config.get('polarization', 'VV')
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_file,
                    f"-Pfile1={frame_params['file1']}",
                    f"-Pfile2={frame_params['file2']}",
                    f"-Pswath_type1={frame_params['swath_type1']}",
                    f"-Pl_burst1={frame_params['l_burst1']}",
                    f"-Pu_burst1={frame_params['u_burst1']}",
                    f"-Pswath_type2={frame_params['swath_type2']}",
                    f"-Pl_burst2={frame_params['l_burst2']}",
                    f"-Pu_burst2={frame_params['u_burst2']}",
                    f"-Ppolarization={polarization}",
                    f"-Poutput_dim={output_dim}"
                ]
                desc = f"SliceAssemblyMultiFrame {date}"
            else:
                graph_file = self.get_graph_file('single_slice.xml')
                polarization = self.config.get('polarization', 'VV')
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_file,
                    f"-Pzip_file={files[0]}",
                    f"-Ppolarization={polarization}",
                    f"-Pswath_type={swath_name}",
                    f"-Pl_burst={self.config.get('l_burst', 1)}",
                    f"-Pu_burst={self.config['u_burst']}",
                    f"-Poutput_dim={output_dim}"
                ]
                desc = f"SingleSlice {date}"

            success = self.run_command(cmd, desc)
            if not success or not os.path.exists(output_dim):
                print(f"  ✗ Failed: {date}")
                return False

            print(f"  ✓ Complete: {date}")

        return True


    def step02_create_coreg_stack(self):
        print(f"\n{'='*80}")
        print(f"STEP 3: Create Coregistered Stack")
        print(f"{'='*80}")
        
        if not self.validate_metadata_info():
            return False
        
        def _burst_cfg_str(val, default='1'):
            if val is None or val == '':
                return str(default).strip()
            return str(val).strip()

        swath_raw = _burst_cfg_str(self.config.get('swath', ''), '')
        l_burst_raw = _burst_cfg_str(
            self.config.get('l_burst', self.config.get('lburst', 1)), '1')
        u_burst_raw = _burst_cfg_str(
            self.config.get('u_burst', self.config.get('uburst', 3)), '3')

        is_multiframe = (
            ',' in swath_raw
            or ',' in l_burst_raw
            or ',' in u_burst_raw
        )

        if is_multiframe:
            graph_filename = '02_backgeo_esd_mF.xml'
            processing_mode = "Multi-Frame BackGeo ESD"
            print(f"  Burst configuration: Multi-frame (swath={swath_raw}, l_burst={l_burst_raw}, u_burst={u_burst_raw})")
        else:
            try:
                lb = int(l_burst_raw.strip())
                ub = int(u_burst_raw.strip())
            except (ValueError, TypeError):
                lb = 1
                ub = 3
            if lb == ub:
                graph_filename = '02_backgeo_single_burst.xml'
                processing_mode = "Single Burst"
                print(f"  Burst configuration: Single burst (burst {lb})")
            else:
                graph_filename = '02_backgeo_esd.xml'
                processing_mode = "Multi-Burst ESD"
                print(f"  Burst configuration: Multi-burst ({lb} to {ub})")
        
        graph_xml = self.get_graph_file(graph_filename)
        
        if not os.path.exists(graph_xml):
            print(f"  ✗ Graph file not found: {graph_xml}")
            return False
        
        print(f"  Using graph: {graph_filename}")
        
        dem_model_backgeo = self.get_backgeo_dem_model()
        print(f"  DEM model (-Pdem_name_model): {dem_model_backgeo}")
        
        if not self.load_sbas_pairs():
            return False
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            commands_data = []
            
            for pair in self.sbas_pairs:
                date1 = pair['date1']
                date2 = pair['date2']
                pair_name = f"{date1}_{date2}"
                
                if self._skip_pair_step03_rule(date1, date2):
                    mode_lbl = (
                        'LiCSBAS'
                        if self.insar_target == 1
                        else ('StaMPS PS' if self.insar_method == 2 else 'StaMPS SBAS')
                    )
                    print(f"  Skipping {pair_name}: excluded by master-date rule ({mode_lbl})")
                    continue
                
                master_dim = self.find_slice_dim(swath_name, self.master_date)
                date1_dim = self.find_slice_dim(swath_name, date1)
                date2_dim = self.find_slice_dim(swath_name, date2)
                
                if not master_dim:
                    print(f"  ⚠ Skipping {pair_name}: Master .dim not found")
                    continue
                
                if not date1_dim or not date2_dim:
                    missing = []
                    if not date1_dim:
                        missing.append('Date1')
                    if not date2_dim:
                        missing.append('Date2')
                    print(f"  ⚠ Skipping {pair_name}: {' and '.join(missing)} .dim not found")
                    continue
                file_list = f"{master_dim},{date1_dim},{date2_dim}"
                
                output_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_{self.master_date}', pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd.dim")
                
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_xml,
                    f"-PFILE_LIST_FROM_STEP01={file_list}",
                    f"-Pdem_name_model={dem_model_backgeo}",
                    f"-POUTPUT_STACK_FILE={output_file}"
                ]
                
                commands_data.append((cmd, f"{processing_mode} {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} coreg stacks...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 3 - {processing_mode} ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        return True
    
    
    def step03_land_sea_mask(self):
        """Step 3: Apply Land-Sea-Mask to coregistered stacks"""
        print(f"\n{'='*80}")
        print(f"STEP 4: Land-Sea-Mask")
        print(f"{'='*80}")
        
        if not self.validate_metadata_info():
            return False
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            coreg_stack_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_{self.master_date}')
            
            if not os.path.exists(coreg_stack_dir):
                print(f"  ⚠ Coreg stack directory not found: {coreg_stack_dir}")
                continue
            
            commands_data = []
            
            for pair in self.sbas_pairs:
                date1 = pair['date1']
                date2 = pair['date2']
                pair_name = f"{date1}_{date2}"
                
                if self._skip_pair_step03_rule(date1, date2):
                    print(f"  Skipping {pair_name}: excluded by master-date rule")
                    continue
                
                input_file = os.path.join(coreg_stack_dir, pair_name, f"{pair_name}_Stack_esd.dim")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input not found")
                    continue
                
                output_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_{self.master_date}', pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask.dim")
                
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'Land-Sea-Mask',
                    f"-Ssource={input_file}",
                    "-PlandMask=false",
                    '-t', output_file
                ]
                
                commands_data.append((cmd, f"Land-Sea-Mask {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} land-sea masks...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 4 - Land-Sea-Mask ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        return True
    
    
    def step04_topsar_deburst(self):
        """Step 4: TOPSAR-Deburst"""
        print(f"\n{'='*80}")
        print(f"STEP 5: TOPSAR-Deburst")
        print(f"{'='*80}")
        
        if not self.validate_metadata_info():
            return False
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            coreg_stack_mask_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_{self.master_date}')
            
            if not os.path.exists(coreg_stack_mask_dir):
                print(f"  ⚠ Coreg stack mask directory not found: {coreg_stack_mask_dir}")
                continue
            
            commands_data = []
            
            for pair in self.sbas_pairs:
                date1 = pair['date1']
                date2 = pair['date2']
                pair_name = f"{date1}_{date2}"
                
                if self._skip_pair_step03_rule(date1, date2):
                    print(f"  Skipping {pair_name}: excluded by master-date rule")
                    continue
                
                input_file = os.path.join(coreg_stack_mask_dir, pair_name, f"{pair_name}_Stack_esd_mask.dim")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input not found")
                    continue
                
                output_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_deb_{self.master_date}', pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb.dim")
                
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'TOPSAR-Deburst',
                    f"-Ssource={input_file}",
                    '-t', output_file
                ]
                
                commands_data.append((cmd, f"Deburst {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} deburst operations...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 5 - TOPSAR-Deburst ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        return True
    
    
    def step05_add_elevation(self):
        print(f"\n{'='*80}")
        print(f"STEP 6: Add Elevation")
        print(f"{'='*80}")
        
        dem_name = self.config.get('demName', 'Copernicus 30m Global DEM').strip()
        external_dem = self.config.get('externalDEMFile', '').strip()
        
        if external_dem:
            print(f"  Using external DEM: {external_dem}")
            if not os.path.exists(external_dem):
                print(f"  ✗ External DEM file not found: {external_dem}")
                return False
        else:
            print(f"  Using DEM: {dem_name}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            coreg_deb_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_deb_{self.master_date}')
            input_suffix = ""
            print(f"  Using deburst input from Step 03 (always)")
            
            if not os.path.exists(coreg_deb_dir):
                print(f"  ✗ Input directory not found: {coreg_deb_dir}")
                print(f"  Run Step 3 first")
                return False
            
            commands_data = []
            
            for pair in self.sbas_pairs:
                date1 = pair['date1']
                date2 = pair['date2']
                pair_name = f"{date1}_{date2}"
                
                if self._skip_pair_step03_rule(date1, date2):
                    print(f"  Skipping {pair_name}: excluded by master-date rule")
                    continue
                
                source_file = os.path.join(coreg_deb_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb{input_suffix}.dim")
                
                if not os.path.exists(source_file):
                    print(f"  ⚠ Skipping {pair_name}: Source not found")
                    continue
                
                output_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_deb_hgt_{self.master_date}', pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_hgt.dim")
                
                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    continue
                
                cmd = [
                    *self.gpt_base_cmd(),
                    'AddElevation',
                    f"-Ssource={source_file}"
                ]
                
                if external_dem:
                    cmd.append(f"-PdemName=External DEM")
                    cmd.append(f"-PexternalDEMFile={external_dem}")
                else:
                    cmd.append(f"-PdemName={dem_name}")
                
                cmd.extend(['-t', output_file])
                
                commands_data.append((cmd, f"AddElevation {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} pairs with AddElevation...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 6 - AddElevation ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        return True
    
    
    def step06_create_multi_interferograms(self):
        print(f"\n{'='*80}")
        print(f"STEP 7: Create Multi-Interferograms")
        print(f"{'='*80}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        list_step05 = {}
        
        for pair in self.master_second_pairs:
            pair_name = f"{pair['date1']}_{pair['date2']}"
            list_step05[pair_name] = {
                'date1': pair['date1'],
                'date2': pair['date2'],
                'used': False
            }
        
        if list_step05:
            print(f"  Found {len(list_step05)} pairs with master as date2 (for bridging):")
            for pair_name in sorted(list_step05.keys()):
                info = list_step05[pair_name]
                print(f"    • {pair_name} ({info['date1']} - master: {info['date2']})")
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            self._reset_multi_intf_bridging_state(list_step05)
            
            coreg_stack_hgt_dir = os.path.join(self.proj_root, swath_name, f'coreg_stack_mask_deb_hgt_{self.master_date}')
            
            if not os.path.exists(coreg_stack_hgt_dir):
                print(f"  ⚠ Coreg stack with elevation directory not found: {coreg_stack_hgt_dir}")
                print(f"  Run Step 4 (AddElevation) first")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(coreg_stack_hgt_dir)
            
            if not found_pairs:
                print(f"  ⚠ No pairs found in Step 4 output: {coreg_stack_hgt_dir}")
                continue
            
            print(f"\n  Found {len(found_pairs)} pair(s) in Step 4 output")
            
            commands_data = []
            
            for pair_name in found_pairs:
                parts = pair_name.split('_')
                if len(parts) != 2:
                    continue
                
                date1, date2 = parts[0], parts[1]
                
                if self._skip_pair_step03_rule(date1, date2):
                    print(f"  Skipping {pair_name}: excluded by master-date rule")
                    continue
                
                input_file = os.path.join(coreg_stack_hgt_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb_hgt.dim")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input file not found")
                    continue
                
                output_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_{self.master_date}', pair_name)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg.dim")
                
                base_pairs = self.generate_interferogram_pairs(date1, date2, self.master_date)
                base_pairs_set = set(base_pairs.split(','))

                if self.check_output_file_valid(output_file):
                    print(f"  Skipping {pair_name} (already exists)")
                    self._consume_bridging_pairs_for_dates(
                        list_step05, date1, date2, base_pairs_set)
                    continue
                
                additional_pairs = []
                for bridging_pair_name, bridging_info in list_step05.items():
                    if bridging_info['used']:
                        continue
                    
                    bridging_date1 = bridging_info['date1']
                    bridging_date2 = bridging_info['date2']
                    
                    if date1 == bridging_date1 or date2 == bridging_date1:
                        bridging_date1_fmt = self.convert_date_format(bridging_date1)
                        bridging_date2_fmt = self.convert_date_format(bridging_date2)
                        
                        bridging_pair_str = f"{bridging_date1_fmt}-{bridging_date2_fmt}"
                        
                        if bridging_pair_str not in base_pairs_set:
                            additional_pairs.append(bridging_pair_str)
                            base_pairs_set.add(bridging_pair_str)
                            print(f"    Adding bridging pair: {bridging_pair_str} (from {bridging_pair_name})")
                        else:
                            print(f"    Skipping bridging pair: {bridging_pair_str} (already in base pairs)")
                        
                        bridging_info['used'] = True
                
                if additional_pairs:
                    ifg_pairs = base_pairs + ',' + ','.join(additional_pairs)
                else:
                    ifg_pairs = base_pairs
                
                graph_xml = self.get_graph_file('03_multi_intf.xml')
                
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_xml,
                    f"-PINPUT_STACK_FILE={input_file}",
                    f"-PINTERFEROGRAM_PAIRS={ifg_pairs}",
                    f"-POUTPUT_MMIFG_FILE={output_file}"
                ]
                
                commands_data.append((cmd, f"Multi-Interferogram {pair_name}", pair_name, output_file))
            
            if not commands_data:
                print(f"  All {swath_name} pairs already processed")
                continue
            
            print(f"\n  Processing {len(commands_data)} multi-interferogram(s)...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 7 - Multi-Interferogram ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        return True
    
    
    def step07_merge_swaths(self):
        """Step 7: Merge multiple swaths (only for swath=0, 12, or 23)"""
        swath_config = self.get_swath_config_value()
        
        if swath_config in [1, 2, 3]:
            print(f"\n{'='*80}")
            print(f"STEP 8: Merge Swaths - SKIPPED")
            print(f"{'='*80}")
            print(f"  Single swath selected (swath={swath_config}), merging not necessary\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 8: Merge Swaths")
        print(f"{'='*80}")
        
        if swath_config == 0:
            swaths = ['IW1', 'IW2', 'IW3']
        elif swath_config == 12:
            swaths = ['IW1', 'IW2']
        elif swath_config == 23:
            swaths = ['IW2', 'IW3']
        else:
            print(f"  ✗ Invalid swath config: {swath_config}")
            return False
        
        print(f"  Merging swaths: {', '.join(swaths)}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                return False
        
        merge_folder_name = self.get_merge_folder_name(swath_config)
        if not merge_folder_name:
            print(f"  ✗ Invalid swath config: {swath_config}")
            return False
        
        merge_dir = os.path.join(self.proj_root, merge_folder_name)
        merge_output_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_{self.master_date}')
        os.makedirs(merge_output_dir, exist_ok=True)
        
        print(f"  Output directory: {merge_folder_name}/multi_intf_deb_mrg_{self.master_date}/")
        
        all_swath_pairs = []
        for swath in swaths:
            deb_dir = os.path.join(self.proj_root, swath, f'multi_intf_deb_{self.master_date}')
            swath_pairs = []
            if os.path.exists(deb_dir):
                for pair in self.sbas_pairs:
                    date1 = pair['date1']
                    date2 = pair['date2']
                    pair_name = f"{date1}_{date2}"
                    
                    
                    pair_dir = os.path.join(deb_dir, pair_name)
                    if os.path.exists(pair_dir):
                        swath_pairs.append(pair_name)
            all_swath_pairs.append(set(swath_pairs))
        
        if all_swath_pairs:
            common_pairs = set.intersection(*all_swath_pairs)
        else:
            common_pairs = set()
        
        if not common_pairs:
            print(f"  ✗ No common pairs found across all swaths")
            for i, swath in enumerate(swaths):
                print(f"    {swath}: {len(all_swath_pairs[i])} pairs")
            return False
        
        pairs = sorted(common_pairs)
        print(f"  Found {len(pairs)} common pairs across all swaths")
        
        merge_graph = self.get_graph_file('05_merge_swath.xml')
        if not os.path.exists(merge_graph):
            print(f"  ✗ Merge graph not found: {merge_graph}")
            return False
        
        commands_data = []
        
        for pair_name in pairs:
            deb_file_list = []
            missing_files = False
            for swath in swaths:
                deb_file = os.path.join(
                    self.proj_root,
                    swath,
                    f'multi_intf_deb_{self.master_date}',
                    pair_name,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg.dim"
                )
                if not os.path.exists(deb_file):
                    print(f"  ✗ Missing: {deb_file}")
                    missing_files = True
                    break
                deb_file_list.append(deb_file)
            
            if missing_files:
                continue
            
            output_dir = os.path.join(merge_output_dir, pair_name)
            output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg.dim")
            
            if self.check_output_file_valid(output_file):
                print(f"  Skipping {pair_name} (already exists)")
                continue
            
            cmd = [
                *self.gpt_base_cmd(),
                merge_graph,
                f"-Pdeb_file_list={','.join(deb_file_list)}",
                f"-Poutput_merged_file={output_file}"
            ]
            
            commands_data.append((cmd, f"Merge {pair_name}", pair_name, output_file))
        
        if not commands_data:
            print(f"  All pairs already merged")
        else:
            print(f"\n  Merging {len(commands_data)} pairs...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                "Step 8 - Merge Swaths"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        
        print(f"\n  ✓ Step 6 complete\n")
        return True
    
    
    def step08_subset_interferograms(self):
        print(f"\n{'='*80}")
        print(f"STEP 9: Subset Interferograms")
        print(f"{'='*80}")
        
        aoi_region = self.config.get('clip_roi', '')
        
        if not aoi_region:
            print(f"  ⚠ No clip_roi defined in config, skipping subset")
            print(f"  ℹ Add 'clip_roi' parameter to config to enable subsetting")
            print(f"  ✓ Step 7 skipped (no subsetting needed)\n")
            return True
        
        aoi_region = self.normalize_clip_roi(aoi_region)
        
        subset_graph = self.get_graph_file('subset_intf.xml')
        if not os.path.exists(subset_graph):
            print(f"  ⚠ Subset graph not found: {subset_graph}")
            print(f"  Skipping subset step")
            return True
        
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        
        if use_merged:
            merge_folder_name = self.get_merge_folder_name(swath_config)
            merge_deb_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_{self.master_date}')
            use_merged = os.path.exists(merge_deb_dir) and os.listdir(merge_deb_dir)
            if use_merged:
                print(f"  Using merged inputs from {merge_folder_name}/multi_intf_deb_mrg_{self.master_date}/")
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            
            if use_merged:
                input_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_{self.master_date}')
                input_suffix = "_mrg"
            else:
                input_base_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_{self.master_date}')
                input_suffix = ""
            
            if not os.path.exists(input_base_dir):
                print(f"  ⚠ Input directory not found: {input_base_dir}")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(input_base_dir)
            
            if not found_pairs:
                print(f"  ⚠ No pairs found in previous step output")
                continue
            
            print(f"  Found {len(found_pairs)} pair(s)")
            
            commands_data = []
            skipped_count = 0
            
            for pair_name in found_pairs:
                if self._skip_pair_folder_for_current_mode(pair_name):
                    print(f"  Skipping {pair_name}: excluded by master-date rule")
                    continue
                
                if use_merged:
                    input_file = os.path.join(input_base_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg.dim")
                else:
                    input_file = os.path.join(input_base_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb_mmifg.dim")
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input file not found")
                    continue
                
                if use_merged:
                    output_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_subset_{self.master_date}', pair_name)
                else:
                    output_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_subset_{self.master_date}', pair_name)
                os.makedirs(output_dir, exist_ok=True)
                output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mmifg{input_suffix}_subset.dim")
                
                if self.check_output_file_valid(output_file):
                    skipped_count += 1
                    continue
                else:
                    if os.path.exists(output_file):
                        data_dir = output_file.replace('.dim', '.data')
                        if not os.path.exists(data_dir):
                            print(f"  ⚠ {pair_name}: .dim exists but .data directory missing")
                        elif not os.listdir(data_dir):
                            print(f"  ⚠ {pair_name}: .data directory is empty")
                        else:
                            print(f"  ⚠ {pair_name}: File size is 0")
                
                cmd = [
                    *self.gpt_base_cmd(),
                    subset_graph,
                    f"-Pintf_source={input_file}",
                    f"-Paoi_region_subset={aoi_region}",
                    f"-Poutput_subset_dir={output_file}"
                ]
                
                commands_data.append((cmd, f"Subset {pair_name}", pair_name, output_file))
            
            if skipped_count > 0:
                print(f"  Skipped {skipped_count} pair(s) (already processed)")
            
            if not commands_data:
                print(f"  All {swath_name} pairs already subsetted")
                continue
            
            print(f"\n  Processing {len(commands_data)} subset operation(s)...")
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 9 - Subset ({swath_name})"
            )
            
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed subsetting")
                return False
            
            if use_merged:
                break
        
        return True
    
    
    def step10_multilook(self):
        if self.insar_target == 2 and self.insar_method == 2:
            print(f"\n{'='*80}")
            print(f"STEP 14: Multilook - SKIPPED")
            print(f"{'='*80}")
            print(f"  PS mode uses filtered pairs from previous step instead\n")
            return True

        if self.insar_target == 2 and self.insar_method == 1 and not self._range_looks_configured():
            print(f"\n{'='*80}")
            print(f"STEP 14: Multilook - SKIPPED")
            print(f"{'='*80}")
            print(f"  Range not set in config (SBAS StaMPS uses Step 12 bandmath without multilook)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 14: Multilook")
        print(f"{'='*80}")
        
        multilook_bandmath = (self.insar_target == 2 and self.insar_method == 1)
        
        if multilook_bandmath:
            return self._step10_multilook_bandmath()
        else:
            return self._step10_multilook_interferograms()
    
    def _step10_multilook_bandmath(self):
        """Multilook bandmath outputs from Step 12 (for SBAS StaMPS mode)"""
        print(f"  Mode: Multilooking bandmath outputs from Step 12")
        
        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]
        
        if is_multi_swath:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            input_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_bandmath_sbas_{self.master_date}')
            output_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_ml_bandmath_sbas_{self.master_date}')
            input_suffix = "_bandmath.dim"
            output_suffix = "_bandmath_ml.dim"
        else:
            swath_name = self.swaths_to_process[0]
            swath_root = os.path.join(self.proj_root, swath_name)
            input_dir = os.path.join(swath_root, f'multi_intf_deb_bandmath_sbas_{self.master_date}')
            output_dir = os.path.join(swath_root, f'multi_intf_deb_ml_bandmath_sbas_{self.master_date}')
            input_suffix = "_bandmath.dim"
            output_suffix = "_bandmath_ml.dim"
        
        if not os.path.exists(input_dir):
            print(f"  ✗ Input directory not found: {input_dir}")
            print(f"  Run Step 12 first to generate bandmath outputs")
            return False
        
        pairs = self.list_pair_dirs_check_only(input_dir)
        if not pairs:
            print(f"  ✗ No pairs found in {input_dir}")
            return False
        
        print(f"  Found {len(pairs)} pair(s)")
        print(f"  Input: {os.path.basename(input_dir)}/")
        print(f"  Output: {os.path.basename(output_dir)}/")
        print(f"  Range looks: {self.range_looks}")
        
        os.makedirs(output_dir, exist_ok=True)
        
        def pair_output_file(pn):
            return os.path.join(output_dir, pn, f"{pn}{output_suffix}")
        
        eligible = []
        commands_data = []
        skipped_count = 0
        
        for pair_name in sorted(pairs):
            input_file = os.path.join(input_dir, pair_name, f"{pair_name}{input_suffix}")
            output_file = pair_output_file(pair_name)
            
            if not os.path.exists(input_file):
                print(f"  ⚠ Skipping {pair_name}: input file not found")
                print(f"     Expected: {input_file}")
                continue
            
            eligible.append(pair_name)
            
            if self.check_output_file_valid(output_file):
                skipped_count += 1
                continue
            
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            cmd = self.build_step14_multilook_gpt_cmd(input_file, output_file)
            commands_data.append((cmd, f"Multilook bandmath {pair_name}", pair_name, output_file))
        
        if skipped_count > 0:
            print(f"  Skipped {skipped_count} pair(s) (already processed)")
        
        total_units = len(eligible)
        if total_units > 0:
            self._release_progress_begin(total_units)
            if skipped_count > 0:
                self._release_progress_advance(skipped_count)
        
        if not eligible:
            print(f"  ⚠ No eligible pairs for multilook bandmath")
            return True
        
        gate_pair = eligible[0]
        gate_out = pair_output_file(gate_pair)
        ctx_swath = os.path.basename(output_dir)
        
        def _fail_bandmath_gate(msg_ctx):
            print(f"\n{'='*80}")
            print(f"ERROR: coherence vs imaginary band grid mismatch after Multilook (Step 14) {msg_ctx}")
            print(f"{'='*80}")
            print(f"  Check Range/Azimuth multilook parameters in configuration.")
            print(f"  Delete the multilooked bandmath folder and re-run Step 14, e.g.:")
            print(f"    {output_dir}")
            print(f"{'='*80}\n")
            if os.path.isdir(output_dir):
                try:
                    shutil.rmtree(output_dir)
                    print(f"  Removed: {output_dir}")
                except OSError as e:
                    print(f"  ⚠ Failed to remove {output_dir}: {e}")
            sys.exit(1)
        
        if self._multilook_run_envi_coh_i_grid_check():
            print(f"\n  Multilook coh/i grid gate pair: {gate_pair}")
        
        pilot_cmds = [c for c in commands_data if c[2] == gate_pair]
        other_cmds = [c for c in commands_data if c[2] != gate_pair]
        
        if pilot_cmds:
            cmd, desc, pair_name, out_f = pilot_cmds[0]
            if not self.run_command(cmd, desc, expected_output_file=out_f):
                return False
            self._release_progress_advance(1)
        
        if self._multilook_run_envi_coh_i_grid_check():
            gctx = f"({ctx_swath}, pair {gate_pair})"
            if not self.validate_ml_product_coh_i_grid_match(
                    gate_out, context=f'Multilook Step 14 bandmath {gctx}'):
                _fail_bandmath_gate(gctx)
        
        exec_tail = pilot_cmds[1:] + other_cmds
        if exec_tail:
            print(f"\n  Processing {len(exec_tail)} multilook bandmath operation(s)...")
            success_list, failed_list = self.run_commands_parallel(
                exec_tail,
                f"Step 14 - Multilook bandmath ({ctx_swath})",
                reset_progress=False,
                progress_total=total_units,
            )
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                return False
        elif not commands_data:
            print(f"  All pairs already processed")
            if self._multilook_run_envi_coh_i_grid_check():
                gctx = f"({ctx_swath}, pair {gate_pair})"
                if not self.validate_ml_product_coh_i_grid_match(
                        gate_out, context=f'Multilook Step 14 bandmath {gctx}'):
                    _fail_bandmath_gate(gctx)
        
        print(f"\n  ✓ Step 14 complete (bandmath multilook)\n")
        return True
    
    def _step10_multilook_interferograms(self):
        """Multilook interferograms from previous steps (for LiCSBAS mode)"""
        print(f"  Mode: Multilooking interferograms")
        
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        
        use_subset = False
        if use_merged:
            merge_folder_name = self.get_merge_folder_name(swath_config)
            merge_subset_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_subset_{self.master_date}')
            merge_deb_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_{self.master_date}')
            
            if os.path.exists(merge_subset_dir) and os.listdir(merge_subset_dir):
                use_subset = True
                use_merged = True
                print(f"  Using merged subset inputs from {merge_folder_name}/multi_intf_deb_mrg_subset_{self.master_date}/")
            elif os.path.exists(merge_deb_dir) and os.listdir(merge_deb_dir):
                use_merged = True
                print(f"  Using merged inputs from {merge_folder_name}/multi_intf_deb_mrg_{self.master_date}/")
            else:
                use_merged = False
        
        if not use_merged and not use_subset:
            for swath_name in self.swaths_to_process:
                subset_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_subset_{self.master_date}')
                if os.path.exists(subset_dir) and os.listdir(subset_dir):
                    use_subset = True
                    print(f"  Detected subset output from Step 9")
                    break
        
        for swath_name in self.swaths_to_process:
            print(f"\nProcessing swath: {swath_name}")
            print(f"  Range looks: {self.range_looks}")
            
            use_vector_mask_lc = (
                self.insar_target == 1 and bool(self.get_msk_shp_path()))
            
            if use_vector_mask_lc:
                print(
                    "  LiCSBAS + msk_shp: multilooking vector-mask interferograms "
                    "(multi_intf_deb_*_ml_flt_shp_mask_*)")
                if use_merged:
                    input_base_dir = os.path.join(
                        self.proj_root, merge_folder_name,
                        f'multi_intf_deb_mrg_ml_flt_shp_mask_{self.master_date}')
                else:
                    input_base_dir = os.path.join(
                        self.proj_root, swath_name,
                        f'multi_intf_deb_ml_flt_shp_mask_{self.master_date}')
            elif use_merged:
                if use_subset:
                    input_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_subset_{self.master_date}')
                    input_file_suffix = "_sub"
                else:
                    input_base_dir = os.path.join(self.proj_root, merge_folder_name, f'multi_intf_deb_mrg_{self.master_date}')
                    input_file_suffix = ""
                input_suffix = "_mrg"
            else:
                if use_subset:
                    input_base_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_subset_{self.master_date}')
                    input_file_suffix = "_sub"
                else:
                    input_base_dir = os.path.join(self.proj_root, swath_name, f'multi_intf_deb_{self.master_date}')
                    input_file_suffix = ""
                input_suffix = ""
            
            if not os.path.exists(input_base_dir):
                print(f"  ⚠ Input directory not found: {input_base_dir}")
                continue
            
            found_pairs = self.list_pair_dirs_check_only(input_base_dir)
            
            if not found_pairs:
                print(f"  ⚠ No pairs found in previous step output")
                continue
            
            print(f"  Found {len(found_pairs)} pair(s)")
            
            def multilook_io_paths(pair_name):
                if use_vector_mask_lc:
                    if use_merged:
                        input_file = os.path.join(
                            input_base_dir, pair_name,
                            f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg_ml_flt_shp_mask.dim")
                        output_dir = os.path.join(
                            self.proj_root, merge_folder_name,
                            f'multi_intf_deb_mrg_ml_{self.master_date}', pair_name)
                        output_file = os.path.join(
                            output_dir,
                            f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg_ml.dim")
                    else:
                        input_file = os.path.join(
                            input_base_dir, pair_name,
                            f"{pair_name}_Stack_esd_mask_deb_mmifg_ml_flt_shp_mask.dim")
                        output_dir = os.path.join(
                            self.proj_root, swath_name,
                            f'multi_intf_deb_ml_{self.master_date}', pair_name)
                        output_file = os.path.join(
                            output_dir,
                            f"{pair_name}_Stack_esd_mask_deb_mmifg_ml.dim")
                    return input_file, output_file
                if use_subset:
                    input_file = os.path.join(
                        input_base_dir, pair_name,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg{input_suffix}_subset.dim")
                elif use_merged:
                    input_file = os.path.join(
                        input_base_dir, pair_name,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg_mrg.dim")
                else:
                    input_file = os.path.join(
                        input_base_dir, pair_name,
                        f"{pair_name}_Stack_esd_mask_deb_mmifg.dim")
                if use_merged:
                    output_dir = os.path.join(
                        self.proj_root, merge_folder_name,
                        f'multi_intf_deb_mrg_ml_{self.master_date}', pair_name)
                else:
                    output_dir = os.path.join(
                        self.proj_root, swath_name,
                        f'multi_intf_deb_ml_{self.master_date}', pair_name)
                output_file = os.path.join(
                    output_dir,
                    f"{pair_name}_Stack_esd_mask_deb_mmifg{input_suffix}_ml.dim")
                return input_file, output_file
            
            eligible = []
            commands_data = []
            skipped_count = 0
            
            for pair_name in sorted(found_pairs):
                if self.insar_target == 1:
                    parts = pair_name.split('_')
                    if len(parts) == 2 and (parts[0] == self.master_date or parts[1] == self.master_date):
                        print(f"  Skipping {pair_name}: Folder involves epoch master (LiCSBAS mode, no stack)")
                        continue
                
                input_file, output_file = multilook_io_paths(pair_name)
                
                if not os.path.exists(input_file):
                    print(f"  ⚠ Skipping {pair_name}: Input file not found")
                    continue
                
                eligible.append(pair_name)
                
                if self.check_output_file_valid(output_file):
                    skipped_count += 1
                    continue
                
                cmd = self.build_step14_multilook_gpt_cmd(input_file, output_file)
                commands_data.append((cmd, f"Multilook {pair_name}", pair_name, output_file))
            
            if skipped_count > 0:
                print(f"  Skipped {skipped_count} pair(s) (already processed)")
            
            total_units = len(eligible)
            if total_units > 0:
                self._release_progress_begin(total_units)
                if skipped_count > 0:
                    self._release_progress_advance(skipped_count)
            
            if not eligible:
                print(f"  ⚠ No eligible pairs for Multilook in {swath_name}")
                if use_merged:
                    break
                continue
            
            gate_pair = eligible[0]
            _, gate_out = multilook_io_paths(gate_pair)
            
            if self._multilook_run_envi_coh_i_grid_check():
                print(f"\n  Multilook coh/i grid gate pair: {gate_pair}")
            
            def _fail_gate_mismatch_swath(msg_ctx):
                print(f"\n{'='*80}")
                print(f"ERROR: coherence vs imaginary band grid mismatch after Multilook (Step 14) {msg_ctx}")
                print(f"{'='*80}")
                print(f"  Check Range/Azimuth multilook parameters in configuration.")
                print(f"  Actions:")
                print(f"    1. Change Range and/or Azimuth looks if needed.")
                print(f"    2. Delete Step 14 (multilook) and Step 18 (Goldstein) output folders.")
                print(f"    3. Re-run from Step 14.")
                print(f"{'='*80}\n")
                merge_ref = merge_folder_name if use_merged else None
                self.cleanup_multilook_and_goldstein_output_roots(swath_name, use_merged, use_subset, merge_ref)
                sys.exit(1)
            
            pilot_cmds = [c for c in commands_data if c[2] == gate_pair]
            other_cmds = [c for c in commands_data if c[2] != gate_pair]
            
            if pilot_cmds:
                cmd, desc, pair_name, output_file = pilot_cmds[0]
                if not self.run_command(cmd, desc, expected_output_file=output_file):
                    return False
                self._release_progress_advance(1)
            
            if self._multilook_run_envi_coh_i_grid_check():
                gate_ctx = f"({swath_name}, pair {gate_pair})"
                if not self.validate_ml_product_coh_i_grid_match(
                        gate_out, context=f'Multilook Step 14 {gate_ctx}'):
                    _fail_gate_mismatch_swath(gate_ctx)
            
            exec_tail = pilot_cmds[1:] + other_cmds
            if exec_tail:
                print(f"\n  Processing {len(exec_tail)} multilook operation(s)...")
                success_list, failed_list = self.run_commands_parallel(
                    exec_tail,
                    f"Step 14 - Multilook ({swath_name})",
                    reset_progress=False,
                    progress_total=total_units,
                )
                if failed_list:
                    print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed")
                    return False
            elif not commands_data:
                print(f"  All {swath_name} multilook outputs already present")
                if self._multilook_run_envi_coh_i_grid_check():
                    gate_ctx = f"({swath_name}, pair {gate_pair})"
                    if not self.validate_ml_product_coh_i_grid_match(
                            gate_out, context=f'Multilook Step 14 {gate_ctx}'):
                        _fail_gate_mismatch_swath(gate_ctx)
            
            if use_merged:
                break
        
        return True
    
    def apply_coherence_mask_to_dim(self, dim_path, threshold=0.3):
        try:
            import xml.etree.ElementTree as ET
            
            tree = ET.parse(dim_path)
            root = tree.getroot()
            
            modified = False
            
            for band_info in root.findall('.//Spectral_Band_Info'):
                band_desc = band_info.find('BAND_DESCRIPTION')
                if band_desc is None or band_desc.text != 'Phase from complex data':
                    continue
                
                band_name_elem = band_info.find('BAND_NAME')
                if band_name_elem is None:
                    continue
                
                band_name = band_name_elem.text
                if not band_name.startswith('Phase_ifg_'):
                    continue
                
                coh_band_name = band_name.replace('Phase_ifg_', 'coh_')
                
                expr_elem = band_info.find('EXPRESSION')
                if expr_elem is None:
                    continue
                
                original_expr = expr_elem.text
                
                if 'if ' in original_expr and ' then ' in original_expr:
                    continue
                
                masked_expr = f"if {coh_band_name}>{threshold} then {original_expr} else 0"
                expr_elem.text = masked_expr
                modified = True
                
                print(f"    ✓ Applied coherence mask to {band_name}")
            
            if modified:
                tree.write(dim_path, encoding='utf-8', xml_declaration=True)
                return True
            
            return False
            
        except Exception as e:
            print(f"    WARNING: Failed to apply coherence mask to {dim_path}: {e}")
            return False
    
    def parse_envi_hdr_samples_lines(self, hdr_path):
        """Read samples and lines from ENVI-style .hdr file."""
        try:
            with open(hdr_path, 'r', encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError as e:
            print(f"  ⚠ Cannot read {hdr_path}: {e}")
            return None
        sm = re.search(r'^\s*samples\s*=\s*(\d+)', text, re.IGNORECASE | re.MULTILINE)
        ln = re.search(r'^\s*lines\s*=\s*(\d+)', text, re.IGNORECASE | re.MULTILINE)
        if not sm or not ln:
            return None
        return int(sm.group(1)), int(ln.group(1))
    
    _MULTILOOK_IFG_PREFIXES = (
        ('i_ifg_', 0),
        ('q_ifg_', 1),
        ('Intensity_ifg_', 2),
        ('coh_', 3),
    )

    def _ifg_date_pair_suffix_from_band(self, band_name):
        """Trailing ``DDMmmYYYY_DDMmmYYYY`` from an IFG band name (preserves SNAP date order)."""
        for prefix, _ in self._MULTILOOK_IFG_PREFIXES:
            if band_name.startswith(prefix):
                parts = band_name[len(prefix):].split('_')
                if len(parts) >= 2:
                    return f"{parts[-2]}_{parts[-1]}"
        return None

    def _ifg_group_sort_key(self, pair_suffix):
        """Order IFG groups for triple stacks: geo-style master-second before master-first per slave date."""
        parts = pair_suffix.split('_')
        if len(parts) != 2:
            return (9, pair_suffix)
        d1 = self.convert_date_format_reverse(parts[0])
        d2 = self.convert_date_format_reverse(parts[1])
        if not d1 or not d2:
            return (9, pair_suffix)
        m = self.master_date
        if m and d2 == m:
            return (0, d1, 0)
        if m and d1 == m:
            return (0, d2, 1)
        return (1, d1, d2)

    def get_multilook_source_bands_from_dim(self, dim_path):
        if not dim_path or not str(dim_path).lower().endswith('.dim'):
            return None
        data_dir = dim_path[:-4] + '.data'
        if not os.path.isdir(data_dir):
            return None
        geo_leading = set()
        geo_trailing = set()
        ifg_groups = {}
        try:
            for name in os.listdir(data_dir):
                if not name.endswith('.img'):
                    continue
                if name.startswith('Phase'):
                    continue
                band = name[:-4]
                if band in ('elevation', 'incidenceAngle'):
                    geo_leading.add(band)
                    continue
                if band in ('orthorectifiedLon', 'orthorectifiedLat'):
                    geo_trailing.add(band)
                    continue
                for prefix, slot in self._MULTILOOK_IFG_PREFIXES:
                    if band.startswith(prefix):
                        pair_suffix = self._ifg_date_pair_suffix_from_band(band)
                        if pair_suffix:
                            ifg_groups.setdefault(pair_suffix, {})[slot] = band
                        break
        except OSError:
            return None
        if not geo_leading and not geo_trailing and not ifg_groups:
            return None
        ordered = []
        for g in ('elevation', 'incidenceAngle'):
            if g in geo_leading:
                ordered.append(g)
        for pair_suffix in sorted(ifg_groups.keys(), key=self._ifg_group_sort_key):
            slots = ifg_groups[pair_suffix]
            for slot in range(4):
                if slot in slots:
                    ordered.append(slots[slot])
        for g in ('orthorectifiedLon', 'orthorectifiedLat'):
            if g in geo_trailing:
                ordered.append(g)
        return ','.join(ordered)
    
