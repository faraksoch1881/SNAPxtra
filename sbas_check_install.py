#!/usr/bin/env python3
"""
Script to check required libraries, SNAP, SNAPHU and Graph files to run snapxtra_main.py

Usage:
    python check_install.py insar_proj.config


Instructions
    1. Install SNAP (http://step.esa.int/main/download/snap-download/) first (provides GPT)
    2. Download SNAPHU (https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/) and add path of snaphu to *.config file
    3. Install Conda and create environment
    4. activate environment and run sbas_check_install.py to install required libraries automatically



Usage:
    python check_install.py insar_proj.config
    python check_install.py insar_proj.config -y


# 2026.09.30 "Sagar Rawal"
# email: getsrawal@gmail.com
"""

import argparse
import subprocess
import sys
import shutil
import platform
import os
from pathlib import Path

CONDA_PREFERRED = {
    'numpy', 'shapely', 'matplotlib', 'gdal', 'psutil', 'rasterio', 'scipy',
}

CONDA_NAME_MAP = {
    'osgeo': 'gdal',
    'numpy': 'numpy',
    'shapely': 'shapely',
    'matplotlib': 'matplotlib',
    'psutil': 'psutil',
    'rasterio': 'rasterio',
    'scipy': 'scipy',
}

PIP_NAME_MAP = {
    'osgeo': 'gdal',
    'numpy': 'numpy',
    'shapely': 'shapely',
    'matplotlib': 'matplotlib',
    'psutil': 'psutil',
    'rasterio': 'rasterio',
    'scipy': 'scipy',
}

PYTHON_REQUIRED_MODULES = {
    'numpy': 'numpy',
    'shapely': 'shapely',
    'matplotlib': 'matplotlib',
    'osgeo': 'osgeo.gdal',
    'psutil': 'psutil',
    'rasterio': 'rasterio',
    'scipy': 'scipy',
}

MAX_INSTALL_ATTEMPTS = 3

COL_MODULE = 25
COL_VERSION = 20
COL_STATUS = 10


def print_header(text):
    print(f"\n>{text}")


def print_success(text):
    print(f" ✓ {text}")


def print_fail(text):
    print(f" ✗ {text}")


def print_warning(text):
    print(f" ! {text}")


def print_info(text):
    print(f" {text}")


def print_module_table(rows):
    """Print Module / Version / Status table (plain text, no colors)."""
    header = f"{'Module':<{COL_MODULE}}{'Version':<{COL_VERSION}}{'Status':<{COL_STATUS}}"
    sep = '-' * (COL_MODULE + COL_VERSION + COL_STATUS)
    print(header)
    print(sep)
    for name, version, ok in rows:
        status = '✓ OK' if ok else '✗ Missing'
        ver = version if version else 'N/A'
        print(f"{name:<{COL_MODULE}}{ver:<{COL_VERSION}}{status:<{COL_STATUS}}")
    print()


def read_config(config_file):
    """Read configuration file and extract parameters."""
    config = {}
    try:
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    parts = line.split('=', 1)
                    key = parts[0].strip()
                    value = parts[1].strip().split('#')[0].strip()
                    if value:
                        config[key] = value
        return config
    except Exception as e:
        print_fail(f"Failed to read config file: {e}")
        return None

def is_command_available(cmd):
    """Check if a command is available in PATH."""
    return shutil.which(cmd) is not None

def get_command_version(cmd, version_flag='--version'):
    """Get version of a command if available."""
    try:
        result = subprocess.run(
            [cmd, version_flag],
            capture_output=True,
            text=True,
            timeout=5
        )
        return result.stdout.strip().split('\n')[0] if result.returncode == 0 else "unknown"
    except:
        return "unknown"

def check_python_module(module_name, import_name=None):
    """Check if a Python module is available."""
    if import_name is None:
        import_name = module_name
    
    try:
        if module_name == 'numpy':
            import numpy as np
            return True, np.__version__
        elif module_name == 'shapely':
            import shapely
            return True, shapely.__version__
        elif module_name == 'osgeo':
            from osgeo import gdal
            return True, getattr(gdal, '__version__', 'unknown')
        else:
            __import__(import_name)
            module = sys.modules[import_name]
            version = getattr(module, '__version__', 'unknown')
            return True, version
    except ImportError:
        return False, None

def is_conda_installed():
    try:
        subprocess.run(['conda', '--version'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

def is_mamba_available():
    try:
        subprocess.run(['mamba', '--version'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False

def install_conda_packages(packages):
    if not packages:
        return True
    print_info(f"Conda: {packages}")
    solver = 'mamba' if is_mamba_available() else 'conda'
    return subprocess.run(
        [solver, 'install', '-y', '-c', 'conda-forge'] + packages,
        check=False,
    ).returncode == 0

def install_pip_packages(packages):
    if not packages:
        return True
    print_info(f"Pip: {packages}")
    return subprocess.run(
        [sys.executable, '-m', 'pip', 'install'] + packages,
        check=False,
    ).returncode == 0

def get_missing_python_modules(module_map):
    missing = []
    for module_name, import_name in module_map.items():
        available, _ = check_python_module(module_name, import_name)
        if not available:
            missing.append(module_name)
    return missing

def install_python_modules(missing_modules, auto_install=False):
    """Install missing Python modules via conda (preferred) or pip."""
    if not missing_modules:
        return True

    remaining = list(missing_modules)
    for attempt in range(1, MAX_INSTALL_ATTEMPTS + 1):
        if not remaining:
            break

        print(f"\n? Attempt {attempt} of {MAX_INSTALL_ATTEMPTS} to install missing modules.")

        conda_packages = sorted({
            CONDA_NAME_MAP[m] for m in remaining if CONDA_NAME_MAP.get(m, m) in CONDA_PREFERRED
        })
        pip_packages = sorted({
            PIP_NAME_MAP[m] for m in remaining
            if CONDA_NAME_MAP.get(m, m) not in CONDA_PREFERRED
        })

        if conda_packages:
            print_info(f"Conda: {conda_packages}")
        if pip_packages:
            print_info(f"Pip: {pip_packages}")

        if not auto_install and attempt == 1:
            choice = input("\nInstall missing modules now? [Y/n]: ").strip().lower()
            if choice not in ('', 'y', 'yes'):
                print_warning("Skipping automatic Python package installation.")
                return False

        if conda_packages and is_conda_installed():
            install_conda_packages(conda_packages)
        elif conda_packages:
            print_warning("Conda not found; using pip for conda-preferred packages.")
            pip_packages = sorted(set(pip_packages) | set(conda_packages))

        if pip_packages:
            install_pip_packages(pip_packages)

        still_missing = []
        for mod in remaining:
            import_name = PYTHON_REQUIRED_MODULES.get(mod) or mod
            available, version = check_python_module(mod, import_name)
            if available:
                print_success(f"{mod}: {version}")
            else:
                still_missing.append(mod)

        if still_missing and attempt < MAX_INSTALL_ATTEMPTS:
            print_warning(f"Retrying per-package install for: {', '.join(still_missing)}")
            for mod in still_missing[:]:
                pkg_conda = CONDA_NAME_MAP.get(mod, mod)
                pkg_pip = PIP_NAME_MAP.get(mod, mod)
                ok = False
                if is_conda_installed() and pkg_conda in CONDA_PREFERRED:
                    ok = install_conda_packages([pkg_conda])
                if not ok:
                    ok = install_pip_packages([pkg_pip])
                import_name = PYTHON_REQUIRED_MODULES.get(mod) or mod
                available, version = check_python_module(mod, import_name)
                if available:
                    print_success(f"{mod}: {version}")
                    still_missing.remove(mod)
                else:
                    print_fail(f"{mod}: still missing after install attempt")

        remaining = still_missing

    if remaining:
        print_fail(f"Could not install: {', '.join(remaining)}")
        need_conda = sorted({CONDA_NAME_MAP[m] for m in remaining if CONDA_NAME_MAP.get(m, m) in CONDA_PREFERRED})
        need_pip = sorted({PIP_NAME_MAP[m] for m in remaining if CONDA_NAME_MAP.get(m, m) not in CONDA_PREFERRED})
        if need_conda:
            print_info("Manual conda: conda install -c conda-forge " + ' '.join(need_conda))
        if need_pip:
            print_info("Manual pip: pip install " + ' '.join(need_pip))
        return False

    print_success("All requested Python packages installed.")
    return True

def check_snap_installation(config):
    """Check SNAP installation and GPT availability from config."""
    print_header("Checking SNAP/GPT:")
    
    snap_found = False
    gpt_path = config.get('gpt_loc')
    
    if gpt_path:
        print_info(f"Checking GPT from config: {gpt_path}")
        if os.path.exists(gpt_path):
            print_success(f"GPT found: {gpt_path}")
            snap_found = True
            
            # Check SNAP version
            try:
                result = subprocess.run(
                    [gpt_path, '--diag'],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    # Parse SNAP info
                    for line in result.stdout.split('\n'):
                        if 'SNAP Release version' in line:
                            print_success(f"Version: {line.split('version')[-1].strip()}")
                        elif 'Max memory:' in line:
                            print_info(f"Max memory: {line.split(':')[-1].strip()}")
                        elif 'Cache size:' in line:
                            print_info(f"Cache size: {line.split(':')[-1].strip()}")
                        elif 'Tile parallelism:' in line:
                            print_info(f"Tile parallelism: {line.split(':')[-1].strip()}")
                else:
                    print_warning("GPT found but --diag failed")
            except Exception as e:
                print_warning(f"Could not verify GPT installation: {e}")
        else:
            print_fail(f"GPT not found at: {gpt_path}")
            print_info("Update gpt_loc in config file")
    else:
        print_fail("gpt_loc not specified in config file")
        print_info("Add gpt_loc=/path/to/snap/bin/gpt in config")
    
    return snap_found

def check_snaphu_installation(config):
    """Check SNAPHU installation from config (snaphu_bin or snaphu_install)."""
    print_header("Checking SNAPHU:")
    
    snaphu_path = config.get('snaphu_bin')
    
    if snaphu_path:
        print_info(f"Checking SNAPHU from config: {snaphu_path}")
        
        # Check if it's a directory or file
        if os.path.isdir(snaphu_path):
            # It's a directory, look for snaphu binary inside
            snaphu_exe = os.path.join(snaphu_path, 'bin', 'snaphu')
            if os.path.exists(snaphu_exe):
                print_success(f"SNAPHU found: {snaphu_exe}")
                
                # Test run snaphu
                try:
                    result = subprocess.run(
                        [snaphu_exe],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if 'snaphu v' in result.stdout:
                        version_line = result.stdout.split('\n')[0]
                        print_success(f"Version: {version_line}")
                    else:
                        print_warning("SNAPHU found but version check failed")
                    return True
                except Exception as e:
                    print_warning(f"Could not verify SNAPHU: {e}")
                    return True  # File exists, assume it works
            else:
                print_fail(f"SNAPHU not found at: {snaphu_exe}")
                return False
        elif os.path.exists(snaphu_path):
            # It's a file
            print_success(f"SNAPHU found: {snaphu_path}")
            return True
        else:
            print_fail(f"SNAPHU not found at: {snaphu_path}")
            print_info("Update snaphu_bin in config file")
            return False
    else:
        print_fail("snaphu_bin / snaphu_install not specified in config file")
        print_info("Add snaphu_bin=/path/to/snaphu or snaphu_install=/path/to/snaphu in config")
        return False

def check_graph_files(config):
    """Check required XML graph files from config graph_path."""
    print_header("Checking SNAP Graph XML files:")
    
    graph_path = config.get('graph_path') 
    print_info(f"Graph path: {graph_path}")
    
    if not graph_path:
        print_fail("graph_path not specified in config file")
        print_info("Add graph_path=/path/to/multi_graphs in config")
        return False
    
    if not os.path.isdir(graph_path):
        print_fail(f"Graph directory not found: {graph_path}")
        return False
    
    print_success(f"Graph directory exists: {graph_path}")

    required_xmls = [
        '02_backgeo_esd.xml',
        '02_backgeo_esd_mF.xml',
        '02_backgeo_single_burst.xml',
        '03_multi_intf.xml',
        '05_merge_swath.xml',
        'merge_addband.xml',
        'merge_addband_mswath.xml',
        'merge_swath_sbas.xml',
        'sbas_export.xml',
        'single_slice.xml',
        'single_slice_wkt.xml',
        'slice_assembly.xml',
        'snaphu_import.xml',
        'step0_parse_baseline_metadata.xml',
        'step10_phase_preview_bandmath.xml',
        'subset_intf.xml',
    ]

    rows = []
    all_found = True
    for xml_file in required_xmls:
        xml_path = os.path.join(graph_path, xml_file)
        ok = os.path.exists(xml_path)
        if not ok:
            all_found = False
        rows.append((xml_file, 'present' if ok else 'N/A', ok))

    print()
    print_module_table(rows)
    
    try:
        all_xmls = sorted([f for f in os.listdir(graph_path) if f.endswith('.xml')])
        extra_xmls = [x for x in all_xmls if x not in required_xmls]
        
        if extra_xmls:
            print_info("Present but not used by ps_sbas_snapxtra.py:")
            for xml_file in extra_xmls:
                print_info(f"  {xml_file}")
    except OSError:
        pass
    
    return all_found

def check_local_scripts():
    """Check companion Python scripts in the same directory."""
    print_header("Checking local script files:")
    
    script_dir = Path(__file__).resolve().parent
    required_scripts = [
        'ps_sbas_snapxtra.py',
        'lib/sbas_pair_builder.py',
    ]
    
    rows = []
    all_ok = True
    for name in required_scripts:
        path = script_dir / name
        ok = path.is_file()
        if not ok:
            all_ok = False
        rows.append((name, 'present' if ok else 'N/A', ok))

    print()
    print_module_table(rows)
    
    return all_ok

def check_python_dependencies(auto_install=False):
    """Check Python module dependencies used by ps_sbas_snapxtra.py."""
    print_header("Checking required modules:")
    
    rows = []
    for module_name, import_name in PYTHON_REQUIRED_MODULES.items():
        available, version = check_python_module(module_name, import_name)
        rows.append((module_name, version if available else 'N/A', available))

    print()
    print_module_table(rows)

    missing_required = get_missing_python_modules(PYTHON_REQUIRED_MODULES)
    all_ok = not missing_required
    
    if missing_required:
        installed = install_python_modules(missing_required, auto_install=auto_install)
        # Re-print table after install attempts
        rows = []
        for module_name, import_name in PYTHON_REQUIRED_MODULES.items():
            available, version = check_python_module(module_name, import_name)
            rows.append((module_name, version if available else 'N/A', available))
        print_header("Checking required modules (after install):")
        print()
        print_module_table(rows)
        missing_required = get_missing_python_modules(PYTHON_REQUIRED_MODULES)
        all_ok = not missing_required and installed
    elif not all_ok:
        print_info("Install missing modules:")
        print_info("  conda install -c conda-forge numpy shapely matplotlib gdal psutil rasterio scipy")
        print_info("  OR")
        print_info("  pip install numpy shapely matplotlib gdal psutil rasterio scipy")
    
    return all_ok

def check_system_utilities(auto_install=False):
    """Check required system utilities."""
    print_header("Checking system utilities:")
    
    required_utils = ['bc', 'awk', 'sed', 'gdal_translate', 'gdalinfo']
    optional_utils = ['wget', 'curl', 'gmt', 'gdal_edit.py']
    
    all_ok = True
    missing_required = []
    
    rows = []
    for util in required_utils:
        if is_command_available(util):
            rows.append((util, shutil.which(util) or 'found', True))
        else:
            rows.append((util, 'N/A', False))
            missing_required.append(util)
            all_ok = False

    print()
    print_info("Required:")
    print_module_table(rows)

    opt_rows = []
    for util in optional_utils:
        if is_command_available(util):
            opt_rows.append((util, shutil.which(util) or 'found', True))
        else:
            opt_rows.append((util, 'N/A', False))

    print_info("Optional:")
    print_module_table(opt_rows)
    
    gdal_utils_missing = [u for u in missing_required if u.startswith('gdal')]
    if gdal_utils_missing and auto_install and is_conda_installed():
        print_info("Attempting to install GDAL utilities via conda-forge...")
        if install_conda_packages(['gdal']):
            for util in gdal_utils_missing:
                if is_command_available(util):
                    print_success(f"{util}: {shutil.which(util)}")
                    missing_required.remove(util)
            all_ok = not missing_required
    elif gdal_utils_missing:
        print_info("Install GDAL tools: conda install -c conda-forge gdal")
    
    return all_ok

def check_disk_space():
    """Check available disk space."""
    print_header("Checking disk space:")
    
    try:
        stat = os.statvfs(os.getcwd())
        free_gb = (stat.f_bavail * stat.f_frsize) / (1024**3)
        
        if free_gb >= 100:
            print_success(f"Available disk space: {free_gb:.1f} GB")
        elif free_gb >= 50:
            print_warning(f"Available disk space: {free_gb:.1f} GB (recommended: 100+ GB)")
        else:
            print_fail(f"Available disk space: {free_gb:.1f} GB (insufficient for SBAS processing)")
            return False
        
        return True
    except:
        print_warning("Could not check disk space")
        return True

def print_installation_guide(results):
    """Print installation guide based on detected OS and missing components."""
    print_header("Installation guide:")
    
    os_type = platform.system()
    missing_components = [k for k, v in results.items() if not v]
    
    if not missing_components:
        return
    
    if os_type == "Linux":
        distro = platform.freedesktop_os_release().get('ID', 'linux')
        print_info(f"For {distro.capitalize()}:")
        
        step = 1
        
        # Only show SNAP installation if it's missing
        if not results.get('snap', True):
            print_info(f"{step}. Install SNAP:")
            print_info("   Download: http://step.esa.int/main/download/snap-download/")
            print_info("   chmod +x esa-snap_sentinel_unix_*.sh")
            print_info("   ./esa-snap_sentinel_unix_*.sh")
            print()
            step += 1
        
        # Only show Python dependencies if they're missing
        if not results.get('python_modules', True):
            print_info(f"{step}. Install Python dependencies (Conda - RECOMMENDED):")
            print_info("   conda env create -f sbas_environment.yml")
            print_info("   conda activate sbas")
            print()
            step += 1
        
        # Only show SNAPHU installation if it's missing
        if not results.get('snaphu', True):
            print_info(f"{step}. Install SNAPHU:")
            print_info("   wget https://web.stanford.edu/group/radar/softwareandlinks/sw/snaphu/snaphu-v2.0.5.tar.gz")
            print_info("   tar -xzf snaphu-v2.0.5.tar.gz")
            print_info("   cd snaphu-v2.0.5/src")
            print_info("   make")
            print_info("   sudo cp snaphu /usr/local/bin/")
            print()
            step += 1
        
    elif os_type == "Darwin":
        print_info("For macOS:")
        step = 1
        if not results.get('snap', True):
            print_info(f"{step}. Install SNAP from: http://step.esa.int/main/download/snap-download/")
            step += 1
        if not results.get('python_modules', True):
            print_info(f"{step}. conda env create -f sbas_environment.yml")
            step += 1
        if not results.get('snaphu', True):
            print_info(f"{step}. Install SNAPHU (compile from source)")
            step += 1
        
    elif os_type == "Windows":
        print_info("For Windows:")
        step = 1
        if not results.get('snap', True):
            print_info(f"{step}. Install SNAP from: http://step.esa.int/main/download/snap-download/")
            step += 1
        if not results.get('python_modules', True):
            print_info(f"{step}. conda env create -f sbas_environment.yml")
            step += 1
        if not results.get('snaphu', True):
            print_info(f"{step}. Use WSL or Linux for SNAPHU")
            step += 1
    
    print()

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description='Check and optionally install SBAS pipeline dependencies.'
    )
    parser.add_argument('config_file', help='Path to insar project config file')
    parser.add_argument(
        '-y', '--yes',
        action='store_true',
        help='Install missing Python packages without prompting',
    )
    return parser.parse_args(argv)

def main():
    """Run all checks."""
    args = parse_args(sys.argv[1:])
    config_file = args.config_file
    auto_install = args.yes
    
    if not os.path.exists(config_file):
        print_fail(f"Error: Config file not found: {config_file}")
        return 1
    
    print(f"\n>SBAS InSAR Processing - Dependency Check")
    print_info(f"Config: {config_file}")
    print_info(f"Python: {sys.version.split()[0]}")
    print_info(f"Platform: {platform.system()} {platform.release()}")
    if auto_install:
        print_info("Auto-install: enabled")
    
    # Read config
    print_info(f"Reading configuration from: {config_file}")
    config = read_config(config_file)
    
    if config is None:
        return 1
    
    print_success(f"Config loaded: {len(config)} parameters found")
    
    results = {}
    
    # Check SNAP
    results['snap'] = check_snap_installation(config)
    
    # Check SNAPHU
    results['snaphu'] = check_snaphu_installation(config)
    
    # Check graph files
    results['graphs'] = check_graph_files(config)
    
    # Check local pipeline scripts
    results['scripts'] = check_local_scripts()
    
    # Check Python dependencies
    results['python_modules'] = check_python_dependencies(auto_install=auto_install)
    
    # Check system utilities
    results['utils'] = check_system_utilities(auto_install=auto_install)
    
    # Check disk space
    results['disk'] = check_disk_space()
    
    # Print summary
    print_header("Summary:")
    
    if all(results.values()):
        print_success("All dependencies satisfied.")
        print_info("Your system is ready for SBAS processing.")
    else:
        print_warning("Some dependencies are missing.")
        missing = [k for k, v in results.items() if not v]
        print_info(f"Missing/Issues: {', '.join(missing)}")
        print()
        print_installation_guide(results)
    
    # Exit code
    return 0 if all(results.values()) else 1

if __name__ == '__main__':
    sys.exit(main())
