"""SBAS pairs, baselines, bridging"""
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


class PairsMixin:

    def _skip_pair_folder_for_current_mode(self, pair_name):
        parts = pair_name.split('_')
        if len(parts) != 2:
            return False
        return self._skip_pair_step03_rule(parts[0], parts[1])
    
    def _dates_linked_to_master(self, all_pairs):
        master = self.master_date
        if not master:
            return set()
        linked = set()
        for p in all_pairs:
            d1, d2 = p['date1'], p['date2']
            if d1 == master:
                linked.add(d2)
            elif d2 == master:
                linked.add(d1)
        return linked

    def _filter_check_only_candidate_pairs(self, all_pairs):
        master = self.master_date
        if not master:
            return list(all_pairs)
        linked = self._dates_linked_to_master(all_pairs)
        filtered = []
        for p in all_pairs:
            d1, d2 = p['date1'], p['date2']
            if self._pair_contains_master(p, master):
                continue
            if d1 in linked or d2 in linked:
                continue
            filtered.append(p)
        return filtered

    def _select_check_only_pairs_from_list(self, all_pairs):
        master = self.master_date
        if not master or not all_pairs:
            return None

        filtered = self._filter_check_only_candidate_pairs(all_pairs)
        n_excl_master = sum(
            1 for p in all_pairs if self._pair_contains_master(p, master))
        linked = self._dates_linked_to_master(all_pairs)
        n_excl_linked = sum(
            1 for p in all_pairs
            if not self._pair_contains_master(p, master)
            and (p['date1'] in linked or p['date2'] in linked))

        if not filtered:
            print(
                f"\n{'─'*80}\n"
                f"  ⚠ check_only: no SBAS pairs left after excluding master {master} "
                f"and master-linked dates {sorted(linked)}.\n"
                f"  check_only requires at least 3 pairs in sbas_pairs.txt after filtering.\n"
                f"  Add more pairs to sbas_pairs.txt (dates not linked to master) and re-run.\n"
                f"{'─'*80}\n"
            )
            return None

        n = len(filtered)
        if n < 3:
            print(
                f"\n{'─'*80}\n"
                f"  ⚠ check_only: only {n} pair(s) remain after filtering "
                f"({n_excl_master} with master, {n_excl_linked} master-linked); need ≥ 3.\n"
                f"  Filtered candidates:"
            )
            for p in filtered:
                print(f"    • {p['date1']} {p['date2']}")
            print(
                f"\n  Add more pairs to sbas_pairs.txt (no master; no dates linked to master "
                f"{master}) and re-run check_only.\n"
                f"{'─'*80}\n"
            )
            return None

        if n % 2 == 0:
            center_i = n // 2 - 1
        else:
            center_i = n // 2
        pick_indices = []
        for i in (0, center_i, n - 1):
            if i not in pick_indices:
                pick_indices.append(i)
        for i in range(n):
            if len(pick_indices) >= 3:
                break
            if i not in pick_indices:
                pick_indices.append(i)

        picked = [filtered[i] for i in pick_indices[:3]]
        print(f"  check_only pair filter: {len(all_pairs)} total → "
              f"{len(all_pairs) - n_excl_master - n_excl_linked} after exclusions "
              f"({n_excl_master} with master, {n_excl_linked} master-linked dates)")
        print(f"  check_only picks (first / center / last of {n} filtered):")
        for p in picked:
            print(f"    • {p['date1']} {p['date2']}")
        return picked
    
    def read_sbas_pair_rows(self):
        sbas_pairs_file = os.path.join(self.proj_root, 'metadata_info', 'sbas_pairs.txt')
        if not os.path.exists(sbas_pairs_file):
            raise FileNotFoundError(f"SBAS pairs file not found: {sbas_pairs_file}")
        rows = []
        with open(sbas_pairs_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                date1_raw = parts[0]
                date2_raw = parts[1]
                temp_diff = float(parts[2]) if len(parts) > 2 else 0
                baseline_diff = float(parts[3]) if len(parts) > 3 else 0
                if date1_raw <= date2_raw:
                    date1 = date1_raw
                    date2 = date2_raw
                else:
                    date1 = date2_raw
                    date2 = date1_raw
                rows.append({
                    'date1': date1,
                    'date2': date2,
                    'temp_diff': temp_diff,
                    'baseline_diff': baseline_diff,
                    'pair_name': f"{date1}_{date2}",
                })
        return rows
    
    def load_sbas_pairs(self):
        sbas_pairs_file = os.path.join(self.proj_root, 'metadata_info', 'sbas_pairs.txt')
        
        if not os.path.exists(sbas_pairs_file):
            raise FileNotFoundError(f"SBAS pairs file not found: {sbas_pairs_file}")
        
        print(f"Loading SBAS pairs from: {sbas_pairs_file}")
        
        self.sbas_pairs = []
        self.master_second_pairs = []
        
        rows = self.read_sbas_pair_rows()
        for row in rows:
            date1 = row['date1']
            date2 = row['date2']
            self.sbas_pairs.append({
                'date1': date1,
                'date2': date2,
                'temp_diff': row['temp_diff'],
                'baseline_diff': row['baseline_diff'],
            })
            if date2 == self.master_date:
                self.master_second_pairs.append({
                    'date1': date1,
                    'date2': date2,
                    'processed': False,
                })
        
        n_file = len(self.sbas_pairs)
        ms_file = list(self.master_second_pairs)

        if self.is_check_only():
            sub = self._select_check_only_pairs_from_list(self.sbas_pairs)
            if sub:
                self.sbas_pairs = sub

                self.master_second_pairs = ms_file
                dates = {self.master_date} if self.master_date else set()
                for p in sub:
                    dates.add(p['date1'])
                    dates.add(p['date2'])
                self.selected_dates = sorted(dates)
                if self.is_ps_mode():
                    self.ps_pair_to_target_dates = {}
                    for p in sub:
                        pn = f"{p['date1']}_{p['date2']}"
                        targets = sorted(
                            {d for d in (p['date1'], p['date2']) if d != self.master_date}
                        )
                        self.ps_pair_to_target_dates[pn] = targets
                    self.ps_selected_pair_names = set(self.ps_pair_to_target_dates)
                    self.sbas_pairs_full = list(self.sbas_pairs)
                print(f"\n{'─'*80}")
                print(f"  check_only=True  —  using {len(self.sbas_pairs)} of {n_file} SBAS pairs (no master-only pairs)")
                for p in self.sbas_pairs:
                    print(f"    {p['date1']} {p['date2']}  {p['temp_diff']}  {p['baseline_diff']}")
                print(f"\n  Prepare Slices (Step 02) restricted to acquisitions: {', '.join(self.selected_dates)}")
                print(f"{'─'*80}\n")
            else:
                print(f"\n{'='*80}")
                print(f"  ✗ check_only=True: cannot run — insufficient pairs after filtering")
                print(f"    Need ≥ 3 pairs in sbas_pairs.txt after master / master-linked exclusions.")
                print(f"{'='*80}\n")
                return False
        elif self.is_ps_mode():
            all_names = [f"{p['date1']}_{p['date2']}" for p in self.sbas_pairs]
            self.ps_pair_to_target_dates, selected_names = self._select_ps_pairs_from_names(all_names)
            self.ps_selected_pair_names = set(selected_names)

            self.sbas_pairs_full = list(self.sbas_pairs)
            selected_set = self.ps_selected_pair_names
            self.sbas_pairs = [
                p for p in self.sbas_pairs
                if f"{p['date1']}_{p['date2']}" in selected_set
            ]
            dates = {self.master_date} if self.master_date else set()
            for pn in selected_names:
                d1, d2 = pn.split('_')
                dates.add(d1)
                dates.add(d2)
            self.selected_dates = sorted(dates)
            self._print_ps_pair_selection_summary(n_file)
        else:
            self.selected_dates = None
        
        print(f"  Loaded {len(self.sbas_pairs)} SBAS pairs (active)")
        print(f"  Master date: {self.master_date}")
        
        if self.master_second_pairs:
            print(f"  Found {len(self.master_second_pairs)} pairs with master as second date")
        
        return True
    
    def convert_date_format(self, date_str):
        dt = datetime.strptime(date_str, '%Y%m%d')
        return dt.strftime('%d%b%Y')
    
    def convert_date_to_conf_format(self, date_str):
        dt = datetime.strptime(date_str, '%Y%m%d')
        return dt.strftime('%d-%b-%Y').upper()
    
    def convert_conf_to_hdr_format(self, conf_date_str):
        return conf_date_str.replace('-', '').title()
    
    def convert_display_to_yyyymmdd(self, display_date_str):
        try:
            dt = datetime.strptime(display_date_str, '%d%b%Y')
            return dt.strftime('%Y%m%d')
        except Exception as e:
            print(f"  ⚠ Error converting date {display_date_str}: {e}")
            return None
    
    def _hdr_candidates_from_conf(self, conf_filename, swath_name):

        conf_pair = conf_filename.replace('snaphu.conf', '')
        parts = conf_pair.split('_')
        if len(parts) != 2:
            return []
        date1_hdr = self.convert_conf_to_hdr_format(parts[0])
        date2_hdr = self.convert_conf_to_hdr_format(parts[1])
        return [
            f"UnwPhase_ifg_{swath_name}_VV_{date1_hdr}_{date2_hdr}.snaphu.hdr",
            f"UnwPhase_ifg_VV_{date1_hdr}_{date2_hdr}.snaphu.hdr",
        ]

    def get_hdr_filename_from_conf(self, conf_filename, swath_name):

        cands = self._hdr_candidates_from_conf(conf_filename, swath_name)
        return cands[0] if cands else None

    def resolve_hdr_filename_from_conf(self, conf_filename, swath_name, search_dir=None):

        for cand in self._hdr_candidates_from_conf(conf_filename, swath_name):
            if search_dir and os.path.isfile(os.path.join(search_dir, cand)):
                return cand
        cands = self._hdr_candidates_from_conf(conf_filename, swath_name)
        return cands[-1] if cands else None
    
    def generate_interferogram_pairs(self, date1, date2, master_date):
        date1_fmt = self.convert_date_format(date1)
        date2_fmt = self.convert_date_format(date2)
        master_fmt = self.convert_date_format(master_date)
        

        if date1 == master_date:
            pair3 = f"{date1_fmt}-{date2_fmt}"
            return pair3
        

        pairs = []
        

        pair1 = f"{master_fmt}-{date1_fmt}"
        

        pair2 = f"{master_fmt}-{date2_fmt}"
        

        pair3 = f"{date1_fmt}-{date2_fmt}"
        

        seen = set()
        for pair in [pair1, pair2, pair3]:

            if pair not in seen:
                pairs.append(pair)
                seen.add(pair)
        
        return ','.join(pairs)

    @staticmethod
    def _reset_multi_intf_bridging_state(list_step05):

        for info in list_step05.values():
            info['used'] = False

    def _consume_bridging_pairs_for_dates(self, list_step05, date1, date2, base_pairs_set=None):
        for bridging_info in list_step05.values():
            if bridging_info['used']:
                continue
            bridging_date1 = bridging_info['date1']
            if date1 != bridging_date1 and date2 != bridging_date1:
                continue
            bridging_date1_fmt = self.convert_date_format(bridging_date1)
            bridging_date2_fmt = self.convert_date_format(bridging_info['date2'])
            bridging_pair_str = f"{bridging_date1_fmt}-{bridging_date2_fmt}"
            if base_pairs_set is not None and bridging_pair_str in base_pairs_set:
                bridging_info['used'] = True
                continue
            bridging_info['used'] = True
            print(
                f"    Bridging pair {bridging_pair_str} marked used "
                f"(prior run / skip {date1}_{date2})"
            )
    
    def add_bridging_pairs(self, date1, date2, master_date, base_pairs):
        bridging_pairs = []
        

        for pair in self.master_second_pairs:

            if pair.get('processed', False):
                continue
            
            tracked_date = pair['date1']
            

            if tracked_date == date1 or tracked_date == date2:

                date_fmt = self.convert_date_format(tracked_date)
                master_fmt = self.convert_date_format(master_date)
                

                bridging_pair = f"{master_fmt}-{date_fmt}"
                

                if bridging_pair not in base_pairs and bridging_pair not in bridging_pairs:
                    bridging_pairs.append(bridging_pair)
                    print(f"    Adding bridging pair: {bridging_pair} (triggered by {tracked_date} in pair {date1}_{date2})")
                    

                    pair['processed'] = True
                else:

                    print(f"    Skipping bridging pair: {bridging_pair} (already in base pairs)")
                    pair['processed'] = True
        
        if bridging_pairs:
            return base_pairs + ',' + ','.join(bridging_pairs)
        
        return base_pairs
    
    def get_swath_data_dir(self, swath_name):

        return os.path.join(self.proj_root, swath_name, 'data')
    
    def extract_raster_dimensions(self, dim_file):
        try:
            with open(dim_file, 'r') as f:
                content = f.read()
            

            width_match = re.search(r'<BAND_RASTER_WIDTH>(\d+)</BAND_RASTER_WIDTH>', content)
            width = int(width_match.group(1)) if width_match else None
            

            height_match = re.search(r'<BAND_RASTER_HEIGHT>(\d+)</BAND_RASTER_HEIGHT>', content)
            height = int(height_match.group(1)) if height_match else None
            
            return width, height
        except Exception as e:
            print(f"  ⚠ Error extracting dimensions from {dim_file}: {e}")
            return None, None
    
    def calculate_snaphu_tile_parameters(self, dim_file):
        tile_span_px = 252


        width, height = self.extract_raster_dimensions(dim_file)
        
        if width is None or height is None:
            print(f"  ⚠ Could not extract dimensions, using default tile parameters")
            return {
                'numberOfTileRows_tile': 10,
                'numberOfTileCols_tile': 10,
                'rowOverlap': 50,
                'colOverlap': 50
            }
        
        print(f"  Raster dimensions: width={width}, height={height}")
        
        quot_cols = int(width / tile_span_px)
        quot_rows = int(height / tile_span_px)
        ratio_w = width / tile_span_px
        ratio_h = height / tile_span_px
        
        numberOfTileCols_tile = max(1, quot_cols)
        numberOfTileRows_tile = max(1, quot_rows)
        
        narrow_w = ratio_w < 2
        narrow_h = ratio_h < 2
        if narrow_w:
            numberOfTileRows_tile = 1
        if narrow_h:
            numberOfTileCols_tile = 1
        
        print(
            f"  Tile calculation ({tile_span_px}x{tile_span_px}): "
            f"width/{tile_span_px}={ratio_w:.4f}, height/{tile_span_px}={ratio_h:.4f} "
            f"(int quotients: {quot_cols}, {quot_rows})"
        )
        if narrow_w or narrow_h:
            notes = []
            if narrow_w:
                notes.append(f"width/{tile_span_px} < 2 → numberOfTileRows_tile = 1")
            if narrow_h:
                notes.append(f"height/{tile_span_px} < 2 → numberOfTileCols_tile = 1")
            print(f"  {'; '.join(notes)}")
        print(f"  Setting numberOfTileRows_tile={numberOfTileRows_tile}, numberOfTileCols_tile={numberOfTileCols_tile}")
        
        tile_width = width / numberOfTileCols_tile
        tile_height = height / numberOfTileRows_tile
        max_tile_val = max(tile_width, tile_height)
        
        print(f"  Tile dimensions: tile_width={tile_width:.1f}, tile_height={tile_height:.1f}, max={max_tile_val:.1f}")
        
        overlap = int(0.1 * max_tile_val)
        if overlap < 1:
            overlap = 1
        
        print(f"  Overlap (10% of max tile dimension): {overlap}")
        
        return {
            'numberOfTileRows_tile': numberOfTileRows_tile,
            'numberOfTileCols_tile': numberOfTileCols_tile,
            'rowOverlap': overlap,
            'colOverlap': overlap
        }
    
    def log_snaphu_failure(self, pair_name, error_msg=""):
        log_file = os.path.join(self.proj_root, 'snaphu_error.log')
        
        try:
            with open(log_file, 'a') as f:
                timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                if error_msg:
                    f.write(f"{timestamp} | {pair_name} | {error_msg}\n")
                else:
                    f.write(f"{timestamp} | {pair_name}\n")
            
            print(f"  ⚠️  Logged SNAPHU failure for {pair_name} to snaphu_error.log")
        except Exception as e:
            print(f"  WARNING: Failed to write to snaphu_error.log: {e}")
    
    def get_snaphu_failed_conf_files(self):
        log_file = os.path.join(self.proj_root, 'snaphu_error.log')
        failed_conf_files = set()
        
        if not os.path.exists(log_file):
            return failed_conf_files
        
        try:
            with open(log_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split('|')
                    if len(parts) >= 2:
                        conf_name = parts[1].strip()
                        if conf_name.endswith('snaphu.conf'):
                            failed_conf_files.add(conf_name)
        except Exception as e:
            print(f"  WARNING: Failed to read snaphu_error.log: {e}")
        
        return failed_conf_files
    
    def get_snaphu_failed_pairs(self):
        log_file = os.path.join(self.proj_root, 'snaphu_error.log')
        failed_pairs = set()
        
        if not os.path.exists(log_file):
            return failed_pairs
        
        try:
            with open(log_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    
                    parts = line.split('|')
                    if len(parts) >= 2:
                        name = parts[1].strip()
                        if '_' in name and name.count('_') == 1:
                            parts_name = name.split('_')
                            if all(p.isdigit() and len(p) == 8 for p in parts_name):
                                failed_pairs.add(name)
        except Exception as e:
            print(f"  WARNING: Failed to read snaphu_error.log: {e}")
        
        return failed_pairs
    
    def get_bad_pairs(self):
        bad_pair_file = os.path.join(self.proj_root, 'bad_pair.txt')
        bad_pairs = set()
        
        if not os.path.exists(bad_pair_file):
            return bad_pairs
        
        try:
            with open(bad_pair_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    if '_' in line and len(line.split('_')) == 2:
                        bad_pairs.add(line)
        except Exception as e:
            print(f"  WARNING: Failed to read bad_pair.txt: {e}")
        
        return bad_pairs
    
    def find_slice_dim(self, swath_name, date):
        """Find slice .dim file for a given date in swath data directory"""
        data_dir = self.get_swath_data_dir(swath_name)
        date_dir = os.path.join(data_dir, str(date))
        
        if not os.path.exists(date_dir):
            return None
        
        dim_files = glob.glob(os.path.join(date_dir, '*.dim'))
        
        if not dim_files:
            return None
        
        return dim_files[0]
    
    def get_merge_folder_name(self, swath_config):
        if swath_config == 0:
            return "merge_IW1_IW2_IW3"
        elif swath_config == 12:
            return "merge_IW1_IW2"
        elif swath_config == 23:
            return "merge_IW2_IW3"
        else:
            return None
    
    
    def get_graph_file(self, filename):
        """Resolve SNAP graph path: absolute paths unchanged; otherwise XML under graph_path only."""
        if os.path.isabs(filename):
            if not os.path.isfile(filename):
                raise FileNotFoundError(f"SNAP graph XML not found: {filename}")
            return filename
        
        gp = getattr(self, 'graph_path', None)
        if not gp:
            raise RuntimeError("'graph_path' was not initialized; check SBASMultiProcessor setup.")
        
        path = os.path.join(gp, filename)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                f"SNAP graph '{filename}' not found under graph_path={gp}\n"
                f"Expected file: {path}"
            )
        return path
    
    def get_stamps_root_for_method(self, method):
        stamps_dir = 'Stamps_ps' if method == 2 else 'Stamps_sbas'
        return os.path.join(self.proj_root, stamps_dir)
    
    def get_stamps_root(self):
        """Return Stamps output root based on insar_method."""
        return self.get_stamps_root_for_method(self.insar_method)
    
    def get_insar_root(self, stamps_root):
        return os.path.join(stamps_root, f"INSAR_{self.master_date}")
    
    def get_stamps_pair_dir(self, stamps_root, pair_name):
        return os.path.join(self.get_insar_root(stamps_root), 'SMALL_BASELINES', pair_name)
    
    def get_target_dates_from_bandmath(self, bandmath_dir, pair_name):
        pair_dir = os.path.join(bandmath_dir, pair_name)
        if not os.path.exists(pair_dir):
            return []
        
        data_folder = None
        for item in os.listdir(pair_dir):
            if item.endswith('.data'):
                data_folder = os.path.join(pair_dir, item)
                break
        
        if not data_folder or not os.path.exists(data_folder):
            return []
        
        target_dates = set()
        master_fmt = self.convert_date_format(self.master_date)
        
        for filename in os.listdir(data_folder):
            if not filename.endswith('.hdr'):
                continue
            
            if any(filename.startswith(b) for b in ['elevation', 'incidenceAngle', 'orthorectifiedLat', 'orthorectifiedLon']):
                continue
            
            basename = filename.replace('.hdr', '')
            if master_fmt in basename:
                parts = basename.split(master_fmt + '_')
                if len(parts) > 1:
                    target_fmt = parts[1]
                    target_date = self.convert_date_format_reverse(target_fmt)
                    if target_date:
                        target_dates.add(target_date)
        
        return sorted(list(target_dates))
    
    def convert_date_format_reverse(self, date_str):
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12'
        }
        
        try:
            day = date_str[:2]
            month_str = date_str[2:5]
            year = date_str[5:9]
            
            if month_str in months:
                return f"{year}{months[month_str]}{day}"
        except:
            pass
        
        return None
    
    def stamps_pair_outputs_exist(self, output_dir, pair_name, method, target_dates=None):
        parts = pair_name.split('_')
        if len(parts) < 2:
            return False
        date1, date2 = parts[0], parts[1]
        
        if method == 1:
            required = [
                os.path.join(output_dir, f"{pair_name}.base"),
                os.path.join(output_dir, f"{pair_name}.diff"),
                os.path.join(output_dir, f"{pair_name}.diff.par"),
                os.path.join(output_dir, f"{date1}.rslc"),
                os.path.join(output_dir, f"{date1}.rslc.par"),
                os.path.join(output_dir, f"{date2}.rslc"),
                os.path.join(output_dir, f"{date2}.rslc.par"),
                os.path.join(output_dir, f"{self.master_date}.rslc.par"),
            ]
        else:
            if not target_dates:
                target_dates = [date1, date2]
            
            required = []
            for target_date in target_dates:
                master_target = f"{self.master_date}_{target_date}"
                required.extend([
                    os.path.join(output_dir, 'diff0', f"{master_target}.base"),
                    os.path.join(output_dir, 'diff0', f"{master_target}.diff"),
                    os.path.join(output_dir, 'diff0', f"{master_target}.diff.par"),
                    os.path.join(output_dir, 'rslc', f"{target_date}.rslc"),
                    os.path.join(output_dir, 'rslc', f"{target_date}.rslc.par"),
                ])
        
        return all(os.path.exists(p) for p in required)

    def _validate_stamps_export_output(self, output_dir, pair_name, method, target_dates=None):
        """Return True when StampsExport wrote diff0/rslc content under ``output_dir``."""
        diff0 = os.path.join(output_dir, 'diff0')
        rslc = os.path.join(output_dir, 'rslc')
        if not os.path.isdir(diff0) or not os.path.isdir(rslc):
            return False
        if method == 1:
            _, trio = self._stamps_collect_diff_triplet(diff0, pair_name)
            if trio is None:
                return False
            parts = pair_name.split('_')
            if len(parts) != 2:
                return False
            d1, d2 = parts[0], parts[1]
            rslc_ok = any(
                os.path.isfile(os.path.join(rslc, f))
                for f in (f"{d1}.rslc", f"{d2}.rslc"))
            return rslc_ok
        if target_dates:
            for td in target_dates:
                stem = f"{self.master_date}_{td}"
                if os.path.isfile(os.path.join(diff0, f"{stem}.diff")):
                    if os.path.isfile(os.path.join(rslc, f"{td}.rslc")):
                        return True
            return False
        return bool(os.listdir(diff0)) and bool(os.listdir(rslc))

    def _log_last_gpt_output_hint(self):
        """Print tail of last GPT stdout/stderr when export validation fails."""
        out = getattr(self, '_last_cmd_stdout', '') or ''
        err = getattr(self, '_last_cmd_stderr', '') or ''
        blob = (err + '\n' + out).strip()
        if blob:
            tail = blob[-800:] if len(blob) > 800 else blob
            print(f"  GPT output (tail):\n{tail}")
        return blob

    def _compare_stamps_export_dimensions(self, coreg_dim, intf_dim):
        coreg_w, coreg_h = self.extract_raster_dimensions(coreg_dim)
        intf_w, intf_h = self.extract_raster_dimensions(intf_dim)
        if None in (coreg_w, coreg_h, intf_w, intf_h):
            return None, (coreg_w, coreg_h), (intf_w, intf_h)
        return (coreg_w, coreg_h) == (intf_w, intf_h), (coreg_w, coreg_h), (intf_w, intf_h)

    def _last_gpt_mentions_dimension_mismatch(self):
        blob = (
            (getattr(self, '_last_cmd_stderr', '') or '')
            + '\n'
            + (getattr(self, '_last_cmd_stdout', '') or '')
        ).lower()
        return 'compatible dimensions' in blob or 'dimension' in blob and 'geocoding' in blob

