#!/usr/bin/env python3
"""
This workflow is used to process the Sentinel-1 SLC TOPS data generate interferometric stacks that can be analyzed for time series analysis and surface deformation using LiCSBAS and StaMPS. The generated interferometric stacks can be exported in format that can be used directly in LiCSBAS or StaMPS. 

Usage:
    python insar_sbas_multi_op.py <config_file>                # Run all steps (0–27) or use step range from config
    python insar_sbas_multi_op.py <config_file> -s 3           # start_step=3, end_step cleared → run to last step
    python insar_sbas_multi_op.py <config_file> -s 3 -e 10     # Set start_step/end_step in config, then batch run
    python insar_sbas_multi_op.py <config_file> -s 3 -e 10 -t 2 -m 1  # Also set insar_target / insar_method, then batch
    python insar_sbas_multi_op.py <config_file> baselines      # Run Step 0 only (baselines mode)
    python insar_sbas_multi_op.py <step_number> <config_file>  # Run specific step
    
    step_number: 0–27 (StaMPS export is step 27 when insar_target=2; optional coreg mask steps 25–26)
    config_file: path to insar_proj.config file


Output Directory Structure:
    proj_{asc|dsc}/
    ├── bad_pair.txt           # Optional: Manually excluded pairs
    ├── snaphu_error.log       # Auto-generated: Failed SNAPHU pairs
    ├── phase_img_{master}/    # Step 20: Wrapped phase PNG previews (optional)
    ├── unw_img_{master}/      # Step 21: Unwrapped phase PNG collection (terrain correction)
    ├── metadata_info/
    │   ├── baselines      - Single Swath
        ├── data/                             # From main workflow Step 1
        ├── coreg_stack_{master}/             # Step 1: Coregistered stacks
        ├── coreg_stack_mask_{master}/        # Step 2: Land-sea masked stacks
        ├── coreg_stack_mask_deb_{master}/    # Step 3: Debursted stacks
        ├── multi_intf_deb_{master}/          # Step 5: Multi-interferograms
        ├── multi_intf_deb_subset_{master}/   # Step 7: Subset (optional if clip_roi)
        ├── multi_intf_deb_ml_{master}/       # Step 14: Multilooked interferograms
        ├── multi_intf_deb_ml_flt_{master}/   # Step 18: Goldstein-filtered multilook
        ├── multi_snaphu_{master}/            # Step 19: SNAPHU export files
        ├── multi_unw_{master}/               # Step 22: Unwrapped phase (import)
        ├── multi_tc_{master}/                # Step 23: Terrain-corrected
        └── GEOC/                             # Step 24: GeoTIFF exports
    
    Multi-Swath Structure (swath=12):
    ├── IW1/
    │   ├── coreg_stack_{master}/
    │   ├── coreg_stack_mask_{master}/
    │   ├── coreg_stack_mask_deb_{master}/
    │   └── multi_intf_deb_{master}/
    ├── IW2/
    │   ├── coreg_stack_{master}/
    │   ├── coreg_stack_mask_{master}/
    │   ├── coreg_stack_mask_deb_{master}/
    │   └── multi_intf_deb_{master}/
    └── merge_IW1_IW2/                        # Merged outputs (Steps 5-17)
        ├── multi_intf_deb_mrg_{master}/
        ├── multi_intf_deb_mrg_subset_{master}/  # Step 7: Subset (optional if clip_roi)
        ├── multi_intf_deb_mrg_ml_{master}/
        ├── multi_intf_deb_mrg_ml_flt_{master}/
        ├── multi_snaphu_{master}/
        ├── multi_unw_{master}/
        ├── multi_tc_{master}/
        └── GEOC/
            ├── {first_pair}.geo.mli.tif      # Intensity
            └── {first_pair}-poly.kml         # Footprint


# 2026.09.30 "Sagar Rawal"
# email: getsrawal@gmail.com

"""
import os
import sys
import signal

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lib.snapxtra_processor import SBASMultiProcessor
from lib.snapxtra_cli import (
    _parse_cli_argv,
    _print_cli_usage,
    write_config_batch_overrides,
    _write_config_key,
    _insar_mode_label,
)


def main():
    def signal_handler(signum, frame):
        try:
            signal_name = signal.Signals(signum).name
        except (ValueError, AttributeError):
            signal_name = str(signum)
        print(f"\n! Received signal {signum} ({signal_name}) - continuing processing...")

    signal.signal(signal.SIGTERM, signal_handler)

    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        _print_cli_usage()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    try:
        (config_file, step_num, baselines_mode, cli_start, cli_end,
         cli_target, cli_method) = _parse_cli_argv(sys.argv[1:])
    except ValueError as e:
        print(f"ERROR: {e}")
        _print_cli_usage()
        sys.exit(1)

    config_file = os.path.abspath(config_file)

    cli_batch_opts = (
        cli_start is not None or cli_end is not None
        or cli_target is not None or cli_method is not None
    )

    if cli_batch_opts and step_num is not None:
        print("ERROR: -s / -e / -t / -m cannot be used with <step_number> <config_file> (batch mode only)")
        sys.exit(1)

    if cli_batch_opts:
        if baselines_mode:
            print("ERROR: -s / -e / -t / -m cannot be used with baselines mode")
            sys.exit(1)
        clear_end = cli_start is not None and cli_end is None
        if cli_start is not None:
            _write_config_key(config_file, 'batch_wall_start', '')
        write_config_batch_overrides(
            config_file,
            start_step=cli_start,
            end_step=cli_end,
            clear_end_step=clear_end,
            insar_target=cli_target,
            insar_method=cli_method,
        )
        parts = []
        if cli_start is not None:
            parts.append(f"start_step={cli_start}")
        if cli_end is not None:
            parts.append(f"end_step={cli_end}")
        elif clear_end:
            parts.append("end_step=")
        if cli_target is not None:
            parts.append(f"insar_target={cli_target}")
        if cli_method is not None:
            parts.append(f"insar_method={cli_method}")
        print(f"Updated {config_file}: {', '.join(parts)}")
        if cli_target is not None and cli_method is not None:
            print(f"  Mode: {_insar_mode_label(cli_target, cli_method)}")
        if cli_start is not None and cli_end is not None:
            print(f"  Batch run: steps {cli_start}–{cli_end}")
        elif cli_start is not None and cli_end is None:
            print(
                f"  Batch run: from step {cli_start} through workflow end "
                "(end_step blank → 24 LiCSBAS / 27 StaMPS)"
            )

    if not os.path.exists(config_file):
        print(f"ERROR: Config file not found: {config_file}")
        sys.exit(1)

    processor = None
    if baselines_mode:
        try:
            processor = SBASMultiProcessor(config_file, skip_master_check=True)
            if not processor.step00_create_baselines_and_network():
                print("ERROR: Failed to create baselines and SBAS network")
                sys.exit(1)

            print(f"\n{'='*80}")
            print("✓ Baseline Created")
            print("✓ Network Graph  Created")
            print("✓ Metadata Created")
            print(f"{'='*80}")
            print("  Generated files:")
            meta_dir = os.path.join(processor.proj_root, 'metadata_info')
            print(f"    - {os.path.join(meta_dir, 'baselines')}")
            print(f"    - {os.path.join(meta_dir, 'sbas_pairs.txt')}")
            print(f"    - {os.path.join(meta_dir, 'network_sbas.png')}")
            print(f"    - {os.path.join(meta_dir, 'network_sbas.pdf')}")
            print(f"    - {os.path.join(meta_dir, 'network_ps.png')}")
            print(f"    - {os.path.join(meta_dir, 'network_ps.pdf')}")

            connect_sb = os.path.join(meta_dir, 'connect_sb.txt')
            if os.path.exists(connect_sb):
                print(f"    - {connect_sb} (bridging pairs)")

            print(f"\n  Next step: python ps_sbas_snapxtra.py 1 {config_file}")
            print("             (or run steps 2+ to continue processing)\n")
            sys.exit(0)
        except Exception as e:
            print("\nERROR: Baselines mode failed")
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            if processor is not None:
                processor._teardown_session_logging()
        return

    try:
        skip_master = (step_num == 0)
        processor = SBASMultiProcessor(config_file, skip_master_check=skip_master)
        if cli_start is not None:
            processor._reset_batch_wall_start = True

        if step_num is None:
            success = processor.run_all_steps()
        else:
            success = processor.run_specific_step(step_num)

        if not success:
            sys.exit(1)
    except Exception as e:
        print("\nERROR: Processing failed")
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if processor is not None:
            processor._teardown_session_logging()


if __name__ == "__main__":
    main()
