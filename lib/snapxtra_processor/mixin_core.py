"""Core: init, config, runtime, GPT commands"""
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


class SnapxtraCoreMixin:

    def __init__(self, config_file, skip_master_check=False):
        self.config_file = config_file
        self.config = self.read_config(config_file)
        self.savelog = str(self.config.get('savelog', 'debug')).strip().lower()
        if self.savelog not in ('debug', 'release'):
            self.savelog = 'debug'
        self.validate_config()
        
        self.create_project_root()
        self._setup_session_logging()
        
        self.gpt_path = self.config['gpt_loc']
        self.graph_path = self.config['graph_path']
        self.snaphu_install = self.config.get('snaphu_install', os.path.expanduser('~/.snap/auxdata/snaphu-v2.0.4_linux/'))
        
        
        self.baseline_data = {}
        self.master_date = None
        
        config_master = self.config.get('master', '')
        if config_master:
            self.master_date = str(config_master)
            print(f"  Master date from config: {self.master_date}")
        elif not skip_master_check:
            if not self.load_baseline_data():
                print(f"\n{'='*80}")
                print(f"ERROR: Master date not found")
                print(f"{'='*80}")
                print(f"\n  Master date must be specified in config OR baselines file must exist.")
                print(f"\n  Missing files:")
                print(f"    - proj_{{asc|dsc}}/metadata_info/baselines")
                print(f"\n  ⚙️  ACTION REQUIRED:")
                print(f"    1. Run Step 0 to create baselines, then Steps 1–2 for AOI/slices if needed:")
                print(f"       python ps_sbas_snapxtra.py {os.path.basename(config_file)} baselines")
                print(f"       OR: python ps_sbas_snapxtra.py 0 {os.path.basename(config_file)}")
                print(f"    2. OR add 'master_date=YYYYMMDD' to config file")
                print(f"\n  Step 0 uses SNAP InSAR-Overview to auto-detect optimal master date.\n")
                raise FileNotFoundError("Master date not available. Run Step 0 first or specify master_date in config.")
            print(f"  Master date loaded from baselines: {self.master_date}")
        else:
            print(f"  Master date will be auto-detected during baseline creation")
        
        self.swaths_to_process = self.parse_swath_option()
        
        self.sbas_pairs = []
        self.master_second_pairs = []
        self.ps_pair_to_target_dates = {}
        self.ps_selected_pair_names = set()
        self.sbas_pairs_full = []
        
        self.threads, self.use_parallel = self.configure_parallel_processing()
        
      
        self.range_looks = self._parse_int_config('Range', 20)
        self.azimuth_looks = self._parse_int_config('Azimuth', 4)
        self.cost_mode = self.config.get('cost_mode', 'DEFO').upper()
        self.init_method = self.config.get('init_method', 'MCF').upper()
        self.phase_coh_mask_th = float(self.config.get('phase_coh_mask_th', 0.1))  
        self.insar_target = int(self.config.get('insar_target', 1)) 
        self.insar_method = int(self.config.get('insar_method', 1)) 
        
        self.start_step = self.config.get('start_step', '')
        self.end_step = self.config.get('end_step', '')
        self._batch_wall_start = None 
        self._reset_batch_wall_start = False 
        
        self._cached_cache_size = None
        self._cache_size_logged = False
        self._swath_updated = False
        
        self.selected_dates = None
        
        self._aoi_checked = False
        
    def read_config(self, config_file):
        """Read and parse configuration file"""
        config = {}
        config['_config_path'] = os.path.abspath(config_file)
        
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.split('#')[0].strip()
                    
                    if value.lower() in ['true', 'false']:
                        config[key] = value.lower() == 'true'
                    elif value.isdigit():
                        config[key] = int(value)
                    else:
                        try:
                            config[key] = float(value)
                        except ValueError:
                            config[key] = value
        
        return config
    
    def update_config_file(self, key, value):
        """Update a parameter in the config file."""
        config_path = self.config.get('_config_path', None)
        if not config_path or not os.path.exists(config_path):
            print(f"  ⚠ Warning: Cannot update config file (path not found)")
            return
        
        value_str = str(value)
        
        try:
            with open(config_path, 'r') as f:
                lines = f.readlines()
            
   
            updated = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(f"{key}="):

                    if '#' in line:
                        comment_part = line.split('#', 1)[1]
                        lines[i] = f"{key}={value_str}  # {comment_part}"
                    else:
                        lines[i] = f"{key}={value_str}\n"
                    updated = True
                    break
            

            if not updated:
                lines.append(f"\n{key}={value_str}\n")
            
            with open(config_path, 'w') as f:
                f.writelines(lines)
            

            if value_str.lower() in ['true', 'false']:
                self.config[key] = value_str.lower() == 'true'
            elif value_str.isdigit():
                self.config[key] = int(value_str)
            else:
                try:
                    self.config[key] = float(value_str)
                except ValueError:
                    self.config[key] = value_str
                    
        except Exception as e:
            print(f"  ⚠ Warning: Failed to update config file: {e}")

    @staticmethod
    def normalize_orbit(orbit_str):
        """Normalize orbit config to canonical 'Ascending' or 'Descending'."""
        v = str(orbit_str).strip().lower()
        if v in ('asc', 'ascending'):
            return 'Ascending'
        if v in ('dsc', 'descending', 'dscending'):
            return 'Descending'
        raise ValueError(
            f"Invalid orbit: {orbit_str!r}. "
            "Use Ascending/ascending/asc or Descending/descending/dsc."
        )
    
    def validate_config(self):
        required = ['input_data', 'gpt_loc', 'swath', 'orbit']
        
        for param in required:
            if param not in self.config:
                raise ValueError(f"Required parameter '{param}' not found in config file")
        
        if not os.path.exists(self.config['gpt_loc']):
            raise FileNotFoundError(f"GPT executable not found: {self.config['gpt_loc']}")

        self.config['orbit'] = self.normalize_orbit(self.config['orbit'])
        orbit_suffix = 'asc' if self.config['orbit'] == 'Ascending' else 'dsc'
        output_dir = str(self.config.get('output_dir', '')).strip()
        base_dir = self.config['input_data']
        if output_dir and os.path.isdir(output_dir):
            base_dir = output_dir
        proj_root_path = os.path.join(base_dir, f'proj_{orbit_suffix}')
        

        proj_exists = os.path.exists(proj_root_path) and os.path.isdir(proj_root_path)
        proj_has_data = False
        if proj_exists:

            try:
                proj_has_data = bool(os.listdir(proj_root_path))
            except:
                proj_has_data = False
        

        if proj_exists and proj_has_data:
            print(f"  ℹ Project root exists with data: {proj_root_path}")
            print(f"  Skipping input_data directory validation (continuation mode)")
        else:
            if not os.path.exists(self.config['input_data']):
                raise FileNotFoundError(f"Input data directory not found: {self.config['input_data']}")
        
        output_dir = str(self.config.get('output_dir', '')).strip()
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir, exist_ok=True)
                print(f"Created output directory: {output_dir}")
            except Exception as e:
                raise FileNotFoundError(f"Failed to create output directory: {output_dir}\nError: {e}")

        gp = self.config.get('graph_path', None)
        if gp is None or str(gp).strip() == '':
            legacy = self.config.get('sbas_graph', None)
            if legacy is not None and str(legacy).strip() != '':
                print("  Warning: 'sbas_graph' is deprecated; use 'graph_path' in config.")
                gp = legacy
            else:
                raise ValueError(
                    "Required configuration parameter 'graph_path' is missing or empty. "
                    "Set graph_path=/path/to/directory containing all SNAP workflow .xml graph files."
                )
        gp_abs = os.path.abspath(os.path.expanduser(str(gp).strip()))
        if not os.path.isdir(gp_abs):
            raise FileNotFoundError(
                f"'graph_path' must be an existing directory: {gp_abs}"
            )
        self.config['graph_path'] = gp_abs
        
        mask_vec = self.get_msk_shp_path()
        if mask_vec:
            mask_abs = os.path.abspath(os.path.expanduser(mask_vec))
            if not os.path.isfile(mask_abs):
                raise FileNotFoundError(
                    "Configuration 'msk_shp' or 'shp_mask' is set but the file does not exist:\n"
                    f"  {mask_abs}"
                )
    
    def create_project_root(self):

        orbit_suffix = 'asc' if self.config['orbit'] == 'Ascending' else 'dsc'
        output_dir = str(self.config.get('output_dir', '')).strip()
        base_dir = self.config['input_data']
        

        if output_dir and os.path.isdir(output_dir):
            base_dir = output_dir
        
        self.proj_root = os.path.join(base_dir, f'proj_{orbit_suffix}')
        
        if not os.path.exists(self.proj_root):
            os.makedirs(self.proj_root)
            print(f"Created project root: {self.proj_root}")
        else:
            print(f"Using existing project root: {self.proj_root}")

    def _setup_session_logging(self):

        log_dir = os.path.join(self.proj_root, 'log')
        os.makedirs(log_dir, exist_ok=True)
        stamp = datetime.now().strftime('D%Y%m%dT%H%M%S')
        self._session_log_path = os.path.join(log_dir, f'{stamp}.txt')
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._session_log_file = open(
            self._session_log_path, 'w', encoding='utf-8', buffering=1)

        if self.savelog == 'release':
            sys.stdout = ReleaseFilterStream(self._orig_stdout, self._session_log_file)
            now = datetime.now()
            header = (
                f"\n✓ Validation OK !\n\n"
                f"log date: {now.year} - {now.strftime('%m')} - {now.strftime('%d')}\n"
            )
            self._orig_stdout.write(header)
            self._session_log_file.write(header)
        else:
            sys.stdout = TeeStream(self._orig_stdout, self._session_log_file)

        sys.stderr = TeeStream(self._orig_stderr, self._session_log_file)
        session_msg = (
            f"Session log: {self._session_log_path} (savelog={self.savelog})\n")
        self._orig_stdout.write(session_msg)
        self._session_log_file.write(session_msg)

    def _print_block_always(self, text):
        """Write a message block to console and session log, bypassing release filter."""
        block = text if text.endswith('\n') else text + '\n'
        out = getattr(self, '_orig_stdout', None) or sys.stdout
        out.write(block)
        out.flush()
        logf = getattr(self, '_session_log_file', None)
        if logf:
            logf.write(block)
            logf.flush()

    def _teardown_session_logging(self):
        """Restore original stdout/stderr and close the session log file."""
        if getattr(self, '_session_log_file', None):
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            if getattr(self, '_orig_stdout', None) is not None:
                sys.stdout = self._orig_stdout
            if getattr(self, '_orig_stderr', None) is not None:
                sys.stderr = self._orig_stderr
            try:
                self._session_log_file.close()
            except Exception:
                pass
            self._session_log_file = None

    def _log_release_step00_summary(self):
        """Condensed Step 0 footer for savelog=release."""
        if self.savelog != 'release':
            return
        print("✓ Baseline Created")
        print("✓ Network Graph  Created")
        print("✓ Metadata Created")
        sbas_file = os.path.join(self.proj_root, 'metadata_info', 'sbas_pairs.txt')
        bridge_file = os.path.join(self.proj_root, 'metadata_info', 'connect_sb.txt')
        baselines_file = os.path.join(self.proj_root, 'metadata_info', 'baselines')
        n_pairs = 0
        n_bridge = 0
        all_dates = set()
        connected_dates = set()
        if os.path.isfile(sbas_file):
            with open(sbas_file, 'r') as f:
                for ln in f:
                    if not ln.strip() or ln.startswith('#'):
                        continue
                    n_pairs += 1
                    parts = ln.split()
                    if len(parts) >= 2:
                        connected_dates.add(parts[0])
                        connected_dates.add(parts[1])
        if os.path.isfile(bridge_file):
            with open(bridge_file, 'r') as f:
                n_bridge = sum(
                    1 for ln in f if ln.strip() and not ln.startswith('#'))
        if os.path.isfile(baselines_file):
            with open(baselines_file, 'r') as f:
                for ln in f:
                    if not ln.strip() or ln.startswith('#'):
                        continue
                    parts = ln.split()
                    if len(parts) >= 2:
                        all_dates.add(parts[0])
                        all_dates.add(parts[1])
        print("\nNetwork Information: \n")
        print(f"Total pairs: {n_pairs}")
        print(f"Bridge pairs: {n_bridge}")
        if all_dates:
            print(f"Total dates: {len(all_dates)}")
            print(f"Connected dates: {len(connected_dates)}")
            print(f"Isolated dates: {len(all_dates) - len(connected_dates)}")


    _RELEASE_PROGRESS_MILESTONES = (5, 15, 25, 45, 65, 80, 95, 100)

    def _release_progress_begin(self, total):
        self._release_progress_hit = set()
        self._release_progress_total = max(0, int(total))
        self._release_progress_done = 0

    def _release_progress_advance(self, count=1):
        if self.savelog != 'release':
            return
        total = getattr(self, '_release_progress_total', 0)
        if total <= 0:
            return
        self._release_progress_done = getattr(self, '_release_progress_done', 0) + count
        self._release_progress_tick(self._release_progress_done, total)

    def _release_progress_finish_step(self):
        """Ensure release-mode steps end at 100% when work units were tracked."""
        if self.savelog != 'release':
            return
        total = getattr(self, '_release_progress_total', 0)
        done = getattr(self, '_release_progress_done', 0)
        if total <= 0:
            self._release_progress_begin(1)
            self._release_progress_advance(1)
        elif done < total:
            self._release_progress_advance(total - done)

    def _release_progress_reset_step(self):
        self._release_progress_hit = set()
        self._release_progress_total = 0
        self._release_progress_done = 0

    def _release_progress_tick(self, completed, total):
        if self.savelog != 'release' or total <= 0:
            return
        if not hasattr(self, '_release_progress_hit'):
            self._release_progress_hit = set()
        for milestone in self._RELEASE_PROGRESS_MILESTONES:
            if milestone in self._release_progress_hit:
                continue
            threshold = math.ceil(total * milestone / 100.0)
            if completed >= threshold:
                self._release_progress_hit.add(milestone)
                chunk = f"...{milestone}%"
                self._orig_stdout.write(chunk)
                self._orig_stdout.flush()
                if getattr(self, '_session_log_file', None):
                    self._session_log_file.write(chunk)
                    self._session_log_file.flush()
        if completed >= total:
            self._orig_stdout.write('\n')
            self._orig_stdout.flush()
            if getattr(self, '_session_log_file', None):
                self._session_log_file.write('\n')
                self._session_log_file.flush()
    
    def parse_swath_option(self):
        swath_raw = self.config['swath']
        

        if isinstance(swath_raw, str) and ',' in swath_raw:

            swath_parts = swath_raw.split(',')
            swath = int(swath_parts[0].strip())

        else:
            swath = int(swath_raw) if not isinstance(swath_raw, int) else swath_raw
        
        if swath == 0:
            return ['IW1', 'IW2', 'IW3']
        elif swath == 1:
            return ['IW1']
        elif swath == 2:
            return ['IW2']
        elif swath == 3:
            return ['IW3']
        elif swath == 12:
            return ['IW1', 'IW2']
        elif swath == 23:
            return ['IW2', 'IW3']
        else:
            raise ValueError(f"Invalid swath option: {swath_raw}")
    
    def get_swath_config_value(self):
        swath_raw = self.config['swath']
        

        if isinstance(swath_raw, str) and ',' in swath_raw:

            swath_parts = swath_raw.split(',')
            return int(swath_parts[0].strip())
        else:
            return int(swath_raw) if not isinstance(swath_raw, int) else swath_raw
    
    def configure_parallel_processing(self):

        try:

            try:
                n_cpu = len(os.sched_getaffinity(0))
            except:
                n_cpu = mp.cpu_count()
            
            print(f"\n{'='*80}")
            print(f"PARALLEL PROCESSING CONFIGURATION")
            print(f"{'='*80}")
            print(f"  Detected CPUs: {n_cpu}")
            

            parallel_enabled = self.config.get('parallel', True)
            if isinstance(parallel_enabled, str):
                parallel_enabled = parallel_enabled.lower() in ['true', '1', 'yes']
            
            print(f"  Config parallel: {parallel_enabled}")
            

            if not parallel_enabled:
                print(f"  Parallel processing DISABLED (parallel=false)")
                print(f"  GPT jobs run sequentially; no -q flag")
                print(f"{'='*80}\n")
                return 1, False
            

            threads_config = self.config.get('threads', 6)
            if isinstance(threads_config, str):
                threads_config = int(threads_config) if threads_config.strip().isdigit() else 6
            
            print(f"  Config threads: {threads_config}")
            

            cpu_60_percent = max(1, int(n_cpu * 0.6))
            print(f"  60% of CPUs: {cpu_60_percent}")
            

            if threads_config > cpu_60_percent:
                threads = cpu_60_percent
                print(f"  ⚠️  Requested threads ({threads_config}) exceeds 60% of CPUs ({cpu_60_percent})")
                print(f"  Adjusting threads to: {threads}")
            else:
                threads = threads_config
                print(f"  Using threads: {threads}")
            
            use_parallel = (threads > 1 and n_cpu > 1)
            
            if use_parallel:
                print(f"  Parallel processing ENABLED: GPT -q {threads} per job")
                print(f"  Jobs run sequentially (one gpt process at a time)")
            else:
                print(f"  Parallel processing DISABLED (threads=1, no GPT -q)")
                threads = 1
            
            print(f"{'='*80}\n")
            
            return threads, use_parallel
            
        except Exception as e:
            print(f"  WARNING: Failed to configure parallel processing: {e}")
            print(f"  Falling back to sequential mode")
            return 1, False
    
    def get_free_memory_gb(self):
        import platform
        try:
            import psutil
            return psutil.virtual_memory().available / (1024.0 ** 3)
        except Exception:
            pass
        
        try:

            if platform.system() == 'Linux':

                slurm_mem_per_node = os.environ.get('SLURM_MEM_PER_NODE')
                slurm_mem_per_cpu = os.environ.get('SLURM_MEM_PER_CPU')
                slurm_cpus_per_task = os.environ.get('SLURM_CPUS_PER_TASK', os.environ.get('SLURM_NTASKS', '1'))
                
                if slurm_mem_per_node:

                    allocated_mb = int(slurm_mem_per_node)
                    print(f"  Linux + SLURM detected: allocated {allocated_mb} MB per node")
                    return allocated_mb / 1024.0
                elif slurm_mem_per_cpu:

                    mem_per_cpu_mb = int(slurm_mem_per_cpu)
                    num_cpus = int(slurm_cpus_per_task)
                    allocated_mb = mem_per_cpu_mb * num_cpus
                    print(f"  Linux + SLURM detected: allocated {allocated_mb} MB ({mem_per_cpu_mb} MB/CPU × {num_cpus} CPUs)")
                    return allocated_mb / 1024.0
                else:

                    result = subprocess.run(['free', '-m'], capture_output=True, text=True)
                    lines = result.stdout.split('\n')
                    for line in lines:
                        if line.startswith('Mem:'):
                            parts = line.split()
                            if len(parts) >= 7:
                                avail_mb = int(parts[6])
                                return avail_mb / 1024.0
            

            elif platform.system() == 'Windows':
                result = subprocess.run(
                    'systeminfo | findstr /C:"Available Physical Memory"',
                    capture_output=True, text=True, shell=True
                )
                if result.returncode == 0 and result.stdout:
                    import re
                    match = re.search(r'([0-9,]+)\s*MB', result.stdout)
                    if match:
                        free_mb = int(match.group(1).replace(',', ''))
                        return free_mb / 1024.0
            

            else:
                result = subprocess.run(['free', '-m'], capture_output=True, text=True)
                lines = result.stdout.split('\n')
                for line in lines:
                    if line.startswith('Mem:'):
                        parts = line.split()
                        if len(parts) >= 7:
                            avail_mb = int(parts[6])
                            return avail_mb / 1024.0
                            
        except Exception as e:
            print(f"  Warning: Could not get free memory: {e}")
        
        return None

    def wait_for_memory(self, min_free_gb=6.0, check_interval=15):
        while True:
            free_gb = self.get_free_memory_gb()
            if free_gb is None:
                print("  Warning: Could not determine free memory; proceeding without wait.")
                return
            if free_gb >= min_free_gb:
                return
            print(f"  ⚠ Free memory {free_gb:.2f}G < {min_free_gb:.1f}G. Waiting {check_interval}s...")
            time.sleep(check_interval)
    
    def get_cache_size(self):

        if self._cached_cache_size is not None:
            return self._cached_cache_size
        

        free_gb = self.get_free_memory_gb()
        

        cache_val = self.config.get('cache_size', self.config.get('cache_mem', ''))
        

        if isinstance(cache_val, (int, float)):
            cache_val = str(cache_val)
        
        cache_val = cache_val.strip()
        
        if cache_val:

            cache_val_upper = cache_val.upper()
            if cache_val_upper.endswith('GB'):
                requested_gb = float(cache_val_upper[:-2])
            elif cache_val_upper.endswith('G'):
                requested_gb = float(cache_val_upper[:-1])
            else:
                requested_gb = float(cache_val_upper)
            

            if free_gb and (requested_gb > free_gb * 0.7):

                cache_gb = int(free_gb * 0.6)
                if free_gb >= 6 and cache_gb < 3:
                    cache_gb = 3
                if not self._cache_size_logged:
                    print(f"  Config cache ({requested_gb}G) exceeds 70% of free memory ({free_gb:.1f}G)")
                    print(f"  Using 60% of free memory: {cache_gb}G")
                    self._cache_size_logged = True
            else:
                cache_gb = int(requested_gb)
                if not self._cache_size_logged:
                    print(f"  Using cache from config: {cache_gb}G")
                    self._cache_size_logged = True
        else:

            if free_gb:
                cache_gb = int(free_gb * 0.6)
                if free_gb >= 6 and cache_gb < 3:
                    cache_gb = 3
                if not self._cache_size_logged:
                    print(f"  No cache specified, using 60% of free memory: {cache_gb}G (free: {free_gb:.1f}G)")
                    self._cache_size_logged = True
            else:
                cache_gb = 6
                if not self._cache_size_logged:
                    print(f"  Cannot detect free memory, using default: {cache_gb}G")
                    self._cache_size_logged = True
        
        self._cached_cache_size = f"{cache_gb}G"
        return self._cached_cache_size

    def gpt_base_cmd(self, include_cache=True, cache_size=None):
        cmd = [self.gpt_path]
        if self.use_parallel:
            cmd.extend(['-q', str(self.threads)])
        if include_cache:
            cache = cache_size if cache_size is not None else self.get_cache_size()
            cmd.extend(['-c', cache])
        return cmd
    
    def update_swath_in_config(self, new_swath):
        try:
            with open(self.config_file, 'r') as f:
                lines = f.readlines()

            updated = False
            for i, line in enumerate(lines):
                if line.strip().startswith('swath') and '=' in line:
                    prefix = line.split('=')[0]
                    lines[i] = f"{prefix}= {new_swath}\n"
                    updated = True
                    break

            if updated:
                with open(self.config_file, 'w') as f:
                    f.writelines(lines)

                if ',' in str(new_swath):
                    self.config['swath'] = str(new_swath)
                else:
                    self.config['swath'] = int(new_swath)
                self.swaths_to_process = self.parse_swath_option()
                self._swath_updated = True
                print(f"  ✓ Updated swath in config to {new_swath}")
            else:
                print("  ⚠ Could not update swath in config (key not found)")
        except Exception as e:
            print(f"  ⚠ Failed to update swath in config: {e}")
    
    def check_output_file_valid(self, output_file):
        if not os.path.exists(output_file):
            return False
        

        if os.path.getsize(output_file) == 0:
            return False
        

        if output_file.endswith('.dim'):
            data_dir = output_file.replace('.dim', '.data')
            if not os.path.exists(data_dir):
                return False

            if not os.listdir(data_dir):
                return False
        
        return True
    
    def validate_metadata_info(self):
        metadata_dir = os.path.join(self.proj_root, 'metadata_info')
        
        if not os.path.exists(metadata_dir):
            print(f"\n{'='*80}")
            print(f"ERROR: metadata_info/ directory not found")
            print(f"{'='*80}")
            print(f"  Expected location: {metadata_dir}")
            print(f"\n  Please run the metadata generation command first to create:")
            print(f"  - baselines")
            print(f"  - baselines_ps")
            print(f"  - metadata")
            print(f"  - sbas_pairs.txt")
            print(f"  - network_sbas.png (LiCSBAS / StaMPS SBAS) or network_ps.png (StaMPS PS)")
            print(f"\n  ✗ Step terminated - metadata_info/ required\n")
            print(f"{'='*80}\n")
            return False
        
        required_files = [
            'baselines',
            'baselines_ps',
            'metadata',
            'sbas_pairs.txt',
        ]
        if self.insar_target == 1 or (self.insar_target == 2 and self.insar_method == 1):
            required_files.append('network_sbas.png')
        if self.insar_target == 2 and self.insar_method == 2:
            required_files.append('network_ps.png')
        
        missing_files = []
        for filename in required_files:
            file_path = os.path.join(metadata_dir, filename)
            if not os.path.exists(file_path):
                missing_files.append(filename)
        
        if missing_files:
            print(f"\n{'='*80}")
            print(f"ERROR: Missing required files in metadata_info/")
            print(f"{'='*80}")
            print(f"  Location: {metadata_dir}")
            print(f"\n  Missing files:")
            for filename in missing_files:
                print(f"    ✗ {filename}")
            print(f"\n  Please run the metadata generation command to create these files")
            print(f"\n  ✗ Step terminated - complete metadata required\n")
            print(f"{'='*80}\n")
            return False
        
        return True
    
    def run_command(self, cmd, description="", expected_output_file=None):
        if description:
            print(f"\n{description}")
        
        print(f"  Command: {' '.join(cmd)}")
        self._last_cmd_stdout = ''
        self._last_cmd_stderr = ''
        
        try:

            result = subprocess.run(
                cmd, 
                capture_output=True, 
                text=True, 
                timeout=7200,  
                preexec_fn=os.setpgrp  
            )
            

            is_gpt_command = 'gpt' in cmd[0]
            gpt_completed = False
            if result.stdout or result.stderr:
                self._last_cmd_stdout = result.stdout or ''
                self._last_cmd_stderr = result.stderr or ''
            
            if is_gpt_command and result.stdout:

                if 'No product reader found for file' in result.stdout:
                    import re as _re
                    m = _re.search(r"No product reader found for file '([^']+)'", result.stdout)
                    corrupt_file = m.group(1) if m else "unknown"
                    print(f"\n  ✗ CRITICAL ERROR: Corrupt or unreadable zip file detected")
                    print(f"  ════════════════════════════════════════════════════════════════")
                    print(f"  SNAP cannot read the following file:")
                    print(f"    {corrupt_file}")
                    print(f"  ")
                    print(f"  This usually means the zip file is incomplete or corrupted.")
                    print(f"  ACTION REQUIRED:")
                    print(f"  1. Delete the corrupt file:")
                    print(f"       rm -f '{corrupt_file}'")
                    print(f"  2. Re-download it from ASF Vertex or your data source")
                    print(f"  3. Re-run this step")
                    print(f"  ════════════════════════════════════════════════════════════════\n")
                    raise RuntimeError(f"Corrupt zip file: {corrupt_file}")


                if 'No intersection with source product boundary' in result.stdout:
                    print(f"\n  ✗ CRITICAL ERROR: AOI does not intersect with SAR data extent")
                    print(f"  ════════════════════════════════════════════════════════════════")
                    print(f"  The clip_roi parameter in your config does not overlap with")
                    print(f"  the geographic extent of your SAR data.")
                    print(f"  ")
                    print(f"  ACTION REQUIRED:")
                    print(f"  1. Check the geographic extent of your input data")
                    print(f"  2. Update 'clip_roi' parameter in your config file to overlap")
                    print(f"     with your SAR scene boundaries")
                    print(f"  3. Run Step 8 again with corrected AOI coordinates")
                    print(f"  ════════════════════════════════════════════════════════════════\n")
                    return False
                

                if 'done.' in result.stdout or '100%' in result.stdout:
                    gpt_completed = True
            

            if is_gpt_command and result.stderr:
                if 'No product reader found for file' in result.stderr:
                    import re as _re
                    m = _re.search(r"No product reader found for file '([^']+)'", result.stderr)
                    corrupt_file = m.group(1) if m else "unknown"
                    print(f"\n  ✗ CRITICAL ERROR: Corrupt or unreadable zip file detected")
                    print(f"  ════════════════════════════════════════════════════════════════")
                    print(f"  SNAP cannot read the following file:")
                    print(f"    {corrupt_file}")
                    print(f"  ")
                    print(f"  This usually means the zip file is incomplete or corrupted.")
                    print(f"  ACTION REQUIRED:")
                    print(f"  1. Delete the corrupt file:")
                    print(f"       rm -f '{corrupt_file}'")
                    print(f"  2. Re-download it from ASF Vertex or your data source")
                    print(f"  3. Re-run this step")
                    print(f"  ════════════════════════════════════════════════════════════════\n")
                    raise RuntimeError(f"Corrupt zip file: {corrupt_file}")

                if 'No intersection with source product boundary' in result.stderr:
                    print(f"\n  ✗ CRITICAL ERROR: AOI does not intersect with SAR data extent")
                    print(f"  ════════════════════════════════════════════════════════════════")
                    print(f"  The clip_roi parameter in your config does not overlap with")
                    print(f"  the geographic extent of your SAR data.")
                    print(f"  ")
                    print(f"  ACTION REQUIRED:")
                    print(f"  1. Check the geographic extent of your input data")
                    print(f"  2. Update 'clip_roi' parameter in your config file to overlap")
                    print(f"     with your SAR scene boundaries")
                    print(f"  3. Run Step 8 again with corrected AOI coordinates")
                    print(f"  ════════════════════════════════════════════════════════════════\n")
                    return False
            

            if result.returncode != 0:

                if is_gpt_command and gpt_completed:

                    if expected_output_file:
                        if self.check_output_file_valid(expected_output_file):

                            return True
                        else:
                            print(f"  X GPT completed but output file is missing or invalid: {expected_output_file}")
                            return False
                    else:

                        return True
                

                if result.returncode < 0:
                    signal_num = -result.returncode
                    try:
                        signal_name = signal.Signals(signal_num).name
                    except (ValueError, AttributeError):
                        signal_name = "UNKNOWN"
                    print(f"  X Process killed by signal {signal_num} ({signal_name})")
                elif result.returncode > 128:
                    signal_num = result.returncode - 128
                    try:
                        signal_name = signal.Signals(signal_num).name
                    except (ValueError, AttributeError):
                        signal_name = "UNKNOWN"
                    print(f"  X Process killed by signal {signal_num} ({signal_name}) - return code {result.returncode}")
                else:
                    print(f"  X Command failed with return code {result.returncode}")
                    
                if result.stderr:
                    stderr_preview = result.stderr[:500]

                    if not (is_gpt_command and gpt_completed):
                        print(f"  Error: {stderr_preview}")
                return False
            

            if expected_output_file:
                if not self.check_output_file_valid(expected_output_file):
                    print(f"  X Command returned success but output file is missing or invalid: {expected_output_file}")
                    return False
            

            if is_gpt_command and result.stdout:
                if not gpt_completed:

                    if '%' in result.stdout:

                        import re
                        percentages = re.findall(r'(\d+)%', result.stdout)
                        if percentages:
                            max_pct = max(int(p) for p in percentages)
                            if max_pct < 100:
                                print(f"  X GPT command incomplete - only reached {max_pct}%")
                                return False
            
            return True
            
        except subprocess.TimeoutExpired:
            print(f"  X Command timed out after 2 hours")
            return False
        except KeyboardInterrupt:
            print(f"  X Process interrupted (Ctrl+C) - treating as failure and continuing...")
            return False
        except Exception as e:
            print(f"  X Command failed with exception: {e}")
            return False
    
    def run_commands_parallel(
            self, commands_data, description="Processing",
            reset_progress=True, progress_total=None):
        """Run GPT commands one at a time. parallel=true sets GPT -q via gpt_base_cmd()."""
        total_items = len(commands_data)
        if progress_total is not None:
            if reset_progress:
                self._release_progress_begin(progress_total)
            total = progress_total
        elif reset_progress:
            if total_items > 0:
                self._release_progress_begin(total_items)
            total = total_items
        else:
            total = getattr(self, '_release_progress_total', total_items) or total_items

        if self.use_parallel and self.savelog != 'release' and total_items > 1:
            print(f"  {description}: {total_items} items (sequential, GPT -q {self.threads} each)")

        success_list = []
        failed_list = []
        seq_done = getattr(self, '_release_progress_done', 0)
        for cmd_tuple in commands_data:
            if len(cmd_tuple) == 4:
                cmd, desc, item_name, expected_output = cmd_tuple
            else:
                cmd, desc, item_name = cmd_tuple
                expected_output = None

            try:
                success = self.run_command(cmd, desc, expected_output)
                if success:
                    success_list.append(item_name)
                else:
                    failed_list.append(item_name)
            except Exception as e:
                print(f"  X {item_name} - Unexpected exception: {e}")
                failed_list.append(item_name)
            seq_done += 1
            self._release_progress_done = seq_done
            self._release_progress_tick(seq_done, total)

        if self.savelog != 'release' and total_items > 1:
            print(f"  Completed: {len(success_list)}/{total_items}")
            if failed_list:
                print(f"  Failed: {len(failed_list)} items")

        return success_list, failed_list
    
    def load_baseline_file(self, baseline_file):
        self.baseline_data = {}
        self.master_date = None
        with open(baseline_file, 'r') as f:
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

                        self.baseline_data[master] = {
                            'temporal': 0,
                            'perpendicular': 0
                        }


                    self.baseline_data[slave] = {
                        'temporal': temporal,
                        'perpendicular': perpendicular
                    }
    
    def load_baseline_data(self):

        metadata_baseline = os.path.join(self.proj_root, 'metadata_info', 'baselines')
        baselines_ps = os.path.join(self.proj_root, 'metadata_info', 'baselines_ps')
        baselines_sbas = os.path.join(self.proj_root, 'metadata_info', 'baselines_sbas')
        

        if os.path.exists(baselines_sbas):
            baseline_file = baselines_sbas
        elif os.path.exists(baselines_ps):
            baseline_file = baselines_ps
        elif os.path.exists(metadata_baseline):
            baseline_file = metadata_baseline
        else:
            baseline_files = glob.glob(os.path.join(self.config['input_data'], '*baseline*'))
            if not baseline_files:
                return False
            baseline_file = baseline_files[0]
        
        self.load_baseline_file(baseline_file)
        
        return self.master_date is not None
    
    def is_check_only(self):
        v = self.config.get('check_only', False)
        if isinstance(v, bool):
            return v
        return str(v).strip().lower() in ('true', '1', 'yes', 'on')

    def is_ps_mode(self):
        return self.insar_target == 2 and self.insar_method == 2

    def _pair_names_excluding_master(self, pair_names):
        m = self.master_date
        if not m:
            return sorted(pair_names)
        kept = []
        for pn in pair_names:
            parts = pn.split('_')
            if len(parts) != 2:
                kept.append(pn)
                continue
            if parts[0] == m or parts[1] == m:
                continue
            kept.append(pn)
        return sorted(kept)

    def _select_ps_pairs_from_names(self, pair_names):
        all_pairs = self._pair_names_excluding_master(pair_names)
        if not all_pairs:
            return {}, []

        pair_tuples = []
        for pn in all_pairs:
            parts = pn.split('_')
            if len(parts) == 2:
                pair_tuples.append((parts[0], parts[1]))
        pair_set = set(pair_tuples)

        dates = sorted({d for d1, d2 in pair_tuples for d in (d1, d2)})
        if len(dates) < 2:
            return {}, []

        first = dates[0]
        last = dates[-1]
        middle = dates[1:-1]

        selected = []
        selected_set = set()
        pair_to_target_dates = {}
        processed_dates = set()

        def _record_pair(d1, d2, new_dates):
            pair = (d1, d2)
            if pair in selected_set:
                return
            selected.append(pair)
            selected_set.add(pair)
            pn = f"{d1}_{d2}"
            pair_to_target_dates[pn] = sorted(set(new_dates))
            processed_dates.update(new_dates)


        for d in middle:
            if (first, d) in pair_set:
                _record_pair(first, d, [first, d])
                break


        changed = True
        while changed:
            changed = False
            remaining_middle = [d for d in middle if d not in processed_dates]
            if not remaining_middle:
                break


            for md in remaining_middle:
                found = False
                for other in remaining_middle:
                    if md == other:
                        continue
                    pair = (md, other)
                    if pair in pair_set and pair not in selected_set:
                        _record_pair(md, other, [md, other])
                        changed = True
                        found = True
                        break
                if found:
                    break
            if changed:
                continue


            for md in remaining_middle:
                found = False
                for other in dates:
                    if md == other or other == last:
                        continue
                    pair = (other, md)
                    if pair in pair_set and pair not in selected_set:
                        _record_pair(other, md, [md, other])
                        changed = True
                        found = True
                        break
                if found:
                    break


        if last not in processed_dates:
            found = False
            for d1, d2 in pair_tuples:
                if d1 == last:
                    pair = (d1, d2)
                    if pair not in selected_set:
                        _record_pair(d1, d2, [last, d2])
                        found = True
                        break
            if not found:
                for d1, d2 in pair_tuples:
                    if d2 == last:
                        pair = (d1, d2)
                        if pair not in selected_set:
                            _record_pair(d1, d2, [d1, last])
                            break

        selected_names = [f"{d1}_{d2}" for d1, d2 in selected]
        return pair_to_target_dates, selected_names

    def _ensure_ps_pairs_selected(self):

        if self.ps_selected_pair_names:
            return True
        return self.load_sbas_pairs()

    def _print_ps_pair_selection_summary(self, n_file):
        print(f"\n{'─'*80}")
        print(f"  PS mode (insar_method=2): {len(self.ps_selected_pair_names)} of "
              f"{n_file} SBAS pair rows selected from sbas_pairs.txt")
        print(f"  PS pair filter applies to processing steps 3–12 and 15–25 (not step 26)")
        print(f"  (excludes pairs with master {self.master_date} as date1/date2)")
        for pn in sorted(self.ps_selected_pair_names):
            targets = self.ps_pair_to_target_dates.get(pn, [])
            t_fmt = ', '.join(self.convert_date_format(d) for d in targets)
            print(f"    • {pn}  →  target slave date(s): {t_fmt}")
        if self.selected_dates:
            print(f"\n  Acquisitions for slices/coreg (master + selected pairs): "
                  f"{', '.join(self.selected_dates)}")
        print(f"{'─'*80}\n")
    
    def _pair_contains_master(self, pair_dict, master):
        if not master:
            return False
        return pair_dict['date1'] == master or pair_dict['date2'] == master
    
    def _skip_pair_step03_rule(self, date1, date2):
        m = self.master_date
        if not m:
            return False
        return date1 == m or date2 == m
    
