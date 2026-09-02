"""Multilook layout and Goldstein entry"""

import os
import shutil

class LayoutMixin:

    def build_step14_multilook_gpt_cmd(self, input_file, output_file):
        cmd = [
            *self.gpt_base_cmd(),
            'Multilook',
            f"-Ssource={input_file}",
        ]
        source_bands = self.get_multilook_source_bands_from_dim(input_file)
        if source_bands:
            cmd.append(f"-PsourceBands={source_bands}")
        else:
            print(
                f"  ⚠ No multilook sourceBands from {input_file} "
                f"(missing .data or only Phase* bands); Multilook runs without -PsourceBands"
            )
        cmd.extend([
            f"-PnRgLooks={self.range_looks}",
            '-t', output_file,
        ])
        return cmd
    
    def validate_ml_product_coh_i_grid_match(self, ml_dim_path, context='Multilook output'):
        """Return True if the first coh_*.hdr and first i_*.hdr share the same samples×lines (Step 14 gate)."""
        base = ml_dim_path[:-4] if ml_dim_path.lower().endswith('.dim') else ml_dim_path
        data_dir = base + '.data'
        if not os.path.isdir(data_dir):
            print(f"  ✗ {context}: missing product .data directory:\n    {data_dir}")
            return False
        try:
            names = os.listdir(data_dir)
        except OSError as e:
            print(f"  ✗ {context}: cannot list {data_dir}: {e}")
            return False
        coh_hdrs = sorted(x for x in names if x.startswith('coh_') and x.lower().endswith('.hdr'))
        i_hdrs = sorted(x for x in names if x.startswith('i_') and x.lower().endswith('.hdr'))
        if not coh_hdrs or not i_hdrs:
            print(f"  ✗ {context}: need at least one coh_*.hdr and one i_*.hdr in:\n    {data_dir}")
            return False
        ch = os.path.join(data_dir, coh_hdrs[0])
        ih = os.path.join(data_dir, i_hdrs[0])
        dc = self.parse_envi_hdr_samples_lines(ch)
        di = self.parse_envi_hdr_samples_lines(ih)
        if not dc or not di:
            print(f"  ✗ {context}: could not parse samples/lines from:\n    {ch} or {ih}")
            return False
        if dc == di:
            print(f"  coh/i grid ({context}): {coh_hdrs[0]} vs {i_hdrs[0]} → {dc[0]}×{dc[1]} (match)")
            return True
        print(f"  ✗ {context}: coh/i grid mismatch\n"
              f"    {coh_hdrs[0]} → {dc[0]}×{dc[1]}\n"
              f"    {i_hdrs[0]} → {di[0]}×{di[1]}")
        return False
    
    def _multilook_run_envi_coh_i_grid_check(self):
        if self.insar_target == 1:
            return True
        if self.insar_target == 2 and self.insar_method == 1:
            return True
        return False
    
    def cleanup_multilook_and_goldstein_output_roots(self, swath_name, use_merged, use_subset, merge_folder_name):
        """Remove entire Step 14 (multilook) and Step 18 (Goldstein) dated folder trees after a grid mismatch."""
        m = self.master_date
        pr = self.proj_root
        dirs = []
        if use_merged:
            if not merge_folder_name:
                print(f"  ⚠ Skipping merged-folder cleanup (merge folder name missing)")
                return
            mf = os.path.join(pr, merge_folder_name)
            if use_subset:
                dirs.extend([
                    os.path.join(mf, f'multi_intf_deb_mrg_ml_subset_{m}'),
                    os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_subset_{m}'),
                ])
            else:
                dirs.extend([
                    os.path.join(mf, f'multi_intf_deb_mrg_ml_{m}'),
                    os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_{m}'),
                ])
        else:
            if use_subset:
                dirs.extend([
                    os.path.join(pr, swath_name, f'multi_intf_deb_ml_subset_{m}'),
                    os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_subset_{m}'),
                ])
            else:
                dirs.extend([
                    os.path.join(pr, swath_name, f'multi_intf_deb_ml_{m}'),
                    os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_{m}'),
                ])
        if use_merged:
            dirs.extend([
                os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_shp_subset_{m}'),
                os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_shp_mask_subset_{m}'),
                os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_shp_{m}'),
                os.path.join(mf, f'multi_intf_deb_mrg_ml_flt_shp_mask_{m}'),
            ])
        else:
            dirs.extend([
                os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_shp_subset_{m}'),
                os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_shp_mask_subset_{m}'),
                os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_shp_{m}'),
                os.path.join(pr, swath_name, f'multi_intf_deb_ml_flt_shp_mask_{m}'),
            ])
        for d in dirs:
            if os.path.isdir(d):
                try:
                    shutil.rmtree(d)
                    print(f"  Removed: {d}")
                except OSError as e:
                    print(f"  ⚠ Failed to remove {d}: {e}")
    
    
    @staticmethod
    def _parse_int_config_value(raw, default):
        """Parse config int; treat missing/blank as *default*."""
        if raw is None:
            return default
        s = str(raw).strip()
        if s == '':
            return default
        try:
            return int(s)
        except ValueError:
            return default

    def _parse_int_config(self, key, default):
        return self._parse_int_config_value(self.config.get(key), default)

    def _range_looks_configured(self):
        """True when config ``Range`` is set (non-empty); controls Step 27 coreg/intf sources."""
        raw = self.config.get('Range', '')
        if raw is None:
            return False
        return str(raw).strip() != ''

    def get_msk_shp_path(self):
        """Shapefile path from config (`msk_shp` or legacy `shp_mask`). Empty if disabled."""
        for key in ('msk_shp', 'shp_mask'):
            raw = self.config.get(key)
            if raw is None:
                continue
            p = os.path.expanduser(str(raw).strip())
            if p:
                return p
        return ''
    
    def config_invert_mask(self):
        v = self.config.get('invert_mask')
        return str(v).strip().lower() in ('true', '1', 'yes', 'on')
    
    def geometry_name_from_msk_shp(self):
        p = self.get_msk_shp_path()
        if not p:
            return ''
        return f"{os.path.splitext(os.path.basename(p))[0]}_1"
    
    def _intf_base_dir_snaphu_from_subset_only(self, swath_name, lay_subset):
        """LiCSBAS SnaphuExport: use subset interferogram stacks (`multi_intf*_subset_*`), full-res."""
        if lay_subset['use_merged']:
            mf = lay_subset['merge_folder_name']
            if lay_subset['use_subset_intf']:
                return os.path.join(
                    self.proj_root, mf,
                    f'multi_intf_deb_mrg_subset_{self.master_date}')
            return os.path.join(
                self.proj_root, mf, f'multi_intf_deb_mrg_{self.master_date}')
        if lay_subset['use_subset_intf']:
            return os.path.join(
                self.proj_root, swath_name,
                f'multi_intf_deb_subset_{self.master_date}')
        return os.path.join(
            self.proj_root, swath_name, f'multi_intf_deb_{self.master_date}')
    
    def _snaphu_intf_stack_suffix_from_subset_layout(self, lay_subset):
        if lay_subset['use_merged']:
            if lay_subset['use_subset_intf']:
                return '_Stack_esd_mask_deb_mmifg_mrg_subset.dim'
            return '_Stack_esd_mask_deb_mmifg_mrg.dim'
        if lay_subset['use_subset_intf']:
            return '_Stack_esd_mask_deb_mmifg_subset.dim'
        return '_Stack_esd_mask_deb_mmifg.dim'
    
    @staticmethod
    def _intf_folder_basename_intf_to_ml_flt_shp(basename):
        """Subset/deburst intf folder basename → Import-Vector output folder (ml_flt_shp_* name scheme)."""
        if basename.startswith('multi_intf_deb_mrg_subset_'):
            return basename.replace(
                'multi_intf_deb_mrg_subset_', 'multi_intf_deb_mrg_ml_flt_shp_', 1)
        if basename.startswith('multi_intf_deb_subset_'):
            return basename.replace(
                'multi_intf_deb_subset_', 'multi_intf_deb_ml_flt_shp_', 1)
        if basename.startswith('multi_intf_deb_mrg_'):
            return basename.replace(
                'multi_intf_deb_mrg_', 'multi_intf_deb_mrg_ml_flt_shp_', 1)
        if basename.startswith('multi_intf_deb_'):
            return basename.replace(
                'multi_intf_deb_', 'multi_intf_deb_ml_flt_shp_', 1)
        return basename
    
    @staticmethod
    def _stack_suffix_intf_mmifg_to_ml_flt_shp(suffix_dot_dim):
        """Interferogram stack suffix → suffix after Import-Vector (embedded ml_flt_shp segment)."""
        return (
            suffix_dot_dim.replace('mmifg_mrg_subset.dim', 'mmifg_mrg_ml_flt_shp.dim')
            .replace('mmifg_mrg.dim', 'mmifg_mrg_ml_flt_shp.dim')
            .replace('mmifg_subset.dim', 'mmifg_ml_flt_shp.dim')
            .replace('mmifg.dim', 'mmifg_ml_flt_shp.dim')
        )
    
    @staticmethod
    def _is_single_swath_pre_ml_deburst_intf_basename(name):
        """Step 7–9 single-swath deburst tree (exclude subset/ml/shp downstream folders)."""
        if name.startswith('multi_intf_deb_subset_'):
            return False
        if not name.startswith('multi_intf_deb_'):
            return False
        markers = ('subset', 'mrg', 'ml', 'bandmath', 'flt', 'shp', 'snaphu')
        return not any(m in name for m in markers)

    @staticmethod
    def _is_merged_pre_ml_deburst_intf_basename(name):
        """Step 7–9 merged deburst tree (exclude subset/ml/shp downstream folders)."""
        if name.startswith('multi_intf_deb_mrg_subset_'):
            return False
        if not name.startswith('multi_intf_deb_mrg_'):
            return False
        markers = ('ml', 'bandmath', 'flt', 'shp', 'snaphu')
        return not any(m in name for m in markers)

    @staticmethod
    def _stack_suffix_from_pre_ml_intf_basename(basename):
        if basename.startswith('multi_intf_deb_mrg_subset_'):
            return '_Stack_esd_mask_deb_mmifg_mrg_subset.dim'
        if basename.startswith('multi_intf_deb_mrg_'):
            return '_Stack_esd_mask_deb_mmifg_mrg.dim'
        if basename.startswith('multi_intf_deb_subset_'):
            return '_Stack_esd_mask_deb_mmifg_subset.dim'
        if basename.startswith('multi_intf_deb_'):
            return '_Stack_esd_mask_deb_mmifg.dim'
        return '_Stack_esd_mask_deb_mmifg.dim'

    def _discover_pre_ml_intf_dir(self, parent, merged=False):
        """Find Step 7–9 deburst interferogram tree; prefer subset, scan disk if master_date mismatches."""
        if merged:
            subset_prefix = 'multi_intf_deb_mrg_subset_'
            main_prefix = 'multi_intf_deb_mrg_'
            accept_main = self._is_merged_pre_ml_deburst_intf_basename
        else:
            subset_prefix = 'multi_intf_deb_subset_'
            main_prefix = 'multi_intf_deb_'
            accept_main = self._is_single_swath_pre_ml_deburst_intf_basename

        for prefix, accept in (
            (subset_prefix, lambda n: n.startswith(subset_prefix)),
            (main_prefix, accept_main),
        ):
            path = os.path.join(parent, f'{prefix}{self.master_date}')
            if os.path.isdir(path) and os.listdir(path):
                return path

        if not os.path.isdir(parent):
            return None

        for prefix, accept in (
            (subset_prefix, lambda n: n.startswith(subset_prefix)),
            (main_prefix, accept_main),
        ):
            for name in sorted(os.listdir(parent)):
                if not accept(name):
                    continue
                full = os.path.join(parent, name)
                if os.path.isdir(full) and os.listdir(full):
                    return full
        return None

    def _deburst_intf_base_for_swath(self, swath_name, lay):
        bases = lay.get('intf_bases_by_swath') or {}
        if lay['use_merged']:
            return bases.get('_merged_')
        return bases.get(swath_name)

    def _post_subset_interferogram_layout(self):
        """Detect subset/full-res multi_intf deb tree **before** Multilook (Step 9 / merge subset)."""
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        intf_bases_by_swath = {}
        use_subset_intf = False

        if use_merged and merge_folder_name:
            merge_dir = os.path.join(self.proj_root, merge_folder_name)
            found = self._discover_pre_ml_intf_dir(merge_dir, merged=True)
            if found:
                intf_bases_by_swath['_merged_'] = found
                use_subset_intf = 'subset' in os.path.basename(found)
            else:
                use_merged = False

        if not use_merged:
            for sw in self.swaths_to_process:
                sw_root = os.path.join(self.proj_root, sw)
                found = self._discover_pre_ml_intf_dir(sw_root, merged=False)
                if found:
                    intf_bases_by_swath[sw] = found
                    if 'subset' in os.path.basename(found):
                        use_subset_intf = True

        if use_merged:
            stack_suffix = (
                '_Stack_esd_mask_deb_mmifg_mrg_subset.dim' if use_subset_intf
                else '_Stack_esd_mask_deb_mmifg_mrg.dim')
        else:
            stack_suffix = (
                '_Stack_esd_mask_deb_mmifg_subset.dim' if use_subset_intf
                else '_Stack_esd_mask_deb_mmifg.dim')

        if intf_bases_by_swath:
            sample = next(iter(intf_bases_by_swath.values()))
            stack_suffix = self._stack_suffix_from_pre_ml_intf_basename(os.path.basename(sample))

        return {
            'use_merged': use_merged,
            'use_subset_intf': use_subset_intf,
            'merge_folder_name': merge_folder_name,
            'stack_suffix': stack_suffix,
            'intf_bases_by_swath': intf_bases_by_swath,
        }
    
    @staticmethod
    def _intf_folder_basename_ml_flt_to_shp(basename):
        return (
            basename.replace('deb_mrg_ml_flt_subset_', 'deb_mrg_ml_flt_shp_subset_')
            .replace('deb_mrg_ml_flt_', 'deb_mrg_ml_flt_shp_')
            .replace('deb_ml_flt_subset_', 'deb_ml_flt_shp_subset_')
            .replace('deb_ml_flt_', 'deb_ml_flt_shp_', 1)
        )
    
    @staticmethod
    def _intf_folder_basename_shp_to_mask(basename):
        return (
            basename.replace('deb_mrg_ml_flt_shp_subset_', 'deb_mrg_ml_flt_shp_mask_subset_')
            .replace('deb_mrg_ml_flt_shp_', 'deb_mrg_ml_flt_shp_mask_')
            .replace('deb_ml_flt_shp_subset_', 'deb_ml_flt_shp_mask_subset_')
            .replace('deb_ml_flt_shp_', 'deb_ml_flt_shp_mask_')
        )
    
    @staticmethod
    def _stack_suffix_flt_to_shp(suffix_dot_dim):
        return (
            suffix_dot_dim.replace('mmifg_mrg_ml_flt_subset.dim', 'mmifg_mrg_ml_flt_shp_subset.dim')
            .replace('mmifg_mrg_ml_flt.dim', 'mmifg_mrg_ml_flt_shp.dim')
            .replace('mmifg_ml_flt_subset.dim', 'mmifg_ml_flt_shp_subset.dim')
            .replace('mmifg_ml_flt.dim', 'mmifg_ml_flt_shp.dim')
        )
    
    @staticmethod
    def _stack_suffix_shp_to_mask(suffix_dot_dim):
        return (
            suffix_dot_dim.replace('mmifg_mrg_ml_flt_shp_subset.dim', 'mmifg_mrg_ml_flt_shp_mask_subset.dim')
            .replace('mmifg_mrg_ml_flt_shp.dim', 'mmifg_mrg_ml_flt_shp_mask.dim')
            .replace('mmifg_ml_flt_shp_subset.dim', 'mmifg_ml_flt_shp_mask_subset.dim')
            .replace('mmifg_ml_flt_shp.dim', 'mmifg_ml_flt_shp_mask.dim')
        )
    
    def _stamps_sbas_bandmath_masked_intf_input(self, merged_tree):
        if not self.get_msk_shp_path() or self.insar_target != 2:
            return None
        if merged_tree:
            mf = self.get_merge_folder_name(self.get_swath_config_value())
            root = os.path.join(self.proj_root, mf)
            d = os.path.join(root, f'multi_intf_deb_mrg_ml_flt_shp_mask_{self.master_date}')
            sfx = '_Stack_esd_mask_deb_mmifg_mrg_ml_flt_shp_mask.dim'
        else:
            sw = self.swaths_to_process[0]
            d = os.path.join(self.proj_root, sw, f'multi_intf_deb_ml_flt_shp_mask_{self.master_date}')
            sfx = '_Stack_esd_mask_deb_mmifg_ml_flt_shp_mask.dim'
        return d, sfx
    
    def _post_goldstein_ml_flt_layout(self):
        swath_config = self.get_swath_config_value()
        use_merged = swath_config not in [1, 2, 3]
        merge_folder_name = self.get_merge_folder_name(swath_config) if use_merged else None
        use_subset = False
        if use_merged:
            merge_flt_subset_dir = os.path.join(self.proj_root, merge_folder_name,
                                                  f'multi_intf_deb_mrg_ml_flt_subset_{self.master_date}')
            merge_flt_dir = os.path.join(self.proj_root, merge_folder_name,
                                         f'multi_intf_deb_mrg_ml_flt_{self.master_date}')
            if os.path.exists(merge_flt_subset_dir) and os.listdir(merge_flt_subset_dir):
                use_subset = True
                use_merged = True
            elif os.path.exists(merge_flt_dir) and os.listdir(merge_flt_dir):
                use_merged = True
            else:
                use_merged = False
        if not use_merged and not use_subset:
            for sw in self.swaths_to_process:
                subset_flt_dir = os.path.join(self.proj_root, sw,
                                              f'multi_intf_deb_ml_flt_subset_{self.master_date}')
                if os.path.exists(subset_flt_dir) and os.listdir(subset_flt_dir):
                    use_subset = True
                    break
        if use_merged:
            if use_subset:
                file_suffix = '_Stack_esd_mask_deb_mmifg_mrg_ml_flt_subset.dim'
            else:
                file_suffix = '_Stack_esd_mask_deb_mmifg_mrg_ml_flt.dim'
        else:
            if use_subset:
                file_suffix = '_Stack_esd_mask_deb_mmifg_ml_flt_subset.dim'
            else:
                file_suffix = '_Stack_esd_mask_deb_mmifg_ml_flt.dim'
        return {
            'use_merged': use_merged,
            'use_subset': use_subset,
            'merge_folder_name': merge_folder_name,
            'file_suffix': file_suffix,
        }
    
    def _ml_flt_input_base_dir(self, swath_name, lay):
        """Base directory containing Goldstein stacks (multi_intf_* ml_flt_*) prior to optional vector AOI."""
        if lay['use_merged']:
            mf = lay['merge_folder_name']
            if lay['use_subset']:
                return os.path.join(self.proj_root, mf, f'multi_intf_deb_mrg_ml_flt_subset_{self.master_date}')
            return os.path.join(self.proj_root, mf, f'multi_intf_deb_mrg_ml_flt_{self.master_date}')
        if lay['use_subset']:
            return os.path.join(self.proj_root, swath_name, f'multi_intf_deb_ml_flt_subset_{self.master_date}')
        return os.path.join(self.proj_root, swath_name, f'multi_intf_deb_ml_flt_{self.master_date}')
    
    def snaphu_source_base_dir_and_suffix(self, swath_name, lay=None):
        lay = lay or self._post_goldstein_ml_flt_layout()
        ml_base = self._ml_flt_input_base_dir(swath_name, lay)
        fs = lay['file_suffix']
        if self.insar_target == 2 and self.get_msk_shp_path():
            shp_bn = self._intf_folder_basename_ml_flt_to_shp(os.path.basename(ml_base))
            shp_base = os.path.join(os.path.dirname(ml_base), shp_bn)
            mask_bn = self._intf_folder_basename_shp_to_mask(shp_bn)
            mask_base = os.path.join(os.path.dirname(shp_base), mask_bn)
            return mask_base, self._stack_suffix_shp_to_mask(self._stack_suffix_flt_to_shp(fs))
        return ml_base, fs
    
    def snaphu_stack_subfolder_prefix_candidates(self, swath_name, lay=None):
        """Ordered Step 19 stack subfolder suffixes (no ``.dim``) under each pair in ``multi_snaphu_*``."""
        lay = lay or self._post_goldstein_ml_flt_layout()
        _, suf_dim = self.snaphu_source_base_dir_and_suffix(swath_name, lay)
        primary = os.path.splitext(suf_dim)[0]
        candidates = [primary]
        for alt in (
            primary.replace('_mrg_ml_flt_subset', '_mrg_ml_flt'),
            primary.replace('_mrg_ml_flt', '_ml_flt'),
            primary.replace('_ml_flt_subset', '_ml_flt'),
            primary.replace('_ml_flt', '_mrg_ml_flt'),
            primary.replace('_subset', ''),
        ):
            if alt and alt not in candidates:
                candidates.append(alt)
        return candidates
