"""CLI parsing and config batch overrides."""
import os
import sys

def _write_config_key(config_path, key, value):
    value_str = str(value)
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


def write_config_step_range(
        config_path, start_step=None, end_step=None, clear_end_step=False):
    if start_step is not None:
        _write_config_key(config_path, 'start_step', start_step)
    if clear_end_step:
        _write_config_key(config_path, 'end_step', '')
    elif end_step is not None:
        _write_config_key(config_path, 'end_step', end_step)


def write_config_batch_overrides(
        config_path, start_step=None, end_step=None, clear_end_step=False,
        insar_target=None, insar_method=None):
    write_config_step_range(
        config_path, start_step=start_step, end_step=end_step,
        clear_end_step=clear_end_step)
    if insar_target is not None:
        _write_config_key(config_path, 'insar_target', insar_target)
    if insar_method is not None:
        _write_config_key(config_path, 'insar_method', insar_method)


def _insar_mode_label(insar_target, insar_method):
    if insar_target == 1:
        return "LiCSBAS"
    if insar_target == 2 and insar_method == 1:
        return "StaMPS SBAS"
    if insar_target == 2 and insar_method == 2:
        return "StaMPS PS"
    return f"insar_target={insar_target}, insar_method={insar_method}"


def _parse_cli_argv(argv):
    start_step = None
    end_step = None
    insar_target = None
    insar_method = None
    positional = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ('-s', '--start-step'):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} requires a step number")
            start_step = int(argv[i + 1])
            i += 2
            continue
        if tok in ('-e', '--end-step'):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} requires a step number")
            end_step = int(argv[i + 1])
            i += 2
            continue
        if tok in ('-t', '--insar-target', '--target'):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} requires a value (1=LiCSBAS, 2=StaMPS)")
            insar_target = int(argv[i + 1])
            if insar_target not in (1, 2):
                raise ValueError("insar_target must be 1 (LiCSBAS) or 2 (StaMPS)")
            i += 2
            continue
        if tok in ('-m', '--insar-method', '--method'):
            if i + 1 >= len(argv):
                raise ValueError(f"{tok} requires a value (1=SBAS, 2=PS)")
            insar_method = int(argv[i + 1])
            if insar_method not in (1, 2):
                raise ValueError("insar_method must be 1 (SBAS) or 2 (PS)")
            i += 2
            continue
        positional.append(tok)
        i += 1

    if not positional:
        raise ValueError("config file path is required")

    if start_step is not None and end_step is not None and start_step > end_step:
        raise ValueError(
            f"start step (-s {start_step}) must be <= end step (-e {end_step})"
        )

    if len(positional) == 1:
        return positional[0], None, False, start_step, end_step, insar_target, insar_method
    if len(positional) == 2:
        if positional[1] == 'baselines':
            return positional[0], None, True, start_step, end_step, insar_target, insar_method
        try:
            step_num = int(positional[0])
            return positional[1], step_num, False, start_step, end_step, insar_target, insar_method
        except ValueError:
            raise ValueError(
                f"Invalid step number: {positional[0]!r}. "
                "Use: <config_file> [baselines] or <step_number> <config_file>"
            )
    raise ValueError("Too many positional arguments")


def _print_cli_usage():
    print("Usage:")
    print("  python ps_sbas_snapxtra.py <config_file>                # Batch: use start_step/end_step in config")
    print("  python ps_sbas_snapxtra.py <config_file> -s 3           # Batch: start at 3, run to workflow end")
    print("  python ps_sbas_snapxtra.py <config_file> -s 3 -e 10     # Batch: set start_step/end_step, then run")
    print("  python ps_sbas_snapxtra.py <config_file> -s 3 -e 10 -t 2 -m 1  # Also set insar_target/method")
    print("  python ps_sbas_snapxtra.py <config_file> baselines      # Step 0 only (baselines mode)")
    print("  python ps_sbas_snapxtra.py <step_number> <config_file>  # Run one step")
    print("")
    print("Batch overrides (-s / -e / -t / -m write the config, then batch run):")
    print("  -s 3           start_step=3, end_step= (blank) → through step 24 LiCSBAS / 27 StaMPS")
    print("  -s 3 -e 10     start_step=3, end_step=10  (-s must be <= -e)")
    print("  -t 1 -m 1     LiCSBAS (insar_target=1, insar_method=1)")
    print("  -t 2 -m 1     StaMPS SBAS (insar_target=2, insar_method=1)")
    print("  -t 2 -m 2     StaMPS PS   (insar_target=2, insar_method=2)")
    print("  (-s/-e/-t/-m apply to batch mode only, not with <step_number> or baselines)")

