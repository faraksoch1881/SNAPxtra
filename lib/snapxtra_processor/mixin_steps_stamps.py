"""StaMPS bandmath and export steps"""
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


class StepsStampsMixin:

    def step10_filter_ps_pairs(self):
        if self.insar_target != 2 or self.insar_method != 2:
            if self.insar_target == 1:
                skip_reason = "PS filtering only needed for StaMPS (insar_target=2)"
            elif self.insar_method == 1:
                skip_reason = "PS filtering only for insar_method=2, skipping for SBAS (method=1)"
            else:
                skip_reason = "Not applicable"
            
            print(f"\n{'='*80}")
            print(f"STEP 13: Filter PS Pairs - SKIPPED")
            print(f"{'='*80}")
            print(f"  {skip_reason}\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 13: Filter PS Pairs")
        print(f"{'='*80}")
        
        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]
        
        if is_multi_swath:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            input_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_bandmath_sbas_{self.master_date}')
            output_dir = os.path.join(merge_dir, f'multi_intf_deb_mrg_bandmath_ps_{self.master_date}')
        else:
            swath_name = self.swaths_to_process[0]
            swath_root = os.path.join(self.proj_root, swath_name)
            input_dir = os.path.join(swath_root, f'multi_intf_deb_bandmath_sbas_{self.master_date}')
            output_dir = os.path.join(swath_root, f'multi_intf_deb_bandmath_ps_{self.master_date}')
        
        if not os.path.exists(input_dir):
            print(f"  ✗ Input directory not found: {input_dir}")
            print(f"  Run Step 12 first")
            return False

        if not self.ps_selected_pair_names:
            if not self.load_sbas_pairs():
                return False

        pair_to_target_dates = dict(self.ps_pair_to_target_dates)
        selected_pairs = sorted(self.ps_selected_pair_names)
        if not selected_pairs:
            print(f"  ✗ No PS pairs selected from sbas_pairs.txt")
            return False

        on_disk = set(self.list_pair_dirs(input_dir))
        missing = [p for p in selected_pairs if p not in on_disk]
        if missing:
            print(f"  ⚠ {len(missing)} PS-selected pair(s) missing from Step 12 output:")
            for p in missing:
                print(f"    • {p}")

        selected_pairs = [p for p in selected_pairs if p in on_disk]
        if not selected_pairs:
            print(f"  ✗ None of the PS-selected pairs exist under {input_dir}")
            return False

        all_dates = set()
        for pn in selected_pairs:
            d1, d2 = pn.split('_')
            if d1 != self.master_date:
                all_dates.add(d1)
            if d2 != self.master_date:
                all_dates.add(d2)
        unique_dates = sorted(all_dates)
        first_date = unique_dates[0] if unique_dates else None
        last_date = unique_dates[-1] if unique_dates else None

        print(f"  PS-selected {len(selected_pairs)} pair(s) from sbas_pairs.txt "
              f"({len(on_disk)} present in Step 12 output)")
        print(f"  Unique slave dates covered: {len(unique_dates)}")
        print(f"  Selected {len(selected_pairs)} PS pairs")
        
        os.makedirs(output_dir, exist_ok=True)
        
        master_fmt = self.convert_date_format(self.master_date)
        
        for pair_name in selected_pairs:
            target_dates = pair_to_target_dates[pair_name]
            target_dates_sorted = sorted(target_dates)
            
            target_dates_fmt = [self.convert_date_format(d) for d in target_dates_sorted]
            
            master_target_pairs = [f"{master_fmt}_{date_fmt}" for date_fmt in target_dates_fmt]
            
            if len(target_dates_fmt) == 1:
                print(f"\n  Processing {pair_name} (target date: {target_dates_fmt[0]})")
                if target_dates_sorted[0] == first_date:
                    print(f"    Keeping bands with suffix: {master_target_pairs[0]} (#First Date)")
                elif target_dates_sorted[0] == last_date:
                    print(f"    Keeping bands with suffix: {master_target_pairs[0]} (#Last Date)")
                else:
                    print(f"    Keeping bands with suffix: {master_target_pairs[0]}")
            else:
                dates_str = ', '.join(target_dates_fmt)
                suffixes_str = ' and '.join(master_target_pairs)
                special_note = ""
                if first_date in target_dates_sorted and last_date in target_dates_sorted:
                    special_note = " (#First Date, #Last Date)"
                elif first_date in target_dates_sorted:
                    special_note = " (#First Date)"
                elif last_date in target_dates_sorted:
                    special_note = " (#Last Date)"
                print(f"\n  Processing {pair_name} (target dates: {dates_str}){special_note}")
                print(f"    Keeping bands with suffix: {suffixes_str}")
            
            source_pair_dir = os.path.join(input_dir, pair_name)
            dest_pair_dir = os.path.join(output_dir, pair_name)
            
            if os.path.exists(dest_pair_dir):
                print(f"    Skipping (already exists)")
                continue
            
            import shutil
            try:
                shutil.copytree(source_pair_dir, dest_pair_dir)
                print(f"    ✓ Copied to {os.path.basename(output_dir)}/")
            except Exception as e:
                print(f"    ✗ Failed to copy: {e}")
                continue
            
            data_folder = None
            for item in os.listdir(dest_pair_dir):
                if item.endswith('.data'):
                    data_folder = os.path.join(dest_pair_dir, item)
                    break
            
            if not data_folder or not os.path.exists(data_folder):
                print(f"    ⚠ No .data folder found")
                continue
            
            removed_bands = []
            kept_bands = []
            
            for filename in os.listdir(data_folder):
                if not (filename.endswith('.hdr') or filename.endswith('.img')):
                    continue
                
                keep_always = ['elevation', 'incidenceAngle', 'orthorectifiedLat', 'orthorectifiedLon']
                if any(filename.startswith(keep) for keep in keep_always):
                    kept_bands.append(filename)
                    continue
                
                filepath = os.path.join(data_folder, filename)
                if os.path.isdir(filepath):
                    continue
                
                basename = os.path.splitext(filename)[0]
                if any(basename.endswith(suffix) for suffix in master_target_pairs):
                    kept_bands.append(filename)
                else:
                    try:
                        os.remove(filepath)
                        removed_bands.append(filename)
                    except Exception as e:
                        print(f"      ⚠ Failed to remove {filename}: {e}")
            
            band_sets_removed = len([b for b in removed_bands if b.endswith('.hdr')])
            total_bands_removed = band_sets_removed
            
            print(f"    Removed {len(removed_bands)} files ({total_bands_removed} band entries)")
            print(f"    Kept {len(kept_bands)} files")
            
            dim_file = None
            for filename in os.listdir(dest_pair_dir):
                if filename.endswith('.dim'):
                    dim_file = os.path.join(dest_pair_dir, filename)
                    break
            
            if dim_file and os.path.exists(dim_file):
                try:
                    self.edit_dim_file_for_ps(dim_file, master_target_pairs, total_bands_removed)
                    print(f"    ✓ Updated .dim metadata")
                except Exception as e:
                    print(f"    ✗ Failed to edit .dim: {e}")
        
        print(f"\n  {len(selected_pairs)} PS pairs prepared\n")
        return True
    
    def edit_dim_file_for_ps(self, dim_file, master_target_pairs, bands_removed):
        import xml.etree.ElementTree as ET
        
        try:
            tree = ET.parse(dim_file)
            root = tree.getroot()
            
            raster_dims = root.find('.//Raster_Dimensions')
            if raster_dims is not None:
                nbands_elem = raster_dims.find('NBANDS')
                if nbands_elem is not None:
                    current_nbands = int(nbands_elem.text)
                    new_nbands = current_nbands - bands_removed
                    nbands_elem.text = str(new_nbands)
            
            image_interp = root.find('.//Image_Interpretation')
            if image_interp is not None:
                bands_to_remove = []
                removed_indices = []
                
                for band_info in image_interp.findall('Spectral_Band_Info'):
                    band_name_elem = band_info.find('BAND_NAME')
                    if band_name_elem is not None:
                        band_name = band_name_elem.text
                        keep_bands = ['elevation', 'incidenceAngle', 'orthorectifiedLat', 'orthorectifiedLon']
                        should_keep = any(band_name.startswith(kb) for kb in keep_bands) or \
                                     any(band_name.endswith(suffix) for suffix in master_target_pairs)
                        if not should_keep:
                            bands_to_remove.append(band_info)
                            band_idx_elem = band_info.find('BAND_INDEX')
                            if band_idx_elem is not None:
                                removed_indices.append(int(band_idx_elem.text))
                
                for band_info in bands_to_remove:
                    image_interp.remove(band_info)
                
                remaining_bands = image_interp.findall('Spectral_Band_Info')
                new_index = 0
                for band_info in remaining_bands:
                    band_idx_elem = band_info.find('BAND_INDEX')
                    if band_idx_elem is not None:
                        band_idx_elem.text = str(new_index)
                        new_index += 1
            
            data_access = root.find('.//Data_Access')
            if data_access is not None:
                files_to_remove = []
                
                for data_file in data_access.findall('Data_File'):
                    data_path_elem = data_file.find('.//DATA_FILE_PATH')
                    if data_path_elem is not None:
                        href = data_path_elem.get('href', '')
                        basename = os.path.basename(href).replace('.hdr', '')
                        keep_bands = ['elevation', 'incidenceAngle', 'orthorectifiedLat', 'orthorectifiedLon']
                        should_keep = any(basename.startswith(kb) for kb in keep_bands) or \
                                     any(basename.endswith(suffix) for suffix in master_target_pairs)
                        if not should_keep:
                            files_to_remove.append(data_file)
                
                for data_file in files_to_remove:
                    data_access.remove(data_file)
                
                remaining_files = data_access.findall('Data_File')
                new_index = 0
                for data_file in remaining_files:
                    band_idx_elem = data_file.find('BAND_INDEX')
                    if band_idx_elem is not None:
                        band_idx_elem.text = str(new_index)
                        new_index += 1
            
            for geoposition in root.findall('.//Geoposition'):
                band_idx_elem = geoposition.find('BAND_INDEX')
                if band_idx_elem is not None:
                    band_idx = int(band_idx_elem.text)
                    if band_idx in removed_indices:
                        parent = root
                        for elem in root.iter():
                            if geoposition in list(elem):
                                parent = elem
                                break
                        
                        children = list(parent)
                        geopos_idx = children.index(geoposition)
                        if geopos_idx > 0 and children[geopos_idx - 1].tag == 'Coordinate_Reference_System':
                            parent.remove(children[geopos_idx - 1])
                        parent.remove(geoposition)
            
            new_index = 0
            for geoposition in root.findall('.//Geoposition'):
                band_idx_elem = geoposition.find('BAND_INDEX')
                if band_idx_elem is not None:
                    band_idx_elem.text = str(new_index)
                    new_index += 1
            
            tree.write(dim_file, encoding='utf-8', xml_declaration=True)
            return True
            
        except Exception as e:
            print(f"      Error editing .dim file: {e}")
            return False
    
    
    def step11_merge_coreg_stamps(self):
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 15: Merge Coreg for StaMPS - SKIPPED")
            print(f"{'='*80}")
            print(f"  insar_target={self.insar_target} (Merge coreg only needed for StaMPS, insar_target=2)\n")
            return True
        
        swath_config = self.get_swath_config_value()
        if swath_config in [1, 2, 3]:
            print(f"\n{'='*80}")
            print(f"STEP 15: Merge Coreg for StaMPS - SKIPPED")
            print(f"{'='*80}")
            print(f"  Single swath configuration (swath={swath_config})\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 15: Merge Coreg for StaMPS")
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
        
        merge_folder = self.get_merge_folder_name(swath_config)
        merge_dir = os.path.join(self.proj_root, merge_folder)
        os.makedirs(merge_dir, exist_ok=True)
        
        print(f"  Merging swaths: {', '.join(swaths)}")
        
        all_swath_pairs = []
        for swath in swaths:
            coreg_deb_dir = os.path.join(self.proj_root, swath, f'coreg_stack_mask_deb_{self.master_date}')
            if os.path.exists(coreg_deb_dir):
                swath_pairs = set(self.list_pair_dirs_check_only(coreg_deb_dir))
                all_swath_pairs.append(swath_pairs)
            else:
                print(f"  ✗ Coreg directory not found for {swath}: {coreg_deb_dir}")
                print(f"  Run Steps 1-5 first to generate coreg_stack_mask_deb_{self.master_date}/")
                return False
        
        if not all_swath_pairs:
            print(f"  ✗ No coreg_stack_mask_deb directories found in any swath")
            return False
        
        common_pairs = set.intersection(*all_swath_pairs)
        if not common_pairs:
            print(f"  ✗ No common pairs found across all swaths")
            return False
        
        pairs = sorted(p for p in common_pairs if not self._skip_pair_folder_for_current_mode(p))
        if not pairs:
            print(f"  ✗ No common pairs left after master-date exclusions for active mode")
            return False
        print(f"  Found {len(pairs)} common pairs across all swaths (after exclusions)")
        
        coreg_deb_mrg_dir = os.path.join(merge_dir, f'coreg_deb_mrg_{self.master_date}')
        os.makedirs(coreg_deb_mrg_dir, exist_ok=True)
        
        merge_swath_graph = self.get_graph_file('merge_swath_sbas.xml')
        if not os.path.exists(merge_swath_graph):
            print(f"  ✗ Merge graph not found: {merge_swath_graph}")
            return False
        
        print(f"  Output: {merge_folder}/coreg_deb_mrg_{self.master_date}/")
        
        for pair_name in pairs:
            swath_file_list = []
            for swath in swaths:
                swath_file = os.path.join(
                    self.proj_root,
                    swath,
                    f'coreg_stack_mask_deb_{self.master_date}',
                    pair_name,
                    f"{pair_name}_Stack_esd_mask_deb.dim"
                )
                if not os.path.exists(swath_file):
                    print(f"  ✗ Missing: {swath_file}")
                    break
                swath_file_list.append(swath_file)
            
            if len(swath_file_list) != len(swaths):
                print(f"  ⚠ Skipping {pair_name}: incomplete swath coverage")
                continue
            
            output_dir = os.path.join(coreg_deb_mrg_dir, pair_name)
            output_file = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mrg.dim")
            
            if os.path.exists(output_file):
                continue
            
            os.makedirs(output_dir, exist_ok=True)
            cmd = [
                *self.gpt_base_cmd(),
                merge_swath_graph,
                f"-Pdeb_file_list={','.join(swath_file_list)}",
                f"-Poutput_merged_file={output_file}"
            ]
            
            success = self.run_command(cmd, f"Merge coreg {pair_name}")
            if not success:
                print(f"  ✗ Merge failed for {pair_name}")
                continue
        
        print(f"\n  ✓ Step 15 complete\n")
        return True
    
    
    def step12_subset_coreg_stamps(self):
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 16: Subset Coreg for StaMPS - SKIPPED")
            print(f"{'='*80}")
            print(f"  insar_target={self.insar_target} (Subset coreg only needed for StaMPS, insar_target=2)\n")
            return True
        
        aoi_region = self.config.get('clip_roi', '')
        if not aoi_region:
            print(f"\n{'='*80}")
            print(f"STEP 16: Subset Coreg for StaMPS - SKIPPED")
            print(f"{'='*80}")
            print(f"  No clip_roi defined in config\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 16: Subset Coreg for StaMPS")
        print(f"{'='*80}")
        
        aoi_region_wkt = self.normalize_clip_roi(aoi_region)
        
        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]
        
        subset_graph = self.get_graph_file('subset_intf.xml')
        if not os.path.exists(subset_graph):
            print(f"  ✗ Subset graph not found: {subset_graph}")
            return False
        
        if is_multi_swath:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            
            coreg_input_dir = os.path.join(merge_dir, f'coreg_deb_mrg_{self.master_date}')
            if not os.path.exists(coreg_input_dir):
                print(f"  ✗ Merged coreg directory not found: {coreg_input_dir}")
                print(f"  Run Step 15 first to generate coreg_deb_mrg_{self.master_date}/")
                return False
            
            coreg_subset_dir = os.path.join(merge_dir, f'coreg_deb_mrg_subset_{self.master_date}')
            os.makedirs(coreg_subset_dir, exist_ok=True)
            
            pairs = self.list_pair_dirs_check_only(coreg_input_dir)
            if not pairs:
                print(f"  ✗ No pairs found in {coreg_input_dir}")
                return False
            
            print(f"  Processing {len(pairs)} pairs")
            print(f"  Input: {merge_folder}/coreg_deb_mrg_{self.master_date}/")
            print(f"  Output: {merge_folder}/coreg_deb_mrg_subset_{self.master_date}/")
            
            for pair_name in pairs:
                source_dim = os.path.join(coreg_input_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb_mrg.dim")
                output_dir = os.path.join(coreg_subset_dir, pair_name)
                output_dim = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_mrg_subset.dim")
                
                if os.path.exists(output_dim):
                    continue
                
                if not os.path.exists(source_dim):
                    print(f"  ⚠ Skipping {pair_name}: merged source not found")
                    continue
                
                os.makedirs(output_dir, exist_ok=True)
                cmd = [
                    *self.gpt_base_cmd(),
                    subset_graph,
                    f"-Pintf_source={source_dim}",
                    f"-Paoi_region_subset={aoi_region_wkt}",
                    f"-Poutput_subset_dir={output_dim}"
                ]
                
                success = self.run_command(cmd, f"Subset merged coreg {pair_name}", expected_output_file=output_dim)
                if not success:
                    print(f"  ✗ Subset failed for {pair_name}")
                    print(f"  Output not created: {output_dim}")
                    print(f"  Terminating Step 16")
                    return False
        
        else:
            swath_name = self.swaths_to_process[0]
            swath_root = os.path.join(self.proj_root, swath_name)
            
            coreg_input_dir = os.path.join(swath_root, f'coreg_stack_mask_deb_{self.master_date}')
            if not os.path.exists(coreg_input_dir):
                print(f"  ✗ Coreg directory not found: {coreg_input_dir}")
                print(f"  Run Steps 1-3 first to generate coreg_stack_mask_deb_{self.master_date}/")
                return False
            
            coreg_subset_dir = os.path.join(swath_root, f'coreg_stack_mask_deb_subset_{self.master_date}')
            os.makedirs(coreg_subset_dir, exist_ok=True)
            
            pairs = self.list_pair_dirs_check_only(coreg_input_dir)
            if not pairs:
                print(f"  ✗ No pairs found in {coreg_input_dir}")
                return False
            
            print(f"  Processing {len(pairs)} pairs for {swath_name}")
            print(f"  Input: coreg_stack_mask_deb_{self.master_date}/")
            print(f"  Output: coreg_stack_mask_deb_subset_{self.master_date}/")
            
            for pair_name in pairs:
                source_dim = os.path.join(coreg_input_dir, pair_name, f"{pair_name}_Stack_esd_mask_deb.dim")
                output_dir = os.path.join(coreg_subset_dir, pair_name)
                output_dim = os.path.join(output_dir, f"{pair_name}_Stack_esd_mask_deb_subset.dim")
                
                if os.path.exists(output_dim):
                    continue
                
                if not os.path.exists(source_dim):
                    print(f"  ⚠ Skipping {pair_name}: source not found")
                    continue
                
                os.makedirs(output_dir, exist_ok=True)
                cmd = [
                    *self.gpt_base_cmd(),
                    subset_graph,
                    f"-Pintf_source={source_dim}",
                    f"-Paoi_region_subset={aoi_region_wkt}",
                    f"-Poutput_subset_dir={output_dim}"
                ]
                
                success = self.run_command(cmd, f"Subset coreg {pair_name}", expected_output_file=output_dim)
                if not success:
                    print(f"  ✗ Subset failed for {pair_name}")
                    print(f"  Output not created: {output_dim}")
                    print(f"  Terminating Step 16")
                    return False
        
        return True
    
    
    def step13_multilook_coreg_stamps(self):
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 26: Multilook Coreg Stacks - SKIPPED")
            print(f"{'='*80}")
            print(f"  insar_target={self.insar_target} (only runs for StaMPS, insar_target=2)\n")
            return True

        if self.insar_method == 2:
            print(f"\n{'='*80}")
            print(f"STEP 26: Multilook Coregistered Stacks - SKIPPED")
            print(f"{'='*80}")
            print(f"  PS mode (insar_method=2): Step 27 uses full-resolution coreg from Step 25\n")
            return True

        if not self._range_looks_configured():
            print(f"\n{'='*80}")
            print(f"STEP 26: Multilook Coregistered Stacks - SKIPPED")
            print(f"{'='*80}")
            print(f"  Range not set in config (Step 27 uses full-resolution coreg)\n")
            return True

        print(f"\n{'='*80}")
        print(f"STEP 26: Multilook Coregistered Stacks (StaMPS)")
        print(f"{'='*80}")
        print(f"  Range looks: {self.range_looks}")
        print(f"  Azimuth looks: {self.azimuth_looks}")

        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]

        aoi_region = self.config.get('clip_roi', '')
        has_subset = bool(aoi_region)

        if is_multi_swath:
            return self._step12_multilook_coreg_multi_swath(has_subset)
        return self._step12_multilook_coreg_single_swath(has_subset)
    
    def _step12_multilook_coreg_single_swath(self, has_subset):
        """Multilook coregistered stacks for single swath (Step 26)."""
        swath_name = self.swaths_to_process[0]
        swath_root = os.path.join(self.proj_root, swath_name)

        coreg_input_dir, coreg_file_suffix = self._stamps_multilook_coreg_input_specs(
            False, None, swath_root, has_subset)
        if self.get_msk_shp_path():
            print(f"  Input: Step 25 output ({os.path.basename(coreg_input_dir)}/)")
        elif has_subset:
            print(f"  Input: Step 16 output (coreg_stack_mask_deb_subset_{self.master_date}/)")
        else:
            print(f"  Input: Step 03 output (coreg_stack_mask_deb_{self.master_date}/)")
        
        if not os.path.exists(coreg_input_dir):
            print(f"  ✗ Input directory not found: {coreg_input_dir}")
            if has_subset:
                print(f"  Run Step 16 first to generate coreg_stack_mask_deb_subset_{self.master_date}/")
            else:
                print(f"  Run Step 03 first to generate coreg_stack_mask_deb_{self.master_date}/")
            return False
        
        pairs = self.list_pair_dirs_check_only(coreg_input_dir, apply_ps_filter=False)
        if not pairs:
            print(f"  ✗ No pairs found in {coreg_input_dir}")
            return False
        
        print(f"  Processing {len(pairs)} pairs for {swath_name}")
        
        coreg_ml_dir = os.path.join(swath_root, f'coreg_stack_mask_deb_ml_{self.master_date}')
        os.makedirs(coreg_ml_dir, exist_ok=True)
        print(f"  Output: coreg_stack_mask_deb_ml_{self.master_date}/")
        
        commands_data = []
        skipped_count = 0
        for pair_name in pairs:
            source_dim = os.path.join(coreg_input_dir, pair_name, f"{pair_name}{coreg_file_suffix}")
            output_dir = os.path.join(coreg_ml_dir, pair_name)
            output_dim = os.path.join(output_dir, f"{pair_name}_coreg_ml.dim")
            
            if os.path.exists(output_dim):
                skipped_count += 1
                continue
            
            if not os.path.exists(source_dim):
                print(f"  ⚠ Skipping {pair_name}: source not found")
                skipped_count += 1
                continue
            
            os.makedirs(output_dir, exist_ok=True)
            cmd = [
                *self.gpt_base_cmd(),
                'Multilook',
                f"-Ssource={source_dim}",
                f"-PnRgLooks={self.range_looks}",
                '-t', output_dim
            ]
            commands_data.append(
                (cmd, f"Multilook coreg {pair_name}", pair_name, output_dim))
        
        total_units = len(pairs)
        if total_units > 0:
            self._release_progress_begin(total_units)
            if skipped_count > 0:
                self._release_progress_advance(skipped_count)
        
        if commands_data:
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 26 - Multilook coreg ({swath_name})",
                reset_progress=False,
                progress_total=total_units,
            )
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed multilook coreg")
                return False
        
        print(f"\n  ✓ Step 26 complete (single swath)\n")
        return True
    
    def _step12_multilook_coreg_multi_swath(self, has_subset):
        """Multilook coregistered stacks for multi-swath (Step 26)."""
        swath_config = self.get_swath_config_value()
        merge_folder = self.get_merge_folder_name(swath_config)
        merge_dir = os.path.join(self.proj_root, merge_folder)

        coreg_input_dir, coreg_file_suffix = self._stamps_multilook_coreg_input_specs(
            True, merge_dir, None, has_subset)
        if self.get_msk_shp_path():
            print(f"  Input: Step 25 output ({merge_folder}/{os.path.basename(coreg_input_dir)}/)")
        elif has_subset:
            print(f"  Input: Step 16 output ({merge_folder}/coreg_deb_mrg_subset_{self.master_date}/)")
        else:
            print(f"  Input: Step 15 output ({merge_folder}/coreg_deb_mrg_{self.master_date}/)")
        
        if not os.path.exists(coreg_input_dir):
            print(f"  ✗ Input directory not found: {coreg_input_dir}")
            if has_subset:
                print(f"  Run Step 16 first to generate coreg_deb_mrg_subset_{self.master_date}/")
            else:
                print(f"  Run Step 15 first to generate coreg_deb_mrg_{self.master_date}/")
            return False
        
        pairs = self.list_pair_dirs_check_only(coreg_input_dir, apply_ps_filter=False)
        if not pairs:
            print(f"  ✗ No pairs found in {coreg_input_dir}")
            return False
        
        print(f"  Processing {len(pairs)} pairs")
        
        coreg_ml_dir = os.path.join(merge_dir, f'coreg_deb_mrg_ml_{self.master_date}')
        os.makedirs(coreg_ml_dir, exist_ok=True)
        print(f"  Output: {merge_folder}/coreg_deb_mrg_ml_{self.master_date}/")
        
        commands_data = []
        skipped_count = 0
        for pair_name in pairs:
            source_dim = os.path.join(coreg_input_dir, pair_name, f"{pair_name}{coreg_file_suffix}")
            output_dir = os.path.join(coreg_ml_dir, pair_name)
            output_dim = os.path.join(output_dir, f"{pair_name}_coreg_mrg_ml.dim")
            
            if os.path.exists(output_dim):
                skipped_count += 1
                continue
            
            if not os.path.exists(source_dim):
                print(f"  ⚠ Skipping {pair_name}: source not found")
                skipped_count += 1
                continue
            
            os.makedirs(output_dir, exist_ok=True)
            cmd = [
                *self.gpt_base_cmd(),
                'Multilook',
                f"-Ssource={source_dim}",
                f"-PnRgLooks={self.range_looks}",
                '-t', output_dim
            ]
            commands_data.append(
                (cmd, f"Multilook merged coreg {pair_name}", pair_name, output_dim))
        
        total_units = len(pairs)
        if total_units > 0:
            self._release_progress_begin(total_units)
            if skipped_count > 0:
                self._release_progress_advance(skipped_count)
        
        if commands_data:
            success_list, failed_list = self.run_commands_parallel(
                commands_data,
                f"Step 26 - Multilook coreg ({merge_folder})",
                reset_progress=False,
                progress_total=total_units,
            )
            if failed_list:
                print(f"\n  ⚠️  WARNING: {len(failed_list)} pairs failed multilook coreg")
                return False
        
        print(f"\n  ✓ Step 26 complete (multi-swath)\n")
        return True
    
    def _stamps_coreg_mask_folder_names(self, merged_tree):
        """Basenames for StaMPS coreg vector mask (Steps 17/25/26) and Step 27 ``-Pcoreg_file``."""
        m = self.master_date
        if merged_tree:
            return {
                'sbas_shp': f'coreg_deb_mrg_sbas_shp_{m}',
                'ps_shp': f'coreg_deb_mrg_ps_shp_{m}',
                'sbas_msk': f'coreg_deb_mrg_sbas_shp_msk_{m}',
                'ps_msk': f'coreg_deb_mrg_ps_shp_msk_{m}',
            }
        return {
            'sbas_shp': f'coreg_stack_mask_deb_sbas_shp_{m}',
            'ps_shp': f'coreg_stack_mask_deb_ps_shp_{m}',
            'sbas_msk': f'coreg_stack_mask_deb_sbas_shp_msk_{m}',
            'ps_msk': f'coreg_stack_mask_deb_ps_shp_msk_{m}',
        }
    
    def _stamps_coreg_import_vector_specs(self, merged_tree, merge_dir, swath_root, has_subset):
        m = self.master_date
        if merged_tree:
            if has_subset:
                return (
                    os.path.join(merge_dir, f'coreg_deb_mrg_subset_{m}'),
                    '_Stack_esd_mask_deb_mrg_subset.dim',
                    'sbas_shp',
                )
            return (
                os.path.join(merge_dir, f'coreg_deb_mrg_{m}'),
                '_Stack_esd_mask_deb_mrg.dim',
                'sbas_shp',
            )
        if has_subset:
            return (
                os.path.join(swath_root, f'coreg_stack_mask_deb_subset_{m}'),
                '_Stack_esd_mask_deb_subset.dim',
                'sbas_shp',
            )
        return (
            os.path.join(swath_root, f'coreg_stack_mask_deb_{m}'),
            '_Stack_esd_mask_deb.dim',
            'sbas_shp',
        )

    def _stamps_multilook_coreg_input_specs(self, merged_tree, merge_dir, swath_root, has_subset):
        """Return (src_dir, src_suffix) for Step 26 Multilook coreg (after vector mask when set)."""
        m = self.master_date
        names = self._stamps_coreg_mask_folder_names(merged_tree)
        if self.get_msk_shp_path():
            if merged_tree:
                return (
                    os.path.join(merge_dir, names['sbas_msk']),
                    self.STAMPS_COREG_VEC_MSK_DIM,
                )
            return (
                os.path.join(swath_root, names['sbas_msk']),
                self.STAMPS_COREG_VEC_MSK_DIM,
            )
        if merged_tree:
            if has_subset:
                return (
                    os.path.join(merge_dir, f'coreg_deb_mrg_subset_{m}'),
                    '_Stack_esd_mask_deb_mrg_subset.dim',
                )
            return (
                os.path.join(merge_dir, f'coreg_deb_mrg_{m}'),
                '_Stack_esd_mask_deb_mrg.dim',
            )
        if has_subset:
            return (
                os.path.join(swath_root, f'coreg_stack_mask_deb_subset_{m}'),
                '_Stack_esd_mask_deb_subset.dim',
            )
        return (
            os.path.join(swath_root, f'coreg_stack_mask_deb_{m}'),
            '_Stack_esd_mask_deb.dim',
        )

    def _resolve_stamps_step27_sources(self, swath_root, merge_dir, is_multi_swath, has_subset):
        """Pick ``-Pcoreg_file`` / ``-Pintf_file`` trees for Step 27 from prior step outputs."""
        m = self.master_date
        names = self._stamps_coreg_mask_folder_names(is_multi_swath)
        has_shp = bool(self.get_msk_shp_path())
        range_ml = self._range_looks_configured()

        if is_multi_swath:
            bandmath_ml = os.path.join(
                merge_dir, f'multi_intf_deb_mrg_ml_bandmath_sbas_{m}')
            bandmath = os.path.join(
                merge_dir, f'multi_intf_deb_mrg_bandmath_sbas_{m}')
            coreg_ml = os.path.join(merge_dir, f'coreg_deb_mrg_ml_{m}')
            coreg_subset = os.path.join(merge_dir, f'coreg_deb_mrg_subset_{m}')
            coreg_deb = os.path.join(merge_dir, f'coreg_deb_mrg_{m}')
            intf_ps = os.path.join(merge_dir, f'multi_intf_deb_mrg_bandmath_ps_{m}')
            coreg_ml_suf = '_coreg_mrg_ml.dim'
            coreg_subset_suf = '_Stack_esd_mask_deb_mrg_subset.dim'
            coreg_deb_suf = '_Stack_esd_mask_deb_mrg.dim'
        else:
            bandmath_ml = os.path.join(
                swath_root, f'multi_intf_deb_ml_bandmath_sbas_{m}')
            bandmath = os.path.join(
                swath_root, f'multi_intf_deb_bandmath_sbas_{m}')
            coreg_ml = os.path.join(swath_root, f'coreg_stack_mask_deb_ml_{m}')
            coreg_subset = os.path.join(
                swath_root, f'coreg_stack_mask_deb_subset_{m}')
            coreg_deb = os.path.join(swath_root, f'coreg_stack_mask_deb_{m}')
            intf_ps = os.path.join(swath_root, f'multi_intf_deb_bandmath_ps_{m}')
            coreg_ml_suf = '_coreg_ml.dim'
            coreg_subset_suf = '_Stack_esd_mask_deb_subset.dim'
            coreg_deb_suf = '_Stack_esd_mask_deb.dim'

        if self.insar_method == 2:
            intf_dir = intf_ps
            intf_suffix = '_bandmath.dim'
            intf_desc = 'Step 13 (PS bandmath)'
            if has_shp:
                coreg_dir = os.path.join(
                    merge_dir if is_multi_swath else swath_root, names['sbas_msk'])
                coreg_suffix = self.STAMPS_COREG_VEC_MSK_DIM
                coreg_desc = 'Step 25 Land-Sea-Mask coreg (full resolution)'
            elif has_subset:
                coreg_dir = coreg_subset
                coreg_suffix = coreg_subset_suf
                coreg_desc = 'Step 16 subset coreg'
            else:
                coreg_dir = coreg_deb
                coreg_suffix = coreg_deb_suf
                coreg_desc = 'Step 03 deburst coreg'
            return coreg_dir, coreg_suffix, intf_dir, intf_suffix, coreg_desc, intf_desc

        if range_ml:
            intf_dir = bandmath_ml
            intf_suffix = '_bandmath_ml.dim'
            intf_desc = 'Step 14 multilooked bandmath'
            coreg_dir = coreg_ml
            coreg_suffix = coreg_ml_suf
            coreg_desc = 'Step 26 multilooked coreg'
        else:
            intf_dir = bandmath
            intf_suffix = '_bandmath.dim'
            intf_desc = 'Step 12 bandmath (no multilook)'
            if has_shp:
                coreg_dir = os.path.join(
                    merge_dir if is_multi_swath else swath_root, names['sbas_msk'])
                coreg_suffix = self.STAMPS_COREG_VEC_MSK_DIM
                coreg_desc = 'Step 25 Land-Sea-Mask coreg (full resolution)'
            elif has_subset:
                coreg_dir = coreg_subset
                coreg_suffix = coreg_subset_suf
                coreg_desc = 'Step 16 subset coreg'
            else:
                coreg_dir = coreg_deb
                coreg_suffix = coreg_deb_suf
                coreg_desc = 'Step 03 deburst coreg'
        return coreg_dir, coreg_suffix, intf_dir, intf_suffix, coreg_desc, intf_desc
    
    STAMPS_COREG_VEC_SHP_DIM = '_coreg_stack_mask_deb_shp.dim'
    STAMPS_COREG_VEC_MSK_DIM = '_coreg_stack_mask_deb_shp_msk.dim'
    
    
    def step25_import_vector_coreg_stamps(self):
        """Import AOI vector onto coreg stacks (Step 17) before Land-Sea-Mask / multilook."""
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 17: Import-Vector Coreg (AOI mask) - SKIPPED")
            print(f"{'='*80}")
            print(f"  Only used for StaMPS (insar_target=2).\n")
            return True
        
        vec_path = self.get_msk_shp_path()
        if not vec_path:
            print(f"\n{'='*80}")
            print(f"Starting Step 17: Import-Vector AOI Coreg (optional) - SKIPPED")
            print(f"{'='*80}")
            print(f"STEP 17: Import-Vector Coreg (AOI mask) - SKIPPED")
            print(f"  No msk_shp / shp_mask in configuration.\n")
            return True
        
        if not os.path.isfile(vec_path):
            print(f"\n{'='*80}")
            print(f"ERROR: AOI vector file not found:\n  {vec_path}")
            print(f"{'='*80}\n")
            return False
        
        print(f"\n{'='*80}")
        print(f"STEP 17: Import-Vector Coreg (AOI mask)")
        print(f"{'='*80}")
        print(f"  Vector: {vec_path}")
        
        swath_config = self.get_swath_config_value()
        merged = swath_config not in [1, 2, 3]
        aoi_region = self.config.get('clip_roi', '')
        has_subset = bool(aoi_region.strip()) if aoi_region else False
        
        if merged:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            return self._step25_import_vector_coreg_merged(merge_dir, merge_folder, vec_path, has_subset)
        
        swath_name = self.swaths_to_process[0]
        swath_root = os.path.join(self.proj_root, swath_name)
        return self._step25_import_vector_coreg_single_swath(swath_root, vec_path, has_subset)
    
    def _step25_import_vector_coreg_single_swath(self, swath_root, vec_path, has_subset):
        names = self._stamps_coreg_mask_folder_names(False)
        src_dir, src_suf, key = self._stamps_coreg_import_vector_specs(
            False, None, swath_root, has_subset)
        out_dir = os.path.join(swath_root, names[key])
        os.makedirs(out_dir, exist_ok=True)
        
        if not os.path.isdir(src_dir):
            print(f"  ✗ Coreg source directory not found: {src_dir}")
            return False
        
        pairs = self.list_pair_dirs_check_only(src_dir)
        pairs = [p for p in pairs if not self._skip_pair_folder_for_current_mode(p)]
        if not pairs:
            print(f"  ✗ No pair folders under {src_dir}")
            return False
        
        cmds = []
        for pair_name in pairs:
            src = os.path.join(src_dir, pair_name, f"{pair_name}{src_suf}")
            dst = os.path.join(out_dir, pair_name,
                               f"{pair_name}{self.STAMPS_COREG_VEC_SHP_DIM}")
            if not os.path.isfile(src):
                print(f"  ⚠ Skipping {pair_name}: missing coreg stack {src}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if self.check_output_file_valid(dst):
                continue
            cmds.append((
                [
                    *self.gpt_base_cmd(),
                    'Import-Vector',
                    f'-Ssource={src}',
                    f'-PvectorFile={vec_path}',
                    '-t', dst,
                ],
                f"Import-Vector coreg {pair_name}",
                pair_name,
                dst,
            ))
        if not cmds:
            print(f"  All Import-Vector coreg products already exist.\n")
            return True
        succ, fail = self.run_commands_parallel(cmds, "Step 17 - Import-Vector Coreg AOI")
        if fail:
            return False
        print(f"\n  ✓ Step 17 complete\n")
        return True
    
    def _step25_import_vector_coreg_merged(self, merge_dir, merge_folder, vec_path, has_subset):
        names = self._stamps_coreg_mask_folder_names(True)
        src_dir, src_suf, key = self._stamps_coreg_import_vector_specs(
            True, merge_dir, None, has_subset)
        out_dir = os.path.join(merge_dir, names[key])
        os.makedirs(out_dir, exist_ok=True)
        
        if not os.path.isdir(src_dir):
            print(f"  ✗ Coreg source directory not found: {src_dir}")
            return False
        
        pairs = self.list_pair_dirs_check_only(src_dir)
        pairs = [p for p in pairs if not self._skip_pair_folder_for_current_mode(p)]
        if not pairs:
            print(f"  ✗ No pair folders under {src_dir}")
            return False
        
        cmds = []
        for pair_name in pairs:
            src = os.path.join(src_dir, pair_name, f"{pair_name}{src_suf}")
            dst = os.path.join(out_dir, pair_name,
                               f"{pair_name}{self.STAMPS_COREG_VEC_SHP_DIM}")
            if not os.path.isfile(src):
                print(f"  ⚠ Skipping {pair_name}: missing coreg stack {src}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if self.check_output_file_valid(dst):
                continue
            cmds.append((
                [
                    *self.gpt_base_cmd(),
                    'Import-Vector',
                    f'-Ssource={src}',
                    f'-PvectorFile={vec_path}',
                    '-t', dst,
                ],
                f"Import-Vector coreg {pair_name}",
                pair_name,
                dst,
            ))
        if not cmds:
            print(f"  All merged Import-Vector coreg products already exist.\n")
            return True
        succ, fail = self.run_commands_parallel(
            cmds, f"Step 17 - Import-Vector Coreg AOI ({merge_folder})")
        if fail:
            return False
        print(f"\n  ✓ Step 17 complete (multi-swath)\n")
        return True
    
    
    def step26_landsea_mask_coreg_stamps(self):
        """Apply Land-Sea-Mask geometry to coreg stacks from Step 17 (when ``msk_shp`` is set)."""
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 25: Land-Sea-Mask Coreg (AOI geometry) - SKIPPED")
            print(f"{'='*80}")
            print(f"  Only used for StaMPS (insar_target=2).\n")
            return True
        
        if not self.get_msk_shp_path():
            print(f"\n{'='*80}")
            print(f"STEP 25: Land-Sea-Mask Coreg (AOI geometry) - SKIPPED")
            print(f"{'='*80}")
            print(f"  No msk_shp / shp_mask in configuration.\n")
            return True
        
        geometry_name = self.geometry_name_from_msk_shp()
        invert_geom = self.config_invert_mask()
        names = self._stamps_coreg_mask_folder_names(
            self.get_swath_config_value() not in [1, 2, 3])
        shp_key = 'sbas_shp'
        msk_key = 'sbas_msk'
        
        print(f"\n{'='*80}")
        print(f"STEP 25: Land-Sea-Mask Coreg (AOI geometry)")
        print(f"{'='*80}")
        print(f"  geometry={geometry_name}, landMask=false, invertGeometry={invert_geom}")
        
        swath_config = self.get_swath_config_value()
        merged = swath_config not in [1, 2, 3]
        
        if merged:
            merge_folder = self.get_merge_folder_name(swath_config)
            merge_dir = os.path.join(self.proj_root, merge_folder)
            shp_base = os.path.join(merge_dir, names[shp_key])
            mask_base = os.path.join(merge_dir, names[msk_key])
            ctx = merge_folder
        else:
            swath_name = self.swaths_to_process[0]
            sw_root = os.path.join(self.proj_root, swath_name)
            shp_base = os.path.join(sw_root, names[shp_key])
            mask_base = os.path.join(sw_root, names[msk_key])
            ctx = swath_name
        
        if not os.path.isdir(shp_base):
            print(f"  ✗ Step 17 output folder not found: {shp_base}")
            return False
        
        pairs = self.list_pair_dirs_check_only(shp_base)
        pairs = [p for p in pairs if not self._skip_pair_folder_for_current_mode(p)]
        if not pairs:
            print(f"  ✗ No pairs under {shp_base}")
            return False
        
        probe_dim = os.path.join(
            shp_base, pairs[0], f"{pairs[0]}{self.STAMPS_COREG_VEC_SHP_DIM}")
        self._validate_geom_name_in_import_dim_xml(probe_dim, geometry_name)
        
        os.makedirs(mask_base, exist_ok=True)
        cmds = []
        for pair_name in pairs:
            src = os.path.join(
                shp_base, pair_name, f"{pair_name}{self.STAMPS_COREG_VEC_SHP_DIM}")
            dst = os.path.join(
                mask_base, pair_name, f"{pair_name}{self.STAMPS_COREG_VEC_MSK_DIM}")
            if not os.path.isfile(src):
                print(f"  ⚠ Skipping {pair_name}: missing shp-coreg {src}")
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if self.check_output_file_valid(dst):
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
                '-t', dst,
            ])
            cmds.append((cmd, f"Land-Sea-Mask coreg {pair_name}", pair_name, dst))
        
        if not cmds:
            print(f"  All Land-Sea-Mask coreg products already exist.\n")
            return True
        succ, fail = self.run_commands_parallel(cmds, f"Step 25 - Land-Sea-Mask Coreg ({ctx})")
        if fail:
            return False
        print(f"\n  ✓ Step 25 complete\n")
        return True
    
    
    def _handle_stamps_sbas_export_failure(
            self, pair_name, coreg_dim, intf_dim, coreg_dir, intf_dir):
        print(
            f"  ✗ Export for {pair_name} produced no diff0/rslc under "
            f"the StaMPS temp/output folder"
        )
        gpt_blob = self._log_last_gpt_output_hint()

        match, (cw, ch), (iw, ih) = self._compare_stamps_export_dimensions(
            coreg_dim, intf_dim)
        gpt_dim_error = self._last_gpt_mentions_dimension_mismatch() or (
            gpt_blob and 'compatible dimensions' in gpt_blob.lower())

        print(f"\n{'='*80}")
        print("STEP 27 STOPPED — StaMPS SBAS export failed")
        print(f"{'='*80}")
        print("  Compare Intensity-band dimensions in SNAP for:")
        print(f"    Coreg: {coreg_dim}")
        print(f"    Intf:  {intf_dim}")
        if cw and ch and iw and ih:
            print(f"    Coreg size: {cw} x {ch} (width x height)")
            print(f"    Intf  size: {iw} x {ih} (width x height)")
            if match is False:
                print("    ✗ Dimension mismatch between coreg and interferogram products")
            elif match is True:
                print("    ✓ Dimensions match — failure may be due to another GPT issue")
        else:
            print("    ⚠ Could not read dimensions from one or both .dim files")

        if match is False or gpt_dim_error:
            print(f"\n  Dimension mismatch detected — multilooked folders were NOT deleted.")
            print(f"  After you confirm the mismatch in SNAP (or test different Range values),")
            print(f"  manually remove these folders before re-running Steps 14, 26, and 27:")
            for folder in (coreg_dir, intf_dir):
                if os.path.isdir(folder):
                    print(f"    - {folder}")
            print(f"\n  ACTION REQUIRED:")
            print(f"    1. In SNAP, open the coreg and intf .dim files above and compare")
            print(f"       Intensity-band dimensions (Product Explorer → Properties).")
            print(f"    2. Test Multilook on one pair with different Range values until both")
            print(f"       products share the same final dimensions.")
            print(f"    3. Set that Range value in your config file.")
            print(f"    4. Manually delete the folders listed above.")
            print(f"    5. Re-run Step 14 (bandmath multilook) and Step 26 (coreg multilook).")
            print(f"    6. Then re-run Step 27.")
        else:
            print(f"\n  Check GPT output above, fix the issue, then re-run Step 27.")
        print(f"{'='*80}\n")
        return False
    
    
    def step22_stamps_export(self):
        if self.insar_target != 2:
            print(f"\n{'='*80}")
            print(f"STEP 27: StaMPS Export - SKIPPED")
            print(f"{'='*80}")
            print(f"  insar_target={self.insar_target} (StaMPS export requires insar_target=2)\n")
            return True
        
        print(f"\n{'='*80}")
        print(f"STEP 27: StaMPS Export (Method={self.insar_method})")
        print(f"{'='*80}")
        
        if not self.sbas_pairs:
            if not self.load_sbas_pairs():
                print(f"  ✗ Failed to load SBAS pairs")
                return False
        
        swath_config = self.get_swath_config_value()
        is_multi_swath = swath_config not in [1, 2, 3]
        
        if is_multi_swath:
            return self._step19_stamps_export_multi_swath()
        else:
            return self._step19_stamps_export_single_swath()
    
    def _step19_stamps_export_single_swath(self):
        """StaMPS export for single swath."""
        swath_name = self.swaths_to_process[0]
        swath_root = os.path.join(self.proj_root, swath_name)

        aoi_region = self.config.get('clip_roi', '')
        has_subset = bool(aoi_region)

        print(f"  Processing {swath_name}")
        print(f"  Clip ROI: {'Yes' if has_subset else 'No'}")
        print(f"  Method: {'SBAS' if self.insar_method == 1 else 'PS'}")
        print(f"  Range in config: {'Yes' if self._range_looks_configured() else 'No (full-res coreg/intf for SBAS)'}")

        (coreg_final_dir, coreg_final_suffix,
         intf_final_dir, intf_final_suffix,
         coreg_desc, intf_desc) = self._resolve_stamps_step27_sources(
            swath_root, None, False, has_subset)

        if not os.path.exists(coreg_final_dir):
            print(f"  ✗ Coreg directory not found: {coreg_final_dir}")
            return False
        if not os.path.exists(intf_final_dir):
            print(f"  ✗ Interferogram directory not found: {intf_final_dir}")
            return False

        pairs = self.list_pair_dirs_check_only(coreg_final_dir)
        if not pairs:
            print(f"  ✗ No pairs found in {coreg_final_dir}")
            return False

        print(f"  Processing {len(pairs)} pairs")
        print(f"  Coreg source: {coreg_desc} ({os.path.basename(coreg_final_dir)}/)")
        print(f"  Intf source: {intf_desc} ({os.path.basename(intf_final_dir)}/)")

        print(f"\n  Step 20.1: Running sbas_export.xml...")
        stamps_root = self.get_stamps_root()
        insar_root = self.get_insar_root(stamps_root)
        os.makedirs(stamps_root, exist_ok=True)

        if self.insar_method == 1:
            self._prime_stamps_sbas_bridge_tracking(stamps_root)

        export_graph = self.get_graph_file('sbas_export.xml')

        total_units = len(pairs)
        if total_units > 0:
            self._release_progress_begin(total_units)

        for pair_name in pairs:
            coreg_dim = os.path.join(coreg_final_dir, pair_name, f"{pair_name}{coreg_final_suffix}")
            intf_dim = os.path.join(intf_final_dir, pair_name, f"{pair_name}{intf_final_suffix}")

            if not os.path.exists(coreg_dim):
                print(f"  ⚠ Skipping {pair_name}: coreg not found")
                self._release_progress_advance(1)
                continue
            if not os.path.exists(intf_dim):
                print(f"  ⚠ Skipping {pair_name}: bandmath not found")
                self._release_progress_advance(1)
                continue

            target_dates = None
            if self.insar_method == 2:
                target_dates = self.get_target_dates_from_bandmath(intf_final_dir, pair_name)
                if not target_dates:
                    print(f"  ⚠ Skipping {pair_name}: no target dates found in bandmath")
                    self._release_progress_advance(1)
                    continue

            pair_output_dir = self.get_stamps_pair_dir(stamps_root, pair_name)
            if self.insar_method == 1:
                if self.stamps_pair_outputs_exist(pair_output_dir, pair_name, self.insar_method):
                    print(f"  Skipping {pair_name} (output exists)")
                    self._release_progress_advance(1)
                    continue
                temp_dir = os.path.join(insar_root, 'temp')
                os.makedirs(temp_dir, exist_ok=True)
                output_dir = temp_dir
            else:
                output_dir = insar_root
                os.makedirs(output_dir, exist_ok=True)
                if self.stamps_pair_outputs_exist(output_dir, pair_name, self.insar_method, target_dates):
                    target_dates_str = ', '.join(target_dates)
                    print(f"  Skipping {pair_name} (output exists for targets: {target_dates_str})")
                    self._release_progress_advance(1)
                    continue

            cmd = [
                *self.gpt_base_cmd(),
                export_graph,
                f"-Pcoreg_file={coreg_dim}",
                f"-Pintf_file={intf_dim}",
                f"-Poutput_dir_stamps={output_dir}"
            ]

            success = self.run_command(cmd, f"StaMPS export {pair_name}")
            if not success:
                if self.insar_method == 1:
                    return self._handle_stamps_sbas_export_failure(
                        pair_name, coreg_dim, intf_dim, coreg_final_dir, intf_final_dir)
                print(f"  ✗ Export failed for {pair_name}")
                self._release_progress_advance(1)
                continue

            if not self._validate_stamps_export_output(
                    output_dir, pair_name, self.insar_method, target_dates):
                if self.insar_method == 1:
                    return self._handle_stamps_sbas_export_failure(
                        pair_name, coreg_dim, intf_dim, coreg_final_dir, intf_final_dir)
                print(
                    f"  ✗ Export for {pair_name} produced no diff0/rslc under "
                    f"{output_dir}"
                )
                self._log_last_gpt_output_hint()
                self._release_progress_advance(1)
                continue

            if self.insar_method == 1:
                is_master_pair = pair_name.startswith(f"{self.master_date}_")
                if not self.rearrange_stamps_output(
                        temp_dir, insar_root, pair_output_dir, pair_name, is_master_pair):
                    print(f"  ✗ Post-process failed for {pair_name}")

            self._release_progress_advance(1)

        if self.insar_method == 2:
            if not os.path.isdir(os.path.join(insar_root, 'diff0')):
                print(f"  ✗ PS export: no diff0/ under {insar_root}")
            else:
                self._finalize_stamps_ps_geo(insar_root)

        print(f"\n  ✓ Step 27 complete for single swath\n")
        return True
    
    def _step19_stamps_export_multi_swath(self):
        """StaMPS export for multi-swath."""
        swath_config = self.get_swath_config_value()

        if swath_config == 0:
            swaths = ['IW1', 'IW2', 'IW3']
        elif swath_config == 12:
            swaths = ['IW1', 'IW2']
        elif swath_config == 23:
            swaths = ['IW2', 'IW3']
        else:
            print(f"  ✗ Invalid swath config: {swath_config}")
            return False

        merge_folder = self.get_merge_folder_name(swath_config)
        merge_dir = os.path.join(self.proj_root, merge_folder)

        aoi_region = self.config.get('clip_roi', '')
        has_subset = bool(aoi_region)

        print(f"  Merging swaths: {', '.join(swaths)}")
        print(f"  Clip ROI: {'Yes' if has_subset else 'No'}")
        print(f"  Method: {'SBAS' if self.insar_method == 1 else 'PS'}")
        print(f"  Range in config: {'Yes' if self._range_looks_configured() else 'No (full-res coreg/intf for SBAS)'}")

        swath_root = os.path.join(self.proj_root, swaths[0])
        (coreg_final_dir, coreg_final_suffix,
         intf_final_dir, intf_final_suffix,
         coreg_desc, intf_desc) = self._resolve_stamps_step27_sources(
            swath_root, merge_dir, True, has_subset)

        if not os.path.exists(coreg_final_dir):
            print(f"  ✗ Coreg directory not found: {coreg_final_dir}")
            return False
        if not os.path.exists(intf_final_dir):
            print(f"  ✗ Interferogram directory not found: {intf_final_dir}")
            return False

        pairs = self.list_pair_dirs_check_only(coreg_final_dir)
        if not pairs:
            print(f"  ✗ No pairs found in {coreg_final_dir}")
            return False

        print(f"  Processing {len(pairs)} pairs")
        print(f"  Coreg source: {coreg_desc} ({merge_folder}/{os.path.basename(coreg_final_dir)}/)")
        print(f"  Intf source: {intf_desc} ({merge_folder}/{os.path.basename(intf_final_dir)}/)")

        print(f"\n  Step 14.5: Running sbas_export.xml...")
        stamps_root = self.get_stamps_root()
        insar_root = self.get_insar_root(stamps_root)
        os.makedirs(stamps_root, exist_ok=True)

        if self.insar_method == 1:
            self._prime_stamps_sbas_bridge_tracking(stamps_root)

        export_graph = self.get_graph_file('sbas_export.xml')

        total_units = len(pairs)
        if total_units > 0:
            self._release_progress_begin(total_units)

        for pair_name in pairs:
            coreg_dim = os.path.join(coreg_final_dir, pair_name, f"{pair_name}{coreg_final_suffix}")
            intf_dim = os.path.join(intf_final_dir, pair_name, f"{pair_name}{intf_final_suffix}")

            if not os.path.exists(coreg_dim):
                print(f"  ⚠ Skipping {pair_name}: coreg not found")
                self._release_progress_advance(1)
                continue
            if not os.path.exists(intf_dim):
                print(f"  ⚠ Skipping {pair_name}: bandmath not found")
                self._release_progress_advance(1)
                continue

            target_dates = None
            if self.insar_method == 2:
                target_dates = self.get_target_dates_from_bandmath(intf_final_dir, pair_name)
                if not target_dates:
                    print(f"  ⚠ Skipping {pair_name}: no target dates found in bandmath")
                    self._release_progress_advance(1)
                    continue

            pair_output_dir = self.get_stamps_pair_dir(stamps_root, pair_name)
            if self.insar_method == 1:
                if self.stamps_pair_outputs_exist(pair_output_dir, pair_name, self.insar_method):
                    print(f"  Skipping {pair_name} (output exists)")
                    self._release_progress_advance(1)
                    continue
                temp_dir = os.path.join(insar_root, 'temp')
                os.makedirs(temp_dir, exist_ok=True)
                output_dir = temp_dir
            else:
                output_dir = insar_root
                os.makedirs(output_dir, exist_ok=True)
                if self.stamps_pair_outputs_exist(output_dir, pair_name, self.insar_method, target_dates):
                    target_dates_str = ', '.join(target_dates)
                    print(f"  Skipping {pair_name} (output exists for targets: {target_dates_str})")
                    self._release_progress_advance(1)
                    continue

            cmd = [
                *self.gpt_base_cmd(),
                export_graph,
                f"-Pcoreg_file={coreg_dim}",
                f"-Pintf_file={intf_dim}",
                f"-Poutput_dir_stamps={output_dir}"
            ]

            success = self.run_command(cmd, f"StaMPS export {pair_name}")
            if not success:
                if self.insar_method == 1:
                    return self._handle_stamps_sbas_export_failure(
                        pair_name, coreg_dim, intf_dim, coreg_final_dir, intf_final_dir)
                print(f"  ✗ Export failed for {pair_name}")
                self._release_progress_advance(1)
                continue

            if not self._validate_stamps_export_output(
                    output_dir, pair_name, self.insar_method, target_dates):
                if self.insar_method == 1:
                    return self._handle_stamps_sbas_export_failure(
                        pair_name, coreg_dim, intf_dim, coreg_final_dir, intf_final_dir)
                print(
                    f"  ✗ Export for {pair_name} produced no diff0/rslc under "
                    f"{output_dir}"
                )
                self._log_last_gpt_output_hint()
                self._release_progress_advance(1)
                continue

            if self.insar_method == 1:
                is_master_pair = pair_name.startswith(f"{self.master_date}_")
                if not self.rearrange_stamps_output(
                        temp_dir, insar_root, pair_output_dir, pair_name, is_master_pair):
                    print(f"  ✗ Post-process failed for {pair_name}")

            self._release_progress_advance(1)

        if self.insar_method == 2:
            if not os.path.isdir(os.path.join(insar_root, 'diff0')):
                print(f"  ✗ PS export: no diff0/ under {insar_root}")
            else:
                self._finalize_stamps_ps_geo(insar_root)

        print(f"\n  ✓ Step 27 complete for multi-swath\n")
        return True
    
    
    def _parse_batch_wall_start(self):
        raw = str(self.config.get('batch_wall_start', '')).strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    
    def _init_batch_wall_start(self, force_reset=False):
        """Load or create the batch wall-clock anchor (stored in config when persisting)."""
        if not force_reset:
            existing = self._parse_batch_wall_start()
            if existing is not None:
                self._batch_wall_start = existing
                return existing
        self._batch_wall_start = datetime.now()
        self.update_config_file(
            'batch_wall_start', self._batch_wall_start.isoformat(timespec='seconds'))
        return self._batch_wall_start
    
    def _clear_batch_wall_start(self):
        self._batch_wall_start = None
        self.update_config_file('batch_wall_start', '')
        self.config['batch_wall_start'] = ''
    
    def _batch_wall_elapsed_sec(self):
        if self._batch_wall_start is None:
            return 0.0
        return max(0.0, (datetime.now() - self._batch_wall_start).total_seconds())
    
    @staticmethod
    def _format_elapsed_min_sec(seconds):
        """Return (minutes, seconds) for ``Xm Ys`` log lines (MATLAB-style)."""
        seconds = max(0.0, float(seconds))
        return int(seconds // 60), seconds % 60.0
    
    @staticmethod
    def _format_elapsed_hms(seconds):
        """Return (hours, minutes, seconds) for final batch summary."""
        seconds = max(0.0, float(seconds))
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60.0
        return hours, minutes, secs
    
    def _log_step_start_timestamp(self, step_num, step_name=None):
        ts = datetime.now().strftime('%H:%M:%S')
        if self.savelog == 'release':
            title = step_name or f"Step {step_num}"
            print(f"\n[{ts}] ")
            print("=" * 80)
            print(f"STEP {step_num:02d}: {title}")
            print("=" * 80)
        elif step_name:
            print(f"\n[{ts}] Starting Step {step_num}: {step_name}...")
        else:
            print(f"\n[{ts}] Starting Step {step_num}...")
    
    def _log_step_timing_single(self, step_num, elapsed_sec):
        """Single-step run: log only this step's duration."""
        minutes, secs = self._format_elapsed_min_sec(elapsed_sec)
        print(f"Step {step_num:02d} finished in {minutes}m {secs:.0f}s")
    
    def _log_step_timing_batch(self, step_num, step_elapsed_sec, total_elapsed_sec):
        """Batch run: log step duration and cumulative elapsed time."""
        minutes, secs = self._format_elapsed_min_sec(step_elapsed_sec)
        print(f"Step {step_num:02d} finished in {minutes}m {secs:.0f}s")
        t_min, t_sec = self._format_elapsed_min_sec(total_elapsed_sec)
        print(f"Total time execution so far: {t_min}m {t_sec:.0f}s")
    
