"""StaMPS path helpers"""
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


class StampsHelpersMixin:

    def _stamps_read_master_bridge_rows(self):
        """Full-network SBAS pairs that include ``master_date`` (bridged / skipped stacks in Step 3)."""
        m = self.master_date
        if not m:
            return []
        bridges = []
        for row in self.read_sbas_pair_rows():
            d1, d2 = row['date1'], row['date2']
            if d1 != m and d2 != m:
                continue
            bridges.append(row)
        return bridges

    @staticmethod
    def _stamps_bridge_slave_date(master_date, bridge_row):
        """Return the SBAS epoch in a master-including pair that is not the master."""
        d1, d2 = bridge_row['date1'], bridge_row['date2']
        if d1 == master_date != d2:
            return d2
        if d2 == master_date != d1:
            return d1
        return None

    def _stamps_bridge_unique_dates(self, bridge_rows):
        """Dates (excluding master) that appear on master-involving SBAS pairs — Step 27 bridge trigger set."""
        m = self.master_date
        u = set()
        for row in bridge_rows:
            s = self._stamps_bridge_slave_date(m, row)
            if s:
                u.add(s)
        return u

    @staticmethod
    def _stamps_diff_basename_aliases(pair_name):
        """Canonical and reversed diff0 stem (SNAP may write either DATE1_DATE2 or DATE2_DATE1)."""
        parts = pair_name.split('_')
        if len(parts) != 2:
            return [pair_name]
        a, b = parts[0], parts[1]
        rev = f"{b}_{a}"
        return [pair_name, rev] if rev != pair_name else [pair_name]

    def _stamps_collect_diff_triplet(self, diff_src, bridge_pair_name):
        """Resolve .base/.diff/.diff.par in diff0 for either date order."""
        for stem in self._stamps_diff_basename_aliases(bridge_pair_name):
            trio = tuple(
                os.path.join(diff_src, f"{stem}.{suf}")
                for suf in ('base', 'diff', 'diff.par')
            )
            if all(os.path.isfile(p) for p in trio):
                return stem, trio
        return None, None

    def _ensure_stamps_sbas_primary_pair_folder(
            self, temp_dir, target_pair_dir, pair_name, date1, date2):
        """Move IFG diff trio; copy RSLCs (keep temp/rslc for bridge copies); copy master .rslc.par."""
        os.makedirs(target_pair_dir, exist_ok=True)
        diff_src = os.path.join(temp_dir, 'diff0')
        rslc_src = os.path.join(temp_dir, 'rslc')

        stem, trio = self._stamps_collect_diff_triplet(diff_src, pair_name)
        if trio is None:
            for fname in [f"{pair_name}.base", f"{pair_name}.diff", f"{pair_name}.diff.par"]:
                src = os.path.join(diff_src, fname)
                if os.path.exists(src):
                    shutil.move(src, os.path.join(target_pair_dir, os.path.basename(src)))
        else:
            for src in trio:
                base = os.path.basename(src)
                dst_base = base
                if stem != pair_name and base.startswith(f"{stem}."):
                    dst_base = base.replace(f"{stem}.", f"{pair_name}.", 1)
                dst = os.path.join(target_pair_dir, dst_base)
                if os.path.isfile(src):
                    shutil.move(src, dst)

        for fname in [f"{date1}.rslc", f"{date1}.rslc.par", f"{date2}.rslc", f"{date2}.rslc.par"]:
            src = os.path.join(rslc_src, fname)
            dst = os.path.join(target_pair_dir, fname)
            if os.path.isfile(src):
                shutil.copy2(src, dst)

        mast_par = f"{self.master_date}.rslc.par"
        src_m = os.path.join(rslc_src, mast_par)
        dst_m = os.path.join(target_pair_dir, mast_par)
        if os.path.isfile(src_m):
            shutil.copy2(src_m, dst_m)

    def _rename_stamps_geo_elevation_dem(self, geo_dir):
        """Rename SNAP ``elevation_dem.rdc`` to ``{master}_dem.rdc`` for StaMPS/GAMMA."""
        if not geo_dir or not os.path.isdir(geo_dir):
            return
        m = self.master_date
        pairs = [
            ('elevation_dem.rdc', f'{m}.dem.rdc'),
            ('elevation_dem.rdc.par', f'{m}.dem.rdc.par'),
        ]
        for src_name, dst_name in pairs:
            src = os.path.join(geo_dir, src_name)
            dst = os.path.join(geo_dir, dst_name)
            if not os.path.isfile(src):
                continue
            if os.path.isfile(dst):
                print(f"  geo/{dst_name} already exists; leaving geo/{src_name}")
                continue
            os.rename(src, dst)
            print(f"  Renamed geo/{src_name} → geo/{dst_name}")

    def _finalize_stamps_sbas_geo(self, temp_dir, insar_root):
        geo_dst = os.path.join(insar_root, 'geo')
        geo_src = os.path.join(temp_dir, 'geo')
        if os.path.exists(geo_dst) or not os.path.exists(geo_src):
            if os.path.isdir(geo_dst):
                self._rename_stamps_geo_elevation_dem(geo_dst)
            return
        os.makedirs(geo_dst, exist_ok=True)
        for item in os.listdir(geo_src):
            src_file = os.path.join(geo_src, item)
            dst_file = os.path.join(geo_dst, item)
            if item == 'elevation_dem.rdc':
                dst_file = os.path.join(geo_dst, f"{self.master_date}.dem.rdc")
            elif item == 'elevation_dem.rdc.par':
                dst_file = os.path.join(geo_dst, f"{self.master_date}.dem.rdc.par")
            if os.path.isfile(src_file):
                shutil.copy2(src_file, dst_file)
        self._rename_stamps_geo_elevation_dem(geo_dst)

    def _finalize_stamps_ps_geo(self, insar_root):
        """After PS StaMPS export (method=2), normalize DEM names under ``INSAR_*/geo/``."""
        geo_dir = os.path.join(insar_root, 'geo')
        self._rename_stamps_geo_elevation_dem(geo_dir)

    def _export_stamps_sbas_bridge_folders_if_needed(
            self, stamps_root, temp_dir, proc_pair_name):
        """Create SMALL_BASELINES folders for SBAS pairs that include master, when ``temp`` still holds them."""
        if self.insar_method != 1 or self.insar_target != 2:
            return
        if self.is_check_only():
            return
        proc_parts = proc_pair_name.split('_')
        if len(proc_parts) != 2:
            return
        pd1, pd2 = proc_parts[0], proc_parts[1]
        bridges = getattr(self, '_stamps_master_bridge_rows', None) or []
        u_dates = getattr(self, '_stamps_bridge_trigger_dates', None) or set()
        done = getattr(self, '_stamps_bridge_pairs_written', None)
        if done is None:
            done = set()
            self._stamps_bridge_pairs_written = done

        if not bridges or not u_dates:
            return
        if pd1 not in u_dates and pd2 not in u_dates:
            return

        diff_src = os.path.join(temp_dir, 'diff0')
        rslc_src = os.path.join(temp_dir, 'rslc')
        if not os.path.isdir(diff_src) or not os.path.isdir(rslc_src):
            return

        m = self.master_date
        for row in bridges:
            bname = row['pair_name']
            if bname in done:
                continue
            slave = self._stamps_bridge_slave_date(m, row)
            if slave is None or slave not in (pd1, pd2):
                continue

            trio_stem, trio = self._stamps_collect_diff_triplet(diff_src, bname)
            if trio is None:
                continue

            dout = self.get_stamps_pair_dir(stamps_root, bname)
            os.makedirs(dout, exist_ok=True)
            for src in trio:
                base = os.path.basename(src)
                dst_base = base
                if trio_stem != bname and base.startswith(f"{trio_stem}."):
                    dst_base = base.replace(f"{trio_stem}.", f"{bname}.", 1)
                dst = os.path.join(dout, dst_base)
                shutil.copy2(src, dst)

            d_a, d_b = bname.split('_')
            for d in (d_a, d_b):
                for sfx in ('.rslc', '.rslc.par'):
                    fn = f"{d}{sfx}"
                    sp = os.path.join(rslc_src, fn)
                    if os.path.isfile(sp):
                        shutil.copy2(sp, os.path.join(dout, fn))

            print(f"  SBAS bridge StaMPS layout: {bname}/ (from export {proc_pair_name})")
            done.add(bname)

    def _prime_stamps_sbas_bridge_tracking(self, stamps_root):
        """Reset Step 27 SBAS bridge state: full-network master pairs + dirs already complete on disk."""
        if self.is_check_only():
            self._stamps_master_bridge_rows = []
            self._stamps_bridge_trigger_dates = set()
            self._stamps_bridge_pairs_written = set()
            print(
                "  check_only=True: SBAS StaMPS SMALL_BASELINES bridge-folder extraction disabled."
            )
            return
        self._stamps_master_bridge_rows = self._stamps_read_master_bridge_rows()
        self._stamps_bridge_trigger_dates = self._stamps_bridge_unique_dates(
            self._stamps_master_bridge_rows)
        written = set()
        for row in self._stamps_master_bridge_rows:
            bname = row['pair_name']
            dout = self.get_stamps_pair_dir(stamps_root, bname)
            if self.stamps_pair_outputs_exist(dout, bname, 1):
                written.add(bname)
        self._stamps_bridge_pairs_written = written
        nb = len(self._stamps_master_bridge_rows)
        if nb:
            print(
                f"  SBAS master-involving pairs (network): {nb}; "
                f"already on disk: {len(written)}; "
                f"bridge trigger dates: {sorted(self._stamps_bridge_trigger_dates)}"
            )
    
    def rearrange_stamps_output(self, temp_dir, insar_root, target_pair_dir, pair_name, is_master_pair):
        parts = pair_name.split('_')
        if len(parts) < 2:
            return False
        date1, date2 = parts[0], parts[1]

        stamps_root = os.path.dirname(insar_root.rstrip(os.sep))

        if self.insar_method == 1 and self.insar_target == 2:
            self._ensure_stamps_sbas_primary_pair_folder(
                temp_dir, target_pair_dir, pair_name, date1, date2)
            self._export_stamps_sbas_bridge_folders_if_needed(
                stamps_root, temp_dir, pair_name)
            diff_ok = os.path.isfile(os.path.join(target_pair_dir, f"{pair_name}.diff"))
            if not diff_ok:
                for fn in os.listdir(target_pair_dir):
                    if fn.endswith('.diff'):
                        diff_ok = True
                        break
            if not diff_ok:
                print(f"  ✗ rearrange_stamps_output: no .diff in {target_pair_dir}")
                return False
        else:
            os.makedirs(target_pair_dir, exist_ok=True)
            diff_src = os.path.join(temp_dir, 'diff0')
            for fname in [f"{pair_name}.base", f"{pair_name}.diff", f"{pair_name}.diff.par"]:
                src = os.path.join(diff_src, fname)
                if os.path.exists(src):
                    shutil.move(src, os.path.join(target_pair_dir, fname))
            rslc_src = os.path.join(temp_dir, 'rslc')
            for fname in [f"{date1}.rslc", f"{date1}.rslc.par", f"{date2}.rslc", f"{date2}.rslc.par"]:
                src = os.path.join(rslc_src, fname)
                if os.path.exists(src):
                    shutil.move(src, os.path.join(target_pair_dir, fname))

        self._finalize_stamps_sbas_geo(temp_dir, insar_root)

        if os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

        return True
    
    def list_pair_dirs(self, base_dir):
        if not os.path.exists(base_dir):
            return []
        
        pair_dirs = []
        for item in os.listdir(base_dir):
            item_path = os.path.join(base_dir, item)
            if os.path.isdir(item_path) and '_' in item:
                parts = item.split('_')
                if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                    if len(parts[0]) == 8 and len(parts[1]) == 8:
                        pair_dirs.append(item)
        
        return sorted(pair_dirs)
    
    def filter_pairs_for_check_only_mode(self, pair_names):
        if not self.is_check_only():
            return sorted(pair_names)
        if not pair_names:
            return []
        if not self.sbas_pairs:
            try:
                if not self.load_sbas_pairs():
                    return sorted(pair_names)
            except Exception:
                return sorted(pair_names)
            if not self.sbas_pairs:
                return sorted(pair_names)
        allow = {f"{p['date1']}_{p['date2']}" for p in self.sbas_pairs}
        out = [p for p in sorted(pair_names) if p in allow]
        if len(out) != len(pair_names):
            print(
                f"  check_only=True: pair scan {len(pair_names)} folder(s) → "
                f"{len(out)} SBAS-listed pair(s)"
            )
        return out
    
    def filter_pairs_for_ps_mode(self, pair_names, apply_ps_filter=True):
        if not apply_ps_filter or not self.is_ps_mode():
            return sorted(pair_names)
        if not pair_names:
            return []
        if not self.ps_selected_pair_names:
            if not self._ensure_ps_pairs_selected():
                return sorted(pair_names)
        allow = self.ps_selected_pair_names
        out = [p for p in sorted(pair_names) if p in allow]
        if len(out) != len(pair_names):
            print(
                f"  PS mode: pair scan {len(pair_names)} folder(s) → "
                f"{len(out)} PS-selected pair(s)"
            )
        return out

    def list_pair_dirs_check_only(self, base_dir, apply_ps_filter=True):
        pairs = self.filter_pairs_for_check_only_mode(self.list_pair_dirs(base_dir))
        if self.is_check_only():
            return pairs
        return self.filter_pairs_for_ps_mode(pairs, apply_ps_filter=apply_ps_filter)
    

    def get_burst_range(self, dim_path):
        """Return (firstBurstIndex, lastBurstIndex, subswath) from .dim metadata."""
        first_idx = None
        last_idx = None
        subswath = None
        try:
            with open(dim_path, 'r') as f:
                for line in f:
                    if 'subswath' in line:
                        m = re.search(r'subswath" type="ascii">(IW\d)<', line)
                        if m:
                            subswath = m.group(1)
                    if 'firstBurstIndex' in line:
                        m = re.search(r'firstBurstIndex" type="ascii">(\d+)<', line)
                        if m:
                            first_idx = m.group(1)
                    if 'lastBurstIndex' in line:
                        m = re.search(r'lastBurstIndex" type="ascii">(\d+)<', line)
                        if m:
                            last_idx = m.group(1)
                    if first_idx is not None and last_idx is not None and subswath is not None:
                        break
        except Exception:
            return None, None, None

        return first_idx, last_idx, subswath
    

