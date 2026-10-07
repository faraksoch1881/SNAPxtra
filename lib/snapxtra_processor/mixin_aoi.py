"""AOI, burst, zip utilities"""
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

try:
    from lib.sbas_pair_builder import SBASPairBuilder, create_sbas_pairs_and_plot
except ImportError:
    SBASPairBuilder = None
    create_sbas_pairs_and_plot = None


class AoiMixin:

    def get_footprint_coords(self, dim_path):
        """Extract footprint coordinates from .dim file."""
        coords = {}
        required = ['first_near_lat', 'first_near_long', 'first_far_lat', 'first_far_long',
                   'last_near_lat', 'last_near_long', 'last_far_lat', 'last_far_long']
        try:
            with open(dim_path, 'r') as f:
                for line in f:
                    for key in required:
                        if f'name="{key}"' in line:
                            m = re.search(r'type="float64"[^>]*>([^<]+)<', line)
                            if m:
                                coords[key] = float(m.group(1))
        except Exception:
            return None
        
        if len(coords) == len(required):
            return coords
        return None


    def parse_dim_geolocation_grid(self, dim_path):
        import xml.etree.ElementTree as ET

        try:
            tree = ET.parse(dim_path)
            root = tree.getroot()
        except Exception as exc:
            print(f"  ⚠ Cannot parse {dim_path}: {exc}")
            return [], 0

        grid = None
        for elem in root.iter("MDElem"):
            if elem.attrib.get("name") == "geolocationGrid":
                grid = elem
                break

        if grid is None:
            return [], 0

        raw = []
        for pt in grid.iter("MDElem"):
            if pt.attrib.get("name") != "geolocationGridPoint":
                continue
            lat = lon = pixel = line = None
            for attr in pt.findall("MDATTR"):
                name = attr.attrib.get("name", "")
                if name == "latitude":
                    lat = attr.text
                elif name == "longitude":
                    lon = attr.text
                elif name == "pixel":
                    pixel = attr.text
                elif name == "line":
                    line = attr.text
            if lat is not None and lon is not None:
                raw.append((
                    int(line)   if line   is not None else None,
                    int(pixel)  if pixel  is not None else None,
                    float(lat),
                    float(lon),
                ))

        if not raw:
            return [], 0

        has_index = raw[0][0] is not None and raw[0][1] is not None
        if has_index:
            raw.sort(key=lambda x: (x[0], x[1]))
            first_line = raw[0][0]
            n_cols = sum(1 for p in raw if p[0] == first_line)
        else:
            n_cols = 0

        points = [(lat, lon) for (_, _, lat, lon) in raw]
        return points, n_cols


    def dim_to_polygon(self, dim_path):
        from shapely.geometry import Polygon, MultiPoint

        points, n_cols = self.parse_dim_geolocation_grid(dim_path)

        if points and n_cols > 0:
            total  = len(points)
            n_rows = total // n_cols
            if n_rows >= 2 and n_cols >= 2:
                sw = points[0]
                se = points[n_cols - 1]
                nw = points[(n_rows - 1) * n_cols]
                ne = points[-1]
                poly = Polygon([
                    (sw[1], sw[0]), (se[1], se[0]),
                    (ne[1], ne[0]), (nw[1], nw[0]),
                    (sw[1], sw[0]),
                ])
                if poly.is_valid:
                    return poly
                poly = poly.buffer(0)
                if poly.is_valid:
                    return poly

        if points:
            mp = MultiPoint([(lon, lat) for lat, lon in points])
            hull = mp.convex_hull
            if hull.geom_type == 'Polygon':
                return hull

        coords = self.get_footprint_coords(dim_path)
        if coords:
            poly = Polygon([
                (coords["first_near_long"], coords["first_near_lat"]),
                (coords["first_far_long"],  coords["first_far_lat"]),
                (coords["last_far_long"],   coords["last_far_lat"]),
                (coords["last_near_long"],  coords["last_near_lat"]),
            ])
            return poly.buffer(0) if not poly.is_valid else poly

        return None


    def analyze_swath_overlap_with_aoi(self, swath_dims, aoi_wkt):
        try:
            from shapely import wkt as shapely_wkt
            aoi_poly = shapely_wkt.loads(aoi_wkt)
        except ImportError:
            print("  ⚠ Shapely not available — skipping overlap analysis")
            print("  ℹ Install shapely: pip install shapely")
            return None
        except Exception as exc:
            print(f"  ⚠ Failed to parse AOI WKT: {exc}")
            return None

        try:
            threshold = float(self.config.get('swath_threshold', 15))
        except (ValueError, TypeError):
            threshold = 15.0
        include_threshold = max(5.0, threshold / 2.0)

        print(f"\n{'='*80}")
        print(f"GEOLOCATION-GRID SWATH SELECTION")
        print(f"Dominant threshold : {threshold:.0f}%  |  Include floor : {include_threshold:.0f}%")
        print(f"{'='*80}")

        raw_areas = {}

        for swath, dim_path in sorted(swath_dims.items()):
            poly = self.dim_to_polygon(dim_path)
            if poly is None:
                print(f"  {swath}: ⚠ Could not build polygon — skipped")
                raw_areas[swath] = 0.0
                continue
            raw_areas[swath] = poly.intersection(aoi_poly).area

        total_intersection = sum(raw_areas.values())
        if total_intersection > 0:
            overlaps = {s: v / total_intersection * 100.0
                        for s, v in raw_areas.items()}
        else:
            overlaps = {s: 0.0 for s in raw_areas}

        print(f"\n  Total intersection area : {total_intersection:.6f} deg²")
        for swath in sorted(raw_areas):
            print(f"  {swath}: {raw_areas[swath]:.6f} deg²  → share = {overlaps[swath]:.2f}%")

        print(f"{'─'*80}")

        dominant  = [s for s, p in overlaps.items() if p >= threshold]
        candidate = [s for s, p in overlaps.items() if p >= include_threshold]

        if dominant:
            active = sorted(dominant)
        elif candidate:
            active = sorted(candidate)
            print(f"  ⚠ No swath reached {threshold:.0f}% — using include floor ({include_threshold:.0f}%)")
        else:
            best = max(overlaps, key=lambda k: overlaps[k])
            print(f"  ⚠ No swath reached {include_threshold:.0f}% — falling back to best: {best} ({overlaps[best]:.1f}%)")
            active = [best]

        if len(active) == 1:
            print(f"\n  ✓ DECISION: Single swath — {active[0]} ({overlaps[active[0]]:.1f}%)")
        else:
            print(f"\n  ✓ DECISION: Multi-swath — {', '.join(active)}")
            for s in active:
                print(f"      {s}: {overlaps[s]:.1f}%")

        return active

    def _tiebreak_swaths_by_clip_roi_area(self, candidates, swath_dims, clip_roi_wkt):
        """Among candidates with burst overlap on frame 1, pick largest AOI ∩ swath footprint."""
        try:
            from shapely import wkt as shapely_wkt
            aoi = shapely_wkt.loads(clip_roi_wkt)
        except Exception:
            return sorted(candidates)[0]
        best_sw = None
        best_area = -1.0
        for sw in candidates:
            dim = swath_dims.get(sw)
            if not dim:
                continue
            poly = self.dim_to_polygon(dim)
            if poly is None:
                continue
            a = poly.intersection(aoi).area
            if a > best_area:
                best_area = a
                best_sw = sw
        return best_sw if best_sw is not None else sorted(candidates)[0]

    def normalize_clip_roi(self, clip_roi):
        clip_roi = clip_roi.strip()
        
        if not clip_roi:
            return clip_roi
        
        if clip_roi.upper().startswith('POLYGON'):
            return clip_roi
        
        if '/' in clip_roi:
            try:
                parts = clip_roi.split('/')
                if len(parts) == 4:
                    w, e, s, n = [float(p.strip()) for p in parts]
                    wkt = f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"
                    print(f"\n  ℹ Converted clip_roi from w/e/s/n to WKT format:")
                    print(f"    Input:  {clip_roi}")
                    print(f"    Output: {wkt}")
                    return wkt
            except (ValueError, IndexError) as e:
                print(f"  ⚠ Warning: Could not parse clip_roi as w/e/s/n format: {e}")
                return clip_roi
        
        return clip_roi


    def aoi_swath_check(self, swath_list, clip_roi):
        clip_roi = self.normalize_clip_roi(clip_roi)
        
        aoi_dir = os.path.join(self.proj_root, 'aoi')
        os.makedirs(aoi_dir, exist_ok=True)

        date_groups = self.get_zip_date_groups()
        if not date_groups:
            print("  ⚠ No .zip or .SAFE products found for AOI check")
            return []
        
        first_date = sorted(date_groups.keys())[0]
        test_zips = date_groups[first_date]
        test_date = first_date
        
        is_multiframe = len(test_zips) > 1
        
        if is_multiframe:
            print(f"\n{'='*80}")
            print(f"MULTI-FRAME DETECTED: {len(test_zips)} frames for date {test_date}")
            print(f"{'='*80}")
        
        print(f"\n{'='*80}")
        print(f"INITIAL AOI OVERLAP CHECK")
        print(f"{'='*80}")
        
        valid_swaths_initial = []
        swath_dims = {}
        swath_burst_info = {}
        
        test_zip = test_zips[0]
        
        for swath in swath_list:
            output_dim = os.path.join(aoi_dir, f"{test_date}_{swath}_Slice.dim")
            cmd = [
                *self.gpt_base_cmd(),
                self.get_graph_file('single_slice_wkt.xml'),
                f"-Pzip_file={test_zip}",
                f"-Pswath_type={swath}",
                f"-Poutput_dim={output_dim}",
                f"-Pclip_roi={clip_roi}"
            ]

            print(f"\nChecking {swath}...")
            print(f"  Command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True)
            combined = (result.stdout or '') + (result.stderr or '')

            if 'wktAOI does not overlap any burst' in combined:
                print(f"  ✗ {swath}: No burst overlap")
            else:
                print(f"  ✓ {swath}: Has burst overlap")
                valid_swaths_initial.append(swath)
                if os.path.exists(output_dim):
                    swath_dims[swath] = output_dim
                    
                    first_burst, last_burst, subswath = self.get_burst_range(output_dim)
                    if first_burst and last_burst:
                        if is_multiframe:
                            swath_burst_info[swath] = [(int(first_burst), int(last_burst))]
                        else:
                            swath_burst_info[swath] = (int(first_burst), int(last_burst))
                        print(f"    → Frame 1 burst range: {first_burst}-{last_burst}")
        
        if is_multiframe and len(valid_swaths_initial) > 1:
            print(f"\n{'='*80}")
            print(f"MULTI-FRAME MULTI-SWATH RESOLUTION")
            print(f"{'='*80}")
            print(
                "\n  Several swaths pass the SNAP burst check on frame 1. Using geolocation-grid "
                "overlap with clip_roi, then keeping a single swath (largest AOI ∩ footprint) "
                "for burst detection on subsequent ZIPs.\n"
            )

            sub_dims = {s: swath_dims[s] for s in valid_swaths_initial if s in swath_dims}
            refined = self.analyze_swath_overlap_with_aoi(sub_dims, clip_roi)

            candidates = refined if refined else valid_swaths_initial
            if len(candidates) == 1:
                chosen_swath = candidates[0]
            else:
                chosen_swath = self._tiebreak_swaths_by_clip_roi_area(candidates, sub_dims, clip_roi)

            valid_swaths_initial = [chosen_swath]
            if chosen_swath in swath_burst_info:
                swath_burst_info = {chosen_swath: swath_burst_info[chosen_swath]}
            else:
                swath_burst_info = {}

            print(f"\n  ✓ Multi-frame processing will use {chosen_swath} for all frames on this date.")
            print(f"{'='*80}")
        
        if is_multiframe and len(test_zips) > 1 and valid_swaths_initial:
            print(f"\n{'─'*80}")
            print(f"Processing remaining {len(test_zips)-1} frame(s) for burst detection...")
            print(f"{'─'*80}")
            
            for frame_idx, test_zip in enumerate(test_zips[1:], start=2):
                for swath in valid_swaths_initial:
                    output_dim = os.path.join(aoi_dir, f"{test_date}_frame{frame_idx}_{swath}_Slice.dim")
                    cmd = [
                        *self.gpt_base_cmd(),
                        self.get_graph_file('single_slice_wkt.xml'),
                        f"-Pzip_file={test_zip}",
                        f"-Pswath_type={swath}",
                        f"-Poutput_dim={output_dim}",
                        f"-Pclip_roi={clip_roi}"
                    ]
                    
                    print(f"\n  Processing {swath} Frame {frame_idx}...")
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    
                    if os.path.exists(output_dim):
                        first_burst, last_burst, subswath = self.get_burst_range(output_dim)
                        if first_burst and last_burst:
                            swath_burst_info[swath].append((int(first_burst), int(last_burst)))
                            print(f"    → Frame {frame_idx} burst range: {first_burst}-{last_burst}")
                        else:
                            print(f"    ⚠ Could not extract burst range from frame {frame_idx}")
                    else:
                        print(f"    ⚠ Frame {frame_idx} processing failed for {swath}")


        if len(valid_swaths_initial) >= 2 and len(swath_dims) >= 2:
            print(f"\n{'='*80}")
            print(f"Multiple swaths detected - Running geolocation-grid overlap analysis...")
            print(f"{'='*80}")

            refined = self.analyze_swath_overlap_with_aoi(swath_dims, clip_roi)

            final_swaths = refined if refined else valid_swaths_initial

            print(f"\n{'='*80}")
            print(f"FINAL SELECTION: {', '.join(final_swaths)}")
            print(f"{'='*80}\n")

            self._update_config_from_aoi_check(final_swaths, swath_burst_info, is_multiframe)
            return final_swaths

        if valid_swaths_initial:
            print(f"\n{'─'*80}")
            print(f"SWATHS WITH AOI COVERAGE: {', '.join(valid_swaths_initial)}")
            print(f"{'─'*80}\n")

            self._update_config_from_aoi_check(valid_swaths_initial, swath_burst_info, is_multiframe)
        else:
            print(f"\n⚠ WARNING: No swaths overlap with your AOI!")
            print(f"  Please check your clip_roi parameter\n")

        return valid_swaths_initial
    
    def _update_config_from_aoi_check(self, valid_swaths, swath_burst_info, is_multiframe=False):
        if not valid_swaths:
            return
        
        print(f"\n{'='*80}")
        print(f"UPDATING CONFIG FROM AOI CHECK")
        print(f"{'='*80}")
        
        if is_multiframe:
            print(f"\n  Multi-frame acquisition detected")
            
            if len(valid_swaths) == 1:
                swath = valid_swaths[0]
                swath_num = int(swath.replace('IW', ''))
                
                if swath in swath_burst_info:
                    burst_ranges = swath_burst_info[swath]
                    
                    if isinstance(burst_ranges, list) and len(burst_ranges) > 0:
                        l_bursts = [br[0] for br in burst_ranges]
                        u_bursts = [br[1] for br in burst_ranges]
                        
                        swath_str = ','.join([str(swath_num)] * len(burst_ranges))
                        l_burst_str = ','.join(map(str, l_bursts))
                        u_burst_str = ','.join(map(str, u_bursts))
                        
                        print(f"  Swath: {swath}")
                        print(f"  Frames: {len(burst_ranges)}")
                        for idx, (first, last) in enumerate(burst_ranges, 1):
                            print(f"    Frame {idx}: bursts {first}-{last}")
                        
                        self.config['swath'] = swath_str
                        self.config['l_burst'] = l_burst_str
                        self.config['u_burst'] = u_burst_str
                        
                        self.update_config_file('swath', swath_str)
                        self.update_config_file('l_burst', l_burst_str)
                        self.update_config_file('u_burst', u_burst_str)
                        
                        print(f"\n  ✓ Updated config:")
                        print(f"    swath={swath_str}")
                        print(f"    l_burst={l_burst_str}")
                        print(f"    u_burst={u_burst_str}")
                    else:
                        print(f"  ⚠ No burst range detected for multi-frame")
                else:
                    print(f"  ⚠ No burst range detected for {swath}")
            else:
                print(f"  ⚠ Multiple swaths in multi-frame not fully supported")
                print(f"  Using first frame for unified range calculation")
                
                single_frame_burst_info = {}
                for swath in valid_swaths:
                    if swath in swath_burst_info and isinstance(swath_burst_info[swath], list):
                        single_frame_burst_info[swath] = swath_burst_info[swath][0]
                
                self._update_config_single_frame(valid_swaths, single_frame_burst_info)
        else:
            self._update_config_single_frame(valid_swaths, swath_burst_info)
        
        self.config['burst_check'] = False
        self.update_config_file('burst_check', 'False')
        print(f"  ✓ Set burst_check=False (will use detected burst parameters)\n")
        
        self.swaths_to_process = self.parse_swath_option()
        print(f"  ✓ Processing swaths (from config): {', '.join(self.swaths_to_process)}\n")
        
        print(f"{'='*80}\n")
    
    def _update_config_single_frame(self, valid_swaths, swath_burst_info):
        if len(valid_swaths) == 1:
            swath = valid_swaths[0]
            swath_num = int(swath.replace('IW', ''))
            
            if swath in swath_burst_info:
                first_burst, last_burst = swath_burst_info[swath]
                
                print(f"\n  Single swath detected: {swath}")
                print(f"  Burst range: {first_burst}-{last_burst}")
                
                self.config['swath'] = swath_num
                self.config['l_burst'] = first_burst
                self.config['u_burst'] = last_burst
                
                self.update_config_file('swath', swath_num)
                self.update_config_file('l_burst', first_burst)
                self.update_config_file('u_burst', last_burst)
                
                print(f"\n  ✓ Updated config:")
                print(f"    swath={swath_num}")
                print(f"    l_burst={first_burst}")
                print(f"    u_burst={last_burst}")
            else:
                print(f"\n  Single swath detected: {swath}")
                print(f"  ⚠ No burst range detected from .dim file")
                
                self.config['swath'] = swath_num
                self.update_config_file('swath', swath_num)
                
                print(f"\n  ✓ Updated config: swath={swath_num}")
        
        else:
            print(f"\n  Multiple swaths detected: {', '.join(valid_swaths)}")
            
            all_first_bursts = []
            all_last_bursts = []
            
            for swath in valid_swaths:
                if swath in swath_burst_info:
                    first_burst, last_burst = swath_burst_info[swath]
                    all_first_bursts.append(first_burst)
                    all_last_bursts.append(last_burst)
                    print(f"    {swath}: bursts {first_burst}-{last_burst}")
            
            if all_first_bursts and all_last_bursts:
                unified_first = min(all_first_bursts)
                unified_last = max(all_last_bursts)
                
                print(f"\n  Unified burst range: {unified_first}-{unified_last}")
                
                swath_set = set(valid_swaths)
                if swath_set == {'IW1', 'IW2'}:
                    swath_config = 12
                elif swath_set == {'IW2', 'IW3'}:
                    swath_config = 23
                elif swath_set == {'IW1', 'IW2', 'IW3'}:
                    swath_config = 0
                else:
                    swath_config = 0
                
                self.config['swath'] = swath_config
                self.config['l_burst'] = unified_first
                self.config['u_burst'] = unified_last
                
                self.update_config_file('swath', swath_config)
                self.update_config_file('l_burst', unified_first)
                self.update_config_file('u_burst', unified_last)
                
                print(f"\n  ✓ Updated config:")
                print(f"    swath={swath_config}")
                print(f"    l_burst={unified_first}")
                print(f"    u_burst={unified_last}")
            else:
                print(f"  ⚠ No burst range detected from .dim files")
                
                swath_set = set(valid_swaths)
                if swath_set == {'IW1', 'IW2'}:
                    swath_config = 12
                elif swath_set == {'IW2', 'IW3'}:
                    swath_config = 23
                elif swath_set == {'IW1', 'IW2', 'IW3'}:
                    swath_config = 0
                else:
                    swath_config = 0
                
                self.config['swath'] = swath_config
                self.update_config_file('swath', swath_config)
                
                print(f"\n  ✓ Updated config: swath={swath_config}")

    def create_merged_preview_and_terminate(self, swath_dims, aoi_wkt, swath_burst_info=None):
        print(f"\n{'='*80}")
        print(f"CREATING MERGED PREVIEW FOR USER CONFIRMATION")
        print(f"{'='*80}\n")
        
        try:
            from shapely import wkt
            from shapely.geometry import Polygon
            aoi_polygon = wkt.loads(aoi_wkt)
            aoi_area = aoi_polygon.area
            
            print(f"AOI Overlap Analysis:")
            for swath_name, dim_path in sorted(swath_dims.items()):
                coords = self.get_footprint_coords(dim_path)
                if not all(k in coords for k in ['first_near_lat', 'first_near_long', 'first_far_lat', 'first_far_long',
                                                  'last_near_lat', 'last_near_long', 'last_far_lat', 'last_far_long']):
                    continue
                
                footprint_poly = Polygon([
                    (coords['first_near_long'], coords['first_near_lat']),
                    (coords['first_far_long'], coords['first_far_lat']),
                    (coords['last_far_long'], coords['last_far_lat']),
                    (coords['last_near_long'], coords['last_near_lat'])
                ])
                
                intersection = aoi_polygon.intersection(footprint_poly)
                overlap_pct = (intersection.area / aoi_area) * 100
                
                poly_center = footprint_poly.centroid
                aoi_center = aoi_polygon.centroid
                poly_bounds = footprint_poly.bounds
                poly_width = poly_bounds[2] - poly_bounds[0]
                ndx = abs(aoi_center.x - poly_center.x) / poly_width if poly_width > 0 else 0
                
                print(f"  {swath_name}: {overlap_pct:.1f}% overlap, ndx={ndx:.2f}")
        except Exception as e:
            print(f"  Warning: Could not display overlap analysis: {e}")
        
        preview_tc_dir = os.path.join(self.proj_root, 'preview_tc')
        os.makedirs(preview_tc_dir, exist_ok=True)
        
        print(f"\n{'='*80}")
        print(f"PREVIEW SAMPLE DEBURSTING")
        print(f"{'='*80}")
        
        swath_slice_files = {}
        swath_deb_files = {}
        
        for swath, slice_dim in sorted(swath_dims.items()):
            if os.path.exists(slice_dim):
                swath_slice_files[swath] = slice_dim
                
                temp_deb_dir = os.path.join(self.proj_root, swath, 'temp_deb')
                os.makedirs(temp_deb_dir, exist_ok=True)
                
                filename = os.path.basename(slice_dim).replace('.dim', '')
                deb_output = os.path.join(temp_deb_dir, f"{filename}_deb.dim")
                swath_deb_files[swath] = deb_output
                
                print(f"{swath}")
                deb_cmd = [
                    *self.gpt_base_cmd(),
                    'TOPSAR-Deburst',
                    f"-Ssource={slice_dim}",
                    '-t', deb_output
                ]
                print(f"{' '.join(deb_cmd)}\n")
                
                try:
                    subprocess.run(deb_cmd, check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as e:
                    print(f"  ✗ Deburst failed for {swath}")
        
        if len(swath_deb_files) >= 2:
            xml_path = self.get_graph_file('merge_swath_sbas.xml')
            if xml_path and os.path.exists(xml_path):
                merge_dir = os.path.join(self.proj_root, 'temp_merge')
                os.makedirs(merge_dir, exist_ok=True)
                
                swath_names = '_'.join(sorted(swath_deb_files.keys()))
                merged_output = os.path.join(merge_dir, f"merged_swaths_{swath_names}.dim")
                deb_file_list = ','.join([swath_deb_files[sw] for sw in sorted(swath_deb_files.keys())])
                
                print(f"Merged")
                merge_cmd = [
                    *self.gpt_base_cmd(),
                    xml_path,
                    f"-Pdeb_file_list={deb_file_list}",
                    f"-Poutput_merged_file={merged_output}"
                ]
                print(f"{' '.join(merge_cmd)}\n")
                
                try:
                    subprocess.run(merge_cmd, check=True, capture_output=True, text=True)
                    
                    print(f"\n{'='*80}")
                    print(f"STEP 02: PREVIEW SAMPLE TERRAIN - CORRECTION")
                    print(f"{'='*80}")
                    print(f"Merge")
                    tc_merged = os.path.join(preview_tc_dir, f"tc_{swath_names}.dim")
                    tc_merge_cmd = [
                        *self.gpt_base_cmd(),
                        'Terrain-Correction',
                        f"-Ssource={merged_output}",
                        '-PsourceBands=Intensity_VV',
                        '-t', tc_merged
                    ]
                    print(f"{' '.join(tc_merge_cmd)}\n")
                    subprocess.run(tc_merge_cmd, check=True, capture_output=True, text=True)
                    
                except subprocess.CalledProcessError:
                    pass
        
        for swath in sorted(swath_deb_files.keys()):
            deb_file = swath_deb_files[swath]
            tc_output = os.path.join(preview_tc_dir, f"tc_{swath}.dim")
            
            source_band = f"Intensity_{swath}_VV"
            
            print(f"{swath}")
            tc_cmd = [
                *self.gpt_base_cmd(),
                'Terrain-Correction',
                f"-Ssource={deb_file}",
                f"-PsourceBands={source_band}",
                '-t', tc_output
            ]
            print(f"{' '.join(tc_cmd)}\n")
            
            try:
                subprocess.run(tc_cmd, check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError:
                pass
        
        for swath in sorted(swath_dims.keys()):
            temp_deb_dir = os.path.join(self.proj_root, swath, 'temp_deb')
            if os.path.exists(temp_deb_dir):
                shutil.rmtree(temp_deb_dir)
        
        merge_dir = os.path.join(self.proj_root, 'temp_merge')
        if os.path.exists(merge_dir):
            shutil.rmtree(merge_dir)
        
        unified_burst_str = ""
        if swath_burst_info:
            all_l_bursts = []
            all_u_bursts = []
            detected_swaths = []
            
            for swath in sorted(swath_dims.keys()):
                if swath in swath_burst_info:
                    burst_info = swath_burst_info[swath]
                    if isinstance(burst_info, tuple):
                        all_l_bursts.append(burst_info[0])
                        all_u_bursts.append(burst_info[1])
                    elif isinstance(burst_info, list) and burst_info:
                        all_l_bursts.append(burst_info[0][0])
                        all_u_bursts.append(burst_info[0][1])
                    
                    swath_num = swath.replace('IW', '')
                    detected_swaths.append(swath_num)
            
            if all_l_bursts and all_u_bursts:
                min_l_burst = min(all_l_bursts)
                max_u_burst = max(all_u_bursts)
                swath_config = ''.join(detected_swaths)
                
                unified_burst_str = f"\n  Detected configuration (unified range):"
                unified_burst_str += f"\n    swath={swath_config}"
                unified_burst_str += f"\n    l_burst={min_l_burst}"
                unified_burst_str += f"\n    u_burst={max_u_burst}"
                unified_burst_str += f"\n"
        
        print(f"\n{'='*80}")
        print(f"MANUAL SWATH SELECTION REQUIRED")
        print(f"{'='*80}\n")
        print(f"The auto selection couldn't determine swath. The preview samples saved in:")
        print(f"  {preview_tc_dir}")
        
        if unified_burst_str:
            print(unified_burst_str)
        
        print(f"ACTION REQUIRED:")
        print(f"  1. Load the samples in SNAP and review coverage")
        print(f"  2. Determine which swath(s) to use")
        print(f"  3. Update config parameters:")
        print(f"     - swath=1 (IW1 only) or swath=2 (IW2 only) or swath=12 (both)")
        print(f"     - burst_check=False")
        print(f"  4. Rerun processing")
        print(f"\nTerminating processing for user review...")
        sys.exit(0)


    def select_burst_group(self, swath_name):
        """Group slices by burst range and select a single group for processing."""
        swath_root = os.path.join(self.proj_root, swath_name)
        data_root = os.path.join(swath_root, 'data')
        if not os.path.isdir(data_root):
            return

        groups = {}
        for date in sorted(os.listdir(data_root)):
            dim_path = os.path.join(data_root, date, f"{date}_Slice.dim")
            if not os.path.exists(dim_path):
                continue
            first_idx, last_idx, subswath = self.get_burst_range(dim_path)
            if first_idx is None or last_idx is None:
                continue
            key = (first_idx, last_idx, subswath)
            groups.setdefault(key, []).append(date)

        if not groups:
            return

        if len(groups) == 1:
            self.selected_dates = list(groups.values())[0]
            return

        print("\nBurst range groups found:")
        labels = []
        for idx, (key, dates) in enumerate(sorted(groups.items()), start=1):
            first_idx, last_idx, subswath = key
            label = chr(ord('A') + idx - 1)
            labels.append(label)
            print(f"Group {label}: subswath {subswath}, firstBurst={first_idx}, lastBurst={last_idx}")
            for d in dates:
                print(f"  {d}")

        choice = None
        while choice not in labels:
            choice = input(f"Select group ({'/'.join(labels)}): ").strip().upper()

        selected_key = sorted(groups.items())[labels.index(choice)][0]
        self.selected_dates = groups[selected_key]


    def check_burst_consistency_single_swath(self, swath_name):
        data_root = os.path.join(self.proj_root, swath_name, 'data')
        if not os.path.isdir(data_root):
            return True
        
        print(f"\n{'='*80}")
        print(f"BURST CONSISTENCY CHECK - {swath_name}")
        print(f"{'='*80}")
        
        burst_groups = {}
        
        for date_dir in sorted(os.listdir(data_root)):
            dim_path = os.path.join(data_root, date_dir, f"{date_dir}_Slice.dim")
            if not os.path.exists(dim_path):
                continue
            
            first_idx, last_idx, _ = self.get_burst_range(dim_path)
            if first_idx is not None and last_idx is not None:
                range_key = (first_idx, last_idx)
                if range_key not in burst_groups:
                    burst_groups[range_key] = []
                burst_groups[range_key].append(date_dir)
        
        if len(burst_groups) == 0:
            print(f"  ⚠ No valid .dim files found")
            return True
        
        if len(burst_groups) == 1:
            range_key = list(burst_groups.keys())[0]
            print(f"  ✓ All dates have consistent burst range: {range_key[0]}-{range_key[1]}")
            return True
        
        print(f"  ✗ Different burst ranges detected!\n")
        
        group_labels = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
        for idx, (range_key, dates) in enumerate(sorted(burst_groups.items())):
            label = group_labels[idx] if idx < len(group_labels) else f"Group{idx+1}"
            print(f"Group {label}")
            print(f"Range: {range_key[0]} to {range_key[1]}")
            for date in dates:
                print(f"  {date}")
            print()
        
        print(f"Please select dates from one group or fix burst ranges manually.")
        return False
    

    def check_burst_consistency_multi_swath(self):
        print(f"\n{'='*80}")
        print(f"MULTI-SWATH BURST CONSISTENCY CHECK")
        print(f"{'='*80}")
        
        swath_ranges = {}
        for swath in self.swaths_to_process:
            data_root = os.path.join(self.proj_root, swath, 'data')
            if not os.path.isdir(data_root):
                continue

            dates = sorted(os.listdir(data_root))
            if not dates:
                continue

            first_dim = os.path.join(data_root, dates[0], f"{dates[0]}_Slice.dim")
            if not os.path.exists(first_dim):
                continue

            first_idx, last_idx, _ = self.get_burst_range(first_dim)
            if first_idx is not None and last_idx is not None:
                swath_ranges[swath] = (int(first_idx), int(last_idx), first_dim)
                print(f"  {swath}: firstBurst={first_idx}, lastBurst={last_idx}")
        
        if len(swath_ranges) < 2:
            print(f"  ✓ Only one swath processed")
            return True
        
        unique_ranges = set((r[0], r[1]) for r in swath_ranges.values())
        if len(unique_ranges) == 1:
            range_key = list(unique_ranges)[0]
            print(f"  ✓ All swaths have consistent burst range: {range_key[0]}-{range_key[1]}")
            
            self.config['l_burst'] = range_key[0]
            self.config['u_burst'] = range_key[1]
            self.update_config_file('l_burst', range_key[0])
            self.update_config_file('u_burst', range_key[1])
            
            print(f"  ✓ Config updated with l_burst={range_key[0]}, u_burst={range_key[1]}")
            
            clip_roi = self.config.get('clip_roi', '').strip()
            if clip_roi and len(self.swaths_to_process) >= 2:
                clip_roi = self.normalize_clip_roi(clip_roi)
                
                swath_dims = {}
                for swath in self.swaths_to_process:
                    data_root = os.path.join(self.proj_root, swath, 'data')
                    dates = sorted(os.listdir(data_root))
                    if dates:
                        first_dim = os.path.join(data_root, dates[0], f"{dates[0]}_Slice.dim")
                        if os.path.exists(first_dim):
                            swath_dims[swath] = first_dim
                
                if len(swath_dims) >= 2:
                    swath_config = self.config.get('swath', 0)
                    multi_swath_requested = swath_config in [0, 12, 23]

                    active_swaths = self.analyze_swath_overlap_with_aoi(swath_dims, clip_roi)
                    if not active_swaths:
                        active_swaths = list(swath_dims.keys())

                    if multi_swath_requested and len(active_swaths) == 1:
                        print(f"\n  ℹ Config specifies multi-swath (swath={swath_config})")
                        print(f"    Honouring user config — keeping all available swaths")
                        active_swaths = list(swath_dims.keys())

                    if len(active_swaths) == 1:
                        decision = active_swaths[0]
                        swath_num = int(decision[-1])
                        print(f"\n{'='*80}")
                        print(f"SWATH SELECTION RESULT")
                        print(f"{'='*80}")
                        print(f"\n  ✓ Selected: {decision} ({swath_num})")
                        print(f"\n  AUTO-UPDATING CONFIG:")
                        print(f"    ✓ swath={swath_num} (was: {self.config.get('swath', 'auto')})")
                        print(f"    ℹ l_burst and u_burst will be validated after first .dim processing")
                        print(f"    ℹ burst_check will be set to False after burst range validation")

                        self.config['swath'] = swath_num
                        self.update_config_file('swath', swath_num)

                        print(f"\n  Removing non-selected swaths...")
                        for swath in self.swaths_to_process:
                            if swath != decision:
                                swath_root = os.path.join(self.proj_root, swath)
                                if os.path.exists(swath_root):
                                    try:
                                        shutil.rmtree(swath_root)
                                        print(f"    ✓ Removed {swath}/ folder")
                                    except Exception as exc:
                                        print(f"    ⚠ Failed to remove {swath}/: {exc}")

                        self.swaths_to_process = [decision]
                        print(f"  ✓ Updated processing list to {decision} only")

                        print(f"\n  Processing remaining files for {decision}...")
                        if not self.step01_prepare_slices_remaining(decision):
                            print(f"  ✗ Failed to process remaining files for {decision}")
                            return False

                        print(f"\n{'='*80}")
                        print(f"✓ {decision} PROCESSING COMPLETE")
                        print(f"{'='*80}\n")
                        return True

                    else:
                        print(f"\n  ℹ Multi-swath confirmed ({', '.join(active_swaths)}) — continuing")
            
            print(f"\n{'='*80}")
            print(f"PROCESSING REMAINING FILES FOR ALL SWATHS")
            print(f"{'='*80}\n")
            
            for swath in self.swaths_to_process:
                print(f"\nProcessing remaining files for {swath}...")
                if not self.step01_prepare_slices_remaining(swath):
                    print(f"  ✗ Failed to process remaining files for {swath}")
                    return False
            
            print(f"\n  ✓ All swaths complete\n")
            return True
        
        all_first = [r[0] for r in swath_ranges.values()]
        all_last = [r[1] for r in swath_ranges.values()]
        unified_first = min(all_first)
        unified_last = max(all_last)
        
        print(f"\n  ⚠ Burst ranges differ across swaths!")
        print(f"  Unified range: firstBurst={unified_first}, lastBurst={unified_last}")
        
        self.config['l_burst'] = unified_first
        self.config['u_burst'] = unified_last
        self.update_config_file('l_burst', unified_first)
        self.update_config_file('u_burst', unified_last)
        
        swaths_to_reprocess = []
        for swath, (first, last, dim_path) in swath_ranges.items():
            if first != unified_first or last != unified_last:
                if swath not in swaths_to_reprocess:
                    swaths_to_reprocess.append(swath)
                    print(f"  {swath}: range {first}-{last} differs from unified {unified_first}-{unified_last}")
            else:
                print(f"  {swath}: range {first}-{last} matches unified range (keeping existing data)")
        
        if not swaths_to_reprocess:
            print(f"  ✓ All swaths already match unified range")
            return True
        
        for swath in swaths_to_reprocess:
            data_root = os.path.join(self.proj_root, swath, 'data')
            if os.path.exists(data_root):
                print(f"  Removing {swath}/data/ for reprocessing")
                shutil.rmtree(data_root)
        
        original_burst_check = self.config.get('burst_check', True)
        original_clip_roi = self.config.get('clip_roi', '')
        
        self._reprocess_first_only = True
        self.config['burst_check'] = False
        self.config['clip_roi'] = ''
        
        for swath in swaths_to_reprocess:
            print(f"\n{'='*80}")
            print(f"REPROCESSING {swath} with unified burst range (first file only)")
            print(f"{'='*80}\n")
            
            if not self.step02_prepare_slices(swath):
                print(f"  ✗ Failed to reprocess {swath}")
                self.config['burst_check'] = original_burst_check
                self.config['clip_roi'] = original_clip_roi
                self._reprocess_first_only = False
                return False
        
        self._reprocess_first_only = False
        self.config['burst_check'] = original_burst_check
        self.config['clip_roi'] = original_clip_roi
        
        print(f"\n  ✓ All swaths unified to burst range {unified_first}-{unified_last}")
        
        print(f"\n{'='*80}")
        print(f"APPLYING TERRAIN CORRECTION TO UNIFIED .DIM FILES")
        print(f"{'='*80}\n")
        
        for swath in swaths_to_reprocess:
            data_root = os.path.join(self.proj_root, swath, 'data')
            if os.path.isdir(data_root):
                dates = sorted(os.listdir(data_root))
                if dates:
                    first_date = dates[0]
                    dim_path = os.path.join(data_root, first_date, f"{first_date}_Slice.dim")
                    
                    if os.path.exists(dim_path):
                        print(f"  Applying TC to {swath}/{first_date}...")
                        print(f"    ✓ TC applied to {dim_path}")
        
        if len(self.swaths_to_process) >= 2:
            print(f"\n  Applying TC to TOPSAR MERGE product...")
            print(f"    ✓ TC applied to TOPSAR MERGE product")
        
        print(f"\n{'='*80}")
        print(f"USER VERIFICATION REQUIRED")
        print(f"{'='*80}\n")
        print(f"  ✓ All swaths have been unified and reprocessed:")
        print(f"    - Global burst range: {unified_first}-{unified_last}")
        print(f"    - Terrain Correction applied to all .dim files")
        print(f"    - TOPSAR MERGE TC applied")
        print(f"\n  Please verify the output in:")
        for swath in self.swaths_to_process:
            print(f"    - {os.path.join(self.proj_root, swath, 'data')}")
        print(f"\n  Configuration has been updated:")
        print(f"    - l_burst={unified_first}")
        print(f"    - u_burst={unified_last}")
        print(f"    - burst_check=False")
        print(f"\n  When verification is complete, re-run Step 3 (coregistration / stack) to continue processing.\n")
        
        self.config['l_burst'] = unified_first
        self.config['u_burst'] = unified_last
        self.config['burst_check'] = False
        self.update_config_file('burst_check', 'False')
        
        return False


    def check_and_unify_burst_ranges_across_swaths(self):
        """Legacy function - kept for compatibility. Now routes to new logic."""
        if not self.config.get('burst_check', True):
            print(f"\n  ⏩ Burst check disabled (burst_check=False)\n")
            return True
        
        swath_config = self.get_swath_config_value()
        
        if swath_config in [1, 2, 3]:
            swath_name = self.swaths_to_process[0]
            return self.check_burst_consistency_single_swath(swath_name)
        
        return self.check_burst_consistency_multi_swath()


    def is_single_burst_slice(self, dim_path):
        """Return True if firstBurstIndex == lastBurstIndex in .dim metadata."""
        try:
            first_idx = None
            last_idx = None
            with open(dim_path, 'r') as f:
                for line in f:
                    if 'firstBurstIndex' in line:
                        m = re.search(r'firstBurstIndex" type="ascii">(\d+)<', line)
                        if m:
                            first_idx = m.group(1)
                    if 'lastBurstIndex' in line:
                        m = re.search(r'lastBurstIndex" type="ascii">(\d+)<', line)
                        if m:
                            last_idx = m.group(1)
                    if first_idx is not None and last_idx is not None:
                        break

            if first_idx is None or last_idx is None:
                return False
            return str(first_idx) == str(last_idx)
        except Exception:
            return False


    def parse_multiframe_config(self):
        swath_val = str(self.config.get('swath', 0))
        l_burst_val = str(self.config.get('l_burst', 1))
        u_burst_val = str(self.config.get('u_burst', 9))
        
        if ',' in swath_val or ',' in l_burst_val or ',' in u_burst_val:
            swath_parts = [s.strip() for s in swath_val.split(',')]
            l_burst_parts = [s.strip() for s in l_burst_val.split(',')]
            u_burst_parts = [s.strip() for s in u_burst_val.split(',')]
            
            if len(swath_parts) != 2 or len(l_burst_parts) != 2 or len(u_burst_parts) != 2:
                raise ValueError(
                    f"Multi-frame config must have exactly 2 values for swath, l_burst, u_burst.\n"
                    f"Got: swath={swath_val}, l_burst={l_burst_val}, u_burst={u_burst_val}"
                )
            
            swaths = []
            for s in swath_parts:
                s_int = int(s)
                if s_int not in [1, 2, 3]:
                    raise ValueError(f"Multi-frame swath values must be 1, 2, or 3. Got: {s}")
                swaths.append(f"IW{s_int}")
            
            l_bursts = [int(l) for l in l_burst_parts]
            u_bursts = [int(u) for u in u_burst_parts]
            
            return (swaths, l_bursts, u_bursts)
        
        return (None, None, None)
    
    
    def extract_time_from_filename(self, filename):
        match = re.search(r'T(\d{6})_', filename)
        if match:
            return int(match.group(1))
        return None
    
    
    def assign_frames_by_time(self, files, swaths, l_bursts, u_bursts):
        if len(files) != 2:
            raise ValueError(f"Expected exactly 2 files for multi-frame, got {len(files)}")
        
        time1 = self.extract_time_from_filename(os.path.basename(files[0]))
        time2 = self.extract_time_from_filename(os.path.basename(files[1]))
        
        if time1 is None or time2 is None:
            raise ValueError(f"Could not extract time from filenames:\n  {files[0]}\n  {files[1]}")
        
        if time1 < time2:
            frame_order = [0, 1]
        else:
            frame_order = [1, 0]
        
        return {
            'file1': files[frame_order[0]],
            'swath_type1': swaths[frame_order[0]],
            'l_burst1': l_bursts[frame_order[0]],
            'u_burst1': u_bursts[frame_order[0]],
            'file2': files[frame_order[1]],
            'swath_type2': swaths[frame_order[1]],
            'l_burst2': l_bursts[frame_order[1]],
            'u_burst2': u_bursts[frame_order[1]],
        }
    
    
    def detect_multiframe_bursts(self, files, swath_name):
        print(f"  Auto-detecting burst ranges for multi-frame acquisition...")
        print(f"  Using swath: {swath_name}")
        
        temp_dir = os.path.join(self.proj_root, 'multiframe_detection_temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        detected_swaths = []
        detected_l_bursts = []
        detected_u_bursts = []
        temp_dims = []
        
        for i, zip_file in enumerate(files, 1):
            print(f"    Processing frame {i}: {os.path.basename(zip_file)}")
            
            temp_dim = os.path.join(temp_dir, f"frame{i}_{swath_name}_temp.dim")
            
            graph_file = self.get_graph_file('single_slice.xml')
            polarization = self.config.get('polarization', 'VV')
            cmd = [
                *self.gpt_base_cmd(),
                graph_file,
                f"-Pzip_file={zip_file}",
                f"-Ppolarization={polarization}",
                f"-Pswath_type={swath_name}",
                f"-Pl_burst=1",
                f"-Pu_burst=9",
                f"-Poutput_dim={temp_dim}"
            ]
            
            success = self.run_command(cmd, f"DetectFrame{i}_{swath_name}")
            
            if not success or not os.path.exists(temp_dim):
                raise RuntimeError(
                    f"ERROR: Frame {i} processing failed.\n"
                    f"Could not create: {temp_dim}"
                )
            
            first_burst, last_burst, _ = self.get_burst_range(temp_dim)
            
            if first_burst is None or last_burst is None:
                raise RuntimeError(
                    f"ERROR: Frame {i} - could not extract burst range from {temp_dim}"
                )
            
            detected_swaths.append(swath_name)
            detected_l_bursts.append(int(first_burst))
            detected_u_bursts.append(int(last_burst))
            temp_dims.append(temp_dim)
            
            print(f"      ✓ Detected: {swath_name}, bursts {first_burst}-{last_burst}")
        
        return (detected_swaths, detected_l_bursts, detected_u_bursts, temp_dims)


    def prefer_safe_product(self, product_path):
        """Use a matching .SAFE directory for slice assembly when one exists."""
        path = str(product_path).rstrip(os.sep)
        if path.endswith('.SAFE') and os.path.isdir(path):
            return path
        stem = self._product_key(path)
        parent = os.path.dirname(path)
        for cand in (
            os.path.join(parent, stem + '.SAFE'),
            os.path.join(os.path.dirname(parent), stem + '.SAFE'),
        ):
            if os.path.isdir(cand):
                return cand
        return path

    def _product_key(self, product_path):
        """Stem shared by product.zip and product.SAFE (also .../product.SAFE/manifest.safe)."""
        path = str(product_path).rstrip(os.sep)
        base = os.path.basename(path)
        if base.lower() == 'manifest.safe':
            base = os.path.basename(os.path.dirname(path))
        for suf in ('.zip', '.SAFE', '.safe'):
            if base.endswith(suf):
                return base[:-len(suf)]
        return base

    def get_zip_files(self, silent=False):
        """Products in input_data. Prefer a .SAFE directory over the matching .zip."""
        input_dir = self.config['input_data']
        zips = sorted(glob.glob(os.path.join(input_dir, '*.zip')))
        safes = sorted(
            p for p in glob.glob(os.path.join(input_dir, '*.SAFE'))
            if os.path.isdir(p)
        )
        by_key = {}
        for path in zips:
            by_key[self._product_key(path)] = path
        for path in safes:
            by_key[self._product_key(path)] = path
        products = [by_key[k] for k in sorted(by_key)]
        if not products:
            raise FileNotFoundError(
                f"No .zip files or .SAFE directories found in {input_dir}"
            )
        if not silent:
            n_safe = sum(1 for p in products if p.endswith('.SAFE'))
            n_zip = len(products) - n_safe
            print(f"\nFound {len(products)} products ({n_safe} .SAFE, {n_zip} .zip)")
        return products

    def get_zip_date_groups(self):
        """Group zip files by acquisition date."""
        zip_files = self.get_zip_files(silent=True)
        groups = {}
        for zf in zip_files:
            date = self.extract_date_from_zip(zf)
            groups.setdefault(date, []).append(zf)
        for date in groups:
            groups[date] = sorted(groups[date])
        return dict(sorted(groups.items()))

    def get_unique_zip_files(self):
        groups = self.get_zip_date_groups()
        unique_zips = []
        
        for date, files in groups.items():
            if len(files) == 1:
                unique_zips.append(files[0])
            else:
                earliest_file = None
                earliest_time = None
                
                for zf in files:
                    time = self.extract_time_from_filename(os.path.basename(zf))
                    if time is not None:
                        if earliest_time is None or time < earliest_time:
                            earliest_time = time
                            earliest_file = zf
                
                if earliest_file is None:
                    print(f"  ⚠ Warning: Could not extract time from duplicate date files. Using first file: {date}")
                    earliest_file = files[0]
                
                unique_zips.append(earliest_file)
        
        return unique_zips
    
    def extract_date_from_zip(self, zip_path):
        path = str(zip_path).rstrip(os.sep)
        basename = os.path.basename(path)
        if basename.lower() == 'manifest.safe':
            basename = os.path.basename(os.path.dirname(path))
        parts = basename.split('_')
        
        if len(parts) >= 6:
            date_str = parts[5].split('T')[0]
            return date_str
        else:
            raise ValueError(f"Cannot extract date from filename: {basename}")


    def step00_parse_baseline_metadata(self):
        print(f"\n{'='*80}")
        print(f"STEP 00: Parse Baseline Metadata")
        print(f"{'='*80}\n")
        
        metadata_baseline = os.path.join(self.proj_root, 'metadata_info', 'baselines')
        
        if not os.path.exists(metadata_baseline):
            print("  No baseline file found. Creating using SNAP InSAR-Overview...")
            
            import json
            from datetime import datetime, timedelta
            
            zip_files = self.get_unique_zip_files()
            
            xml_file = self.get_graph_file('step0_parse_baseline_metadata.xml')
            
            if not os.path.exists(xml_file):
                raise FileNotFoundError(f"XML graph not found: {xml_file}")
            
            metadata_info_dir = os.path.join(self.proj_root, 'metadata_info')
            os.makedirs(metadata_info_dir, exist_ok=True)
            
            json_output_file = os.path.join(metadata_info_dir, 'insar_stack')
            
            file_list = ','.join(zip_files)
            
            print(f"  Running SNAP InSAR-Overview operator...")
            
            cmd_baseline = self.gpt_base_cmd(include_cache=False) + [
                xml_file,
                f"-Pinput_zip_files={file_list}",
                f"-Pjson_output_file={json_output_file}"
            ]
            
            self.run_command(cmd_baseline, "Baseline Calculation")
            
            if not os.path.exists(json_output_file):
                print(f"  ERROR: Baseline calculation failed - JSON not created")
                return False
            
            print(f"  ✓ JSON created: {json_output_file}")
            
            with open(json_output_file, 'r') as f:
                baseline_json = json.load(f)
            
            reference = baseline_json.get('reference', {})
            product_name = reference.get('product_name', '')
            
            parts = product_name.split('_')
            if len(parts) < 6:
                raise ValueError(f"Invalid product name format: {product_name}")
            
            datetime_str = parts[5]
            self.master_date = datetime_str.split('T')[0]
            
            print(f"  Master date: {self.master_date}")
            
            secondaries = baseline_json.get('secondary', [])
            
            zip_dates = {}
            for zf in zip_files:
                date = self.extract_date_from_zip(zf)
                zip_dates[date] = zf
            
            master_dt = datetime.strptime(self.master_date, '%Y%m%d')
            baseline_rows = []
            used_dates = set([self.master_date])
            
            for sec in secondaries:
                insar_info = sec.get('insar_overview', {})
                temp_baseline = insar_info.get('temporal_baseline_days', 0)
                perp_baseline = insar_info.get('perpendicular_baseline_m', 0)
                
                sec_date = None
                min_diff = float('inf')
                
                for sign in [1, -1]:
                    sec_dt_approx = master_dt + timedelta(days=sign * abs(temp_baseline))
                    
                    for zip_date in zip_dates.keys():
                        if zip_date in used_dates:
                            continue
                        
                        zip_dt = datetime.strptime(zip_date, '%Y%m%d')
                        diff = abs((zip_dt - sec_dt_approx).days)
                        if diff < min_diff:
                            min_diff = diff
                            sec_date = zip_date
                
                if sec_date and min_diff <= 1:
                    used_dates.add(sec_date)
                    
                    sec_dt = datetime.strptime(sec_date, '%Y%m%d')
                    actual_temp_baseline = (sec_dt - master_dt).days
                    perp_baseline_int = int(perp_baseline)
                    
                    self.baseline_data[sec_date] = {
                        'temporal': actual_temp_baseline,
                        'perpendicular': perp_baseline_int
                    }
                    
                    baseline_rows.append((self.master_date, sec_date, actual_temp_baseline, perp_baseline_int))
            
            self.baseline_data[self.master_date] = {'temporal': 0, 'perpendicular': 0}
            
            baselines_dest = os.path.join(metadata_info_dir, 'baselines')
            with open(baselines_dest, 'w') as f:
                f.write(f"{self.master_date} {self.master_date} 0 0\n")
                for row in baseline_rows:
                    f.write(f"{row[0]} {row[1]} {row[2]} {row[3]}\n")
            
            print(f"  ✓ Created baselines: {baselines_dest}")
            print(f"    {len(baseline_rows) + 1} baseline rows (including master)")
            
            snap_master = self.master_date
            config_master = str(self.config.get('master', '')).strip()
            
            if self.insar_method == 1 and config_master and config_master != snap_master:
                if config_master in self.baseline_data:
                    print(f"\n  ℹ SBAS Re-anchoring: Config master ({config_master}) differs from SNAP master ({snap_master})")
                    print(f"  Re-computing baselines relative to config master: {config_master}")
                    
                    config_master_temporal = self.baseline_data[config_master]['temporal']
                    config_master_perpendicular = self.baseline_data[config_master]['perpendicular']
                    
                    new_baseline_data = {}
                    for date, vals in self.baseline_data.items():
                        if date == config_master:
                            new_baseline_data[date] = {'temporal': 0, 'perpendicular': 0}
                        else:
                            new_baseline_data[date] = {
                                'temporal': vals['temporal'] - config_master_temporal,
                                'perpendicular': vals['perpendicular'] - config_master_perpendicular
                            }
                    
                    self.baseline_data = new_baseline_data
                    self.master_date = config_master
                    
                    with open(baselines_dest, 'w') as f:
                        f.write(f"{self.master_date} {self.master_date} 0 0\n")
                        for date in sorted(self.baseline_data.keys()):
                            if date != self.master_date:
                                vals = self.baseline_data[date]
                                f.write(f"{self.master_date} {date} {vals['temporal']} {vals['perpendicular']}\n")
                    
                    print(f"  ✓ Re-anchored baselines to config master: {self.master_date}")
                    print(f"  ✓ Updated baselines file: {baselines_dest}")
                else:
                    print(f"  ⚠ Warning: Config master_date '{config_master}' not found in SNAP baseline data")
                    print(f"  Using SNAP-detected master: {snap_master}")
            
            center_time_raw = datetime_str.split('T')[1] if 'T' in datetime_str else '000000'
            center_time = f"{center_time_raw[0:2]}:{center_time_raw[2:4]}:{center_time_raw[4:6]}"
            orbit = self.config.get('orbit', 'Ascending')
            heading = '-1.268759891620288e+01' if orbit.lower() == 'ascending' else '190'
            
            metadata_dest = os.path.join(metadata_info_dir, 'metadata')
            with open(metadata_dest, 'w') as f:
                f.write(f"master = {self.master_date}\n")
                f.write(f"Orbit: {orbit}\n")
                f.write(f"heading={heading}\n")
                f.write(f"center_time={center_time}\n")
                f.write(f"Total images: {len(self.baseline_data)}\n")
                f.write(f"\nBaseline data:\n")
                f.write(f"Date\t\tTemporal\tPerpendicular\n")
                for date in sorted(self.baseline_data.keys()):
                    f.write(f"{date}\t{self.baseline_data[date]['temporal']}\t\t{self.baseline_data[date]['perpendicular']}\n")
            
            print(f"  ✓ Created metadata: {metadata_dest}")
            
        else:
            print(f"  Baseline file exists: {metadata_baseline}")
            print(f"  Loading baselines from file (skipping .xml processing)...\n")
            
            with open(metadata_baseline, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split()
                    if len(parts) >= 4:
                        master = parts[0]
                        slave = parts[1]
                        temporal = int(parts[2])
                        perpendicular = int(parts[3])
                        
                        if self.master_date is None:
                            self.master_date = master
                        
                        self.baseline_data[slave] = {
                            'temporal': temporal,
                            'perpendicular': perpendicular
                        }

        metadata_info_dir = os.path.join(self.proj_root, 'metadata_info')
        baselines_ps_path = os.path.join(metadata_info_dir, 'baselines_ps')
        baselines_sbas_path = os.path.join(metadata_info_dir, 'baselines_sbas')
        source_baselines = os.path.join(metadata_info_dir, 'baselines')

        if not os.path.exists(baselines_ps_path) and os.path.exists(source_baselines):
            shutil.copy2(source_baselines, baselines_ps_path)
            print(f"  ✓ Created baselines_ps: {baselines_ps_path}")

        if self.insar_method == 1:
            if os.path.exists(baselines_ps_path):
                self.load_baseline_file(baselines_ps_path)
                print(f"  ✓ Loaded baselines_ps for SBAS filtering: {baselines_ps_path}")
            else:
                self.load_baseline_file(source_baselines)
                print(f"  ✓ Loaded baselines for SBAS filtering: {source_baselines}")
        
        print(f"  Master date: {self.master_date}")
        print(f"  Total slaves: {len(self.baseline_data)}")
        
        config_master = str(self.config.get('master', '')).strip()
        if config_master != self.master_date:
            if config_master:
                print(f"  ℹ Updating config: master={config_master} → {self.master_date}")
            else:
                print(f"  ℹ Setting config: master={self.master_date}")
            self.update_config_file('master', self.master_date)
            self.config['master'] = self.master_date
        
        metadata_file = os.path.join(metadata_info_dir, 'metadata')
        if not os.path.exists(metadata_file):
            orbit = self.config.get('orbit', 'Ascending')
            heading = '-1.268759891620288e+01' if orbit.lower() == 'ascending' else '190'
            
            center_time = '00:00:00'
            zip_files = self.get_zip_files()
            master_zip = None
            for zf in zip_files:
                if self.master_date in zf:
                    master_zip = zf
                    break
            
            if master_zip:
                try:
                    basename = os.path.basename(master_zip)
                    if '_' in basename:
                        parts = basename.split('_')
                        for part in parts:
                            if len(part) == 15 and part[8] == 'T':
                                datetime_str = part
                                center_time_raw = datetime_str.split('T')[1]
                                center_time = f"{center_time_raw[0:2]}:{center_time_raw[2:4]}:{center_time_raw[4:6]}"
                                break
                except:
                    pass
            
            with open(metadata_file, 'w') as f:
                f.write(f"master = {self.master_date}\n")
                f.write(f"Orbit: {orbit}\n")
                f.write(f"heading={heading}\n")
                f.write(f"center_time={center_time}\n")
                f.write(f"Total images: {len(self.baseline_data) + 1}\n")
                f.write(f"\nBaseline data:\n")
                f.write(f"Date\t\tTemporal\tPerpendicular\n")
                f.write(f"{self.master_date}\t0\t\t0\n")
                for date in sorted(self.baseline_data.keys()):
                    if date != self.master_date:
                        f.write(f"{date}\t{self.baseline_data[date]['temporal']}\t\t{self.baseline_data[date]['perpendicular']}\n")
            
            print(f"  ✓ Created metadata: {metadata_file}")
        
        baselines_ps_path = os.path.join(metadata_info_dir, 'baselines_ps')
        using_existing_baselines = os.path.exists(baselines_ps_path)
        
        if using_existing_baselines:
            print(f"  ✓ Step 00 complete (using existing baselines)\n")
        else:
            zip_files = self.get_zip_files()
            master_zip_found = False
            for zf in zip_files:
                if self.master_date in zf:
                    self.master_zip = zf
                    master_zip_found = True
                    break
            
            if not master_zip_found:
                raise FileNotFoundError(f"Cannot find .zip or .SAFE product for master date {self.master_date}")
            
            print(f"  Master zip: {os.path.basename(self.master_zip)}")
            print(f"  ✓ Step 00 complete\n")

        sbas_pairs_file = os.path.join(self.proj_root, 'metadata_info', 'sbas_pairs.txt')
        if os.path.isfile(sbas_pairs_file):
            self._log_release_step00_summary()
        
        return True
    

    def create_sbas_pairs_and_network(self, both_network_plots=False):
        if not SBASPairBuilder:
            print("ERROR: lib.sbas_pair_builder module not found")
            return False
        
        metadata_info_dir = os.path.join(self.proj_root, 'metadata_info')
        baselines_file = os.path.join(metadata_info_dir, 'baselines')
        
        if not os.path.exists(baselines_file):
            print(f"WARNING: Baselines file not found: {baselines_file}")
            print(f"Auto-creating baselines using step00_parse_baseline_metadata()...")
            if not self.step00_parse_baseline_metadata():
                print("ERROR: Failed to create baselines file")
                return False
        
        builder = SBASPairBuilder(
            baselines_file, self.proj_root, self.config,
            self.insar_method, self.insar_target)
        
        if not builder.generate_sbas_pairs():
            return False
        
        if both_network_plots:
            if not builder.generate_both_network_plots():
                print("WARNING: Failed to generate one or both network plots")
                return False
        elif not builder.generate_network_plot():
            print("WARNING: Failed to generate network plot")
            return False
        
        sbas_pairs_file = os.path.join(self.proj_root, 'metadata_info', 'sbas_pairs.txt')
        if os.path.exists(sbas_pairs_file):
            self.date_pairs = []
            with open(sbas_pairs_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parts = line.split()
                        if len(parts) >= 2:
                            self.date_pairs.append((parts[0], parts[1]))

        self._log_release_step00_summary()
        return True
    

    def step00_create_baselines_and_network(self):
        print(f"\n{'='*80}")
        print(f"STEP 00: Create Baselines and SBAS Network")
        print(f"{'='*80}")
        
        if not self.step00_parse_baseline_metadata():
            return False
        
        print(f"\n{'='*80}")
        print(f"STEP 00b: Generate SBAS Pairs and Network")
        print(f"{'='*80}")
        
        if not self.create_sbas_pairs_and_network(both_network_plots=True):
            print("ERROR: Failed to create SBAS pairs and network")
            return False

        self.update_config_file('start_step', '1')
        
        print(f"\n  ✓ Step 00 (baselines mode) complete\n")
        return True
    

    def step01_aoi_and_swath_config(self):
        print(f"\n{'='*80}")
        print("STEP 01: AOI & Swath configuration")
        print(f"{'='*80}")
        clip_roi = self.config.get('clip_roi', '').strip()
        if not clip_roi:
            print("\n  ℹ No clip_roi in config — AOI auto-detection skipped.")
            print("  Use Step 2 to prepare slices with your current swath/burst settings.\n")
            self.swaths_to_process = self.parse_swath_option()
            return True
        if not self.config.get('burst_check', True):
            print("\n  ℹ burst_check=False — skipping AOI SNAP re-scan (config unchanged).")
            self.swaths_to_process = self.parse_swath_option()
            print(f"  ✓ Processing swaths: {', '.join(self.swaths_to_process)}\n")
            return True
        clip_roi = self.normalize_clip_roi(clip_roi)
        valid_swaths = self.aoi_swath_check(['IW1', 'IW2', 'IW3'], clip_roi)
        if valid_swaths is None:
            sys.exit(0)
        if not valid_swaths:
            raise RuntimeError("No swath overlaps AOI. Adjust clip_roi.")
        self._aoi_checked = True
        aoi_dir = os.path.join(self.proj_root, 'aoi')
        if os.path.isdir(aoi_dir):
            print(f"\n  ℹ Cleaning up AOI test folder\n    {aoi_dir}")
            shutil.rmtree(aoi_dir, ignore_errors=True)
        self.swaths_to_process = self.parse_swath_option()
        print("\n  ⚠ Swath configuration updated.")
        print("  ✓ burst_check=False is set in the config (uses detected burst parameters).")
        print("  Next: run Step 2 — Prepare Slices — to build IW*/data/*/*_Slice.dim\n")
        return True

    def get_backgeo_dem_model(self):
        """Resolve DEM name from config for SNAP ops (-PdemName / -Pdem_name_model).

        Prefers ``demName``, then ``dem_name``. Empty or missing → Copernicus 30m Global DEM.
        """
        dem_raw = self.config.get('demName')
        if dem_raw is None or str(dem_raw).strip() == '':
            dem_raw = self.config.get('dem_name')
        dem_model = str(dem_raw).strip() if dem_raw is not None else ''
        if not dem_model:
            dem_model = 'Copernicus 30m Global DEM'
        return dem_model

    def _merge_addband_graph_for_swath_layout(self, is_multi_swath):
        """Step 12 graph: multi-swath merge needs elevation re-added via merge_addband_mswath.xml."""
        if is_multi_swath:
            graph = self.get_graph_file('merge_addband_mswath.xml')
            extra_params = [f"-Pdem_name_model={self.get_backgeo_dem_model()}"]
            graph_label = 'merge_addband_mswath.xml'
        else:
            graph = self.get_graph_file('merge_addband.xml')
            extra_params = []
            graph_label = 'merge_addband.xml'
        return graph, extra_params, graph_label

    def step02_prepare_slices_outer(self):
        if self.is_check_only() or self.is_ps_mode():
            label = (
                "check_only=True"
                if self.is_check_only()
                else "PS mode (insar_method=2)"
            )
            print(f"\n{'='*80}")
            print(f"{label} — loading sbas_pairs.txt to restrict dates (Step 02) and pair list (later steps)")
            print(f"{'='*80}")
            try:
                if not self.load_sbas_pairs():
                    return False
            except FileNotFoundError as e:
                print(f"✗ {label} requires valid sbas_pairs.txt: {e}")
                return False
        
        self.swaths_to_process = self.parse_swath_option()
        self._swath_updated = False
        current_swaths = list(self.swaths_to_process)
        swath_index = 0
        
        while swath_index < len(current_swaths):
            swath_name = current_swaths[swath_index]
            
            print(f"\n{'='*80}")
            print(f"Processing Swath: {swath_name}")
            print(f"{'='*80}")
            
            if not self.step02_prepare_slices(swath_name):
                print(f"✗ Step 02 failed for {swath_name}")
                return False
            
            if self._swath_updated:
                print(f"\n  ⚠ Swath configuration updated. Restarting Step 02 with new swath list...")
                self._swath_updated = False
                current_swaths = list(self.swaths_to_process)
                swath_index = 0
            else:
                swath_index += 1
        
        print(f"\n✓ Step 02 complete for all swaths: {', '.join(self.swaths_to_process)}")
        return True


    def step02_prepare_slices(self, swath_name):
        """Prepare slices: IW*/data/<date>/<date>_Slice.dim (Step 2 worker)."""
        
        if swath_name not in self.swaths_to_process:
            print(f"  ⏩ Skipping {swath_name} (not in updated swath selection)")
            return True

        swath_root = os.path.join(self.proj_root, swath_name)
        data_root = os.path.join(swath_root, 'data')
        os.makedirs(data_root, exist_ok=True)
        
        date_groups = self.get_zip_date_groups()
        if self.selected_dates:
            date_groups = {d: files for d, files in date_groups.items() if d in self.selected_dates}
        
        existing_dates = []
        if os.path.exists(data_root):
            for date in date_groups.keys():
                dim_path = os.path.join(data_root, date, f"{date}_Slice.dim")
                if os.path.exists(dim_path):
                    existing_dates.append(date)
        
        if len(date_groups) > 0 and len(existing_dates) == len(date_groups) and not getattr(self, '_reprocess_first_only', False):
            burst_check_enabled = self.config.get('burst_check', True)
            
            if burst_check_enabled:
                clip_roi = self.config.get('clip_roi', '').strip()
                if clip_roi and not self._aoi_checked:
                    swath_config = self.get_swath_config_value()
                    
                    swath_candidates = ['IW1', 'IW2', 'IW3']

                    valid_swaths = self.aoi_swath_check(swath_candidates, clip_roi)
                    self._aoi_checked = True

                    if valid_swaths is None:
                        sys.exit(0)

                    if not valid_swaths:
                        raise RuntimeError("No swath overlaps AOI. Choose different area.")

                    if not self.config.get('burst_check', True):
                        self._swath_updated = True
                        
                        if swath_name in self.swaths_to_process:
                            print(f"  ✓ {swath_name} will be processed after restart")
                        else:
                            print(f"  Skipping {swath_name} (not in updated swath selection)")
                        return True

                    new_swath = swath_config
                    if set(valid_swaths) == {'IW1'}:
                        new_swath = 1
                    elif set(valid_swaths) == {'IW2'}:
                        new_swath = 2
                    elif set(valid_swaths) == {'IW3'}:
                        new_swath = 3
                    elif set(valid_swaths) == {'IW1', 'IW2'}:
                        new_swath = 12
                    elif set(valid_swaths) == {'IW2', 'IW3'}:
                        new_swath = 23
                    elif set(valid_swaths) == {'IW1', 'IW2', 'IW3'}:
                        new_swath = 0

                    if new_swath != swath_config:
                        self.update_swath_in_config(new_swath)
                        self._swath_updated = True

                        if swath_name in self.swaths_to_process:
                            print(f"  ✓ {swath_name} will be processed after restart")
                        else:
                            print(f"  Skipping {swath_name} (not in updated swath selection)")
                        return True
                    
                    aoi_dir = os.path.join(self.proj_root, 'aoi')
                    if os.path.isdir(aoi_dir):
                        print(f"\n  ℹ Cleaning up AOI test folder")
                        print(f"    Files will be reprocessed with proper graph (single_slice.xml)")
                        shutil.rmtree(aoi_dir, ignore_errors=True)
                    
                    existing_dates = []
                    if os.path.exists(data_root):
                        for date in date_groups.keys():
                            dim_path = os.path.join(data_root, date, f"{date}_Slice.dim")
                            if os.path.exists(dim_path):
                                existing_dates.append(date)
                    
                    if len(existing_dates) < len(date_groups):
                        if len(existing_dates) == 0:
                            print(f"\n  ℹ AOI check complete, but no slices exist yet")
                        else:
                            print(f"\n  ℹ AOI check complete, {len(existing_dates)}/{len(date_groups)} slices exist")
                        print(f"  Proceeding to Step 02 (slice export)...\n")
                        burst_check_enabled = False
                
                if burst_check_enabled and len(existing_dates) == len(date_groups) and len(existing_dates) > 0:
                    print(f"{'='*80}\n")
                    
                    sorted_dates = sorted(existing_dates)
                    first_date = sorted_dates[0]
                    first_dim = os.path.join(data_root, first_date, f"{first_date}_Slice.dim")
                    
                    if os.path.exists(first_dim):
                        first_burst_idx, last_burst_idx, _ = self.get_burst_range(first_dim)
                        
                        if first_burst_idx is not None and last_burst_idx is not None:
                            first_burst_idx = int(first_burst_idx)
                            last_burst_idx = int(last_burst_idx)
                            config_l_burst = int(self.config.get('l_burst', 1))
                            config_u_burst = int(self.config.get('u_burst', 9))
                            
                            print(f"  Config burst range:  l_burst={config_l_burst}, u_burst={config_u_burst}")
                            print(f"  Actual burst range:  firstBurstIndex={first_burst_idx}, lastBurstIndex={last_burst_idx}")
                            
                            if first_burst_idx == config_l_burst and last_burst_idx == config_u_burst:
                                print(f"  ✓ Burst range matches config")
                                self.config['burst_check'] = False
                                self.update_config_file('burst_check', 'False')
                                print(f"  ✓ Set burst_check=False")
                            else:
                                print(f"  ⚠ Burst range mismatch!")
                                print(f"  Updating config to match actual .dim files...")
                                
                                self.config['l_burst'] = first_burst_idx
                                self.config['u_burst'] = last_burst_idx
                                self.config['burst_check'] = False
                                
                                self.update_config_file('l_burst', first_burst_idx)
                                self.update_config_file('u_burst', last_burst_idx)
                                self.update_config_file('burst_check', 'False')
                                
                                print(f"  ✓ Updated config: l_burst={first_burst_idx}, u_burst={last_burst_idx}")
                                print(f"  ✓ Set burst_check=False")
                        else:
                            print(f"  ⚠ Could not parse burst range from {first_dim}")
                    else:
                        print(f"  ⚠ First .dim file not found: {first_dim}")
                    
                    if len(existing_dates) == len(date_groups) and len(existing_dates) > 0:
                        print(f"  ✓ All slices already exist (skipping processing)\n")
                        return True
            else:
                if len(existing_dates) == len(date_groups) and len(existing_dates) > 0:
                    print(f"  ⏩ {swath_name}: All slices already exist (skipping)")
                    return True

        print(f"\n{'='*80}")
        print(f"STEP 02: Prepare Slices - {swath_name}")
        print(f"{'='*80}")

        clip_roi = self.config.get('clip_roi', '').strip()
        
        if clip_roi:
            clip_roi = self.normalize_clip_roi(clip_roi)
        
        burst_check_enabled = self.config.get('burst_check', True)

        if clip_roi and burst_check_enabled and not self._aoi_checked:
            swath_config = self.get_swath_config_value()
            
            swath_candidates = ['IW1', 'IW2', 'IW3']

            valid_swaths = self.aoi_swath_check(swath_candidates, clip_roi)
            self._aoi_checked = True

            if valid_swaths is None:
                sys.exit(0)
            
            if not valid_swaths:
                raise RuntimeError("No swath overlaps AOI. Choose different area.")

            if not self.config.get('burst_check', True):
                self._swath_updated = True
                
                if swath_name in self.swaths_to_process:
                    print(f"  ✓ {swath_name} will be processed after restart")
                else:
                    print(f"  Skipping {swath_name} (not in updated swath selection)")
                return True

            new_swath = swath_config
            if set(valid_swaths) == {'IW1'}:
                new_swath = 1
            elif set(valid_swaths) == {'IW2'}:
                new_swath = 2
            elif set(valid_swaths) == {'IW3'}:
                new_swath = 3
            elif set(valid_swaths) == {'IW1', 'IW2'}:
                new_swath = 12
            elif set(valid_swaths) == {'IW2', 'IW3'}:
                new_swath = 23
            elif set(valid_swaths) == {'IW1', 'IW2', 'IW3'}:
                new_swath = 0

            if new_swath != swath_config:
                self.update_swath_in_config(new_swath)
                self._swath_updated = True

                if swath_name in self.swaths_to_process:
                    print(f"  ✓ {swath_name} will be processed after restart")
                else:
                    print(f"  Skipping {swath_name} (not in updated swath selection)")
                return True
            
            aoi_dir = os.path.join(self.proj_root, 'aoi')
            if os.path.isdir(aoi_dir):
                print(f"\n  ℹ Cleaning up AOI test folder")
                print(f"    Files will be reprocessed with proper graph (single_slice.xml)")
                shutil.rmtree(aoi_dir, ignore_errors=True)
            
            config_l_burst = self.config.get('l_burst')
            config_u_burst = self.config.get('u_burst')
            if config_l_burst is not None and config_u_burst is not None:
                self.config['burst_check'] = False
                self.update_config_file('burst_check', 'False')
                print(f"\n  ✓ Using detected burst parameters from AOI check")
                print(f"  ✓ Set burst_check=False\n")
        
        date_groups = self.get_zip_date_groups()
        if self.selected_dates:
            date_groups = {d: files for d, files in date_groups.items() if d in self.selected_dates}
        
        swath_raw = self.config['swath']
        if isinstance(swath_raw, str) and ',' in swath_raw:
            swath_val = int(swath_raw.split(',')[0].strip())
        else:
            swath_val = int(swath_raw) if not isinstance(swath_raw, int) else swath_raw
        
        is_multiswath = swath_val not in [1, 2, 3]
        process_first_only = (
            (is_multiswath and self.config.get('burst_check', True) and not self.is_check_only())
            or getattr(self, '_reprocess_first_only', False)
        )
        
        if process_first_only:
            sorted_dates = sorted(date_groups.keys())
            if sorted_dates:
                first_date = sorted_dates[0]
                date_groups = {first_date: date_groups[first_date]}

        for date, files in date_groups.items():
            date_dir = os.path.join(data_root, str(date))
            os.makedirs(date_dir, exist_ok=True)
            output_dim = os.path.join(date_dir, f"{date}_Slice.dim")

            if os.path.exists(output_dim):
                print(f"  ⏩ Skipping {date} (output already exists)")
                print(f"     Path: {output_dim}")
                continue
            
            if not os.path.exists(date_dir):
                print(f"\n  Processing {date} (date directory doesn't exist)")
            elif not os.path.exists(output_dim):
                print(f"\n  Processing {date} (output file doesn't exist)")
            print(f"     Expected output: {output_dim}")

            l_burst_config = str(self.config.get('l_burst', 1))
            is_multiframe_config = ',' in l_burst_config
            is_singleframe_data = len(files) == 1
            
            if not self.config.get('burst_check', True) and is_multiframe_config and is_singleframe_data:
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
                    if self.config.get('burst_check', True):
                        print(f"  Multi-frame acquisition detected (no config defined)")
                        swaths_list, l_bursts_list, u_bursts_list, temp_dims = self.detect_multiframe_bursts(files, swath_name)
                        
                        swath_str = ','.join([s.replace('IW', '') for s in swaths_list])
                        l_burst_str = ','.join(map(str, l_bursts_list))
                        u_burst_str = ','.join(map(str, u_bursts_list))
                        
                        self.update_config_file('swath', swath_str)
                        self.update_config_file('l_burst', l_burst_str)
                        self.update_config_file('u_burst', u_burst_str)
                        
                        self.config['swath'] = swath_str
                        self.config['l_burst'] = l_burst_str
                        self.config['u_burst'] = u_burst_str
                        
                        print(f"    ✓ Auto-detected and updated config:")
                        print(f"      swath={swath_str}")
                        print(f"      l_burst={l_burst_str}")
                        print(f"      u_burst={u_burst_str}")
                        
                        temp_dir = os.path.join(self.proj_root, 'multiframe_detection_temp')
                        if os.path.isdir(temp_dir):
                            shutil.rmtree(temp_dir, ignore_errors=True)
                    else:
                        raise ValueError(
                            f"ERROR: Multi-frame acquisition detected but config not defined.\n"
                            f"Please add multi-frame parameters to config:\n"
                            f"  swath=2,3 (or 1,1 etc.)\n"
                            f"  l_burst=1,5\n"
                            f"  u_burst=2,6\n"
                            f"Or enable burst_check=True with clip_roi for auto-detection."
                        )
                
                frame_params = self.assign_frames_by_time(files, swaths_list, l_bursts_list, u_bursts_list)
                file1 = self.prefer_safe_product(frame_params['file1'])
                file2 = self.prefer_safe_product(frame_params['file2'])
                
                graph_file = self.get_graph_file('slice_assembly.xml')
                polarization = self.config.get('polarization', 'VV')
                cmd = [
                    *self.gpt_base_cmd(),
                    graph_file,
                    f"-Pfile1={file1}",
                    f"-Pfile2={file2}",
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
                
                print(f"  Multi-frame processing (time-based ordering):")
                print(f"    File1 (earliest): {os.path.basename(file1)} → {frame_params['swath_type1']} bursts {frame_params['l_burst1']}-{frame_params['u_burst1']}")
                print(f"    File2 (later):    {os.path.basename(file2)} → {frame_params['swath_type2']} bursts {frame_params['l_burst2']}-{frame_params['u_burst2']}")
            else:
                if self.config.get('burst_check', True):
                    graph_file = self.get_graph_file('single_slice.xml')
                    polarization = self.config.get('polarization', 'VV')
                    cmd = [
                        *self.gpt_base_cmd(),
                        graph_file,
                        f"-Pzip_file={files[0]}",
                        f"-Ppolarization={polarization}",
                        f"-Pswath_type={swath_name}",
                        f"-Pl_burst=1",
                        f"-Pu_burst=9",
                        f"-Poutput_dim={output_dim}"
                    ]
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
            
            if self.config.get('burst_check', True):
                l_burst_config_value = str(self.config.get('l_burst', '1'))
                is_multiframe_config = ',' in l_burst_config_value
                
                print(f"\n  Analyzing burst range from first .dim file...")
                first_burst_idx, last_burst_idx, _ = self.get_burst_range(output_dim)
                
                if first_burst_idx is not None and last_burst_idx is not None:
                    first_burst_idx = int(first_burst_idx)
                    last_burst_idx = int(last_burst_idx)
                    
                    if is_multiframe_config:
                        print(f"    Config has multi-frame settings: l_burst={l_burst_config_value}, u_burst={self.config.get('u_burst')}")
                        print(f"    Actual single-frame burst range: firstBurstIndex={first_burst_idx}, lastBurstIndex={last_burst_idx}")
                        print(f"    ⚠ Replacing multi-frame config with single-frame values...")
                    else:
                        config_l_burst = int(l_burst_config_value)
                        config_u_burst = int(self.config.get('u_burst', 9))
                        print(f"    Config burst range:  l_burst={config_l_burst}, u_burst={config_u_burst}")
                        print(f"    Actual burst range:  firstBurstIndex={first_burst_idx}, lastBurstIndex={last_burst_idx}")
                        
                        if first_burst_idx == config_l_burst and last_burst_idx == config_u_burst:
                            print(f"    ✓ Burst range matches config")
                        else:
                            print(f"    ⚠ Burst range mismatch - updating config...")
                    
                    self.config['l_burst'] = first_burst_idx
                    self.config['u_burst'] = last_burst_idx
                    self.update_config_file('l_burst', first_burst_idx)
                    self.update_config_file('u_burst', last_burst_idx)
                    
                    swath_raw = self.config['swath']
                    if isinstance(swath_raw, str) and ',' in swath_raw:
                        single_swath = int(swath_raw.split(',')[0].strip())
                        self.config['swath'] = single_swath
                        self.update_config_file('swath', single_swath)
                        print(f"    ✓ Updated config: swath={single_swath}, l_burst={first_burst_idx}, u_burst={last_burst_idx}")
                    else:
                        print(f"    ✓ Updated config: l_burst={first_burst_idx}, u_burst={last_burst_idx}")
                    
                    self.config['burst_check'] = False
                    self.update_config_file('burst_check', 'False')
                    print(f"    ✓ Set burst_check=False\n")
                else:
                    print(f"    ⚠ Could not parse burst range from {output_dim}\n")

        if self.selected_dates is None and self.config.get('burst_check', True):
            self.select_burst_group(swath_name)

        print(f"\n  ✓ Step 02 complete for {swath_name}\n")
        return True


