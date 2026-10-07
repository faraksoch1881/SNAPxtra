#!/usr/bin/env python3
"""Flag StaMPS PATCH_* folders that contain an empty or missing product file.

Each PATCH_* directory is expected to contain:
  mean_amp.flt  patch.in  patch_noover.in
  pscands.1.da  pscands.1.hgt  pscands.1.ij  pscands.1.ij0
  pscands.1.ij.int  pscands.1.ll  pscands.1.ph

A folder is Okay only when every one of those files exists and is larger
than 0 bytes. The same text is printed and written to ps_valid.log in --dir.

Example:
  python ps_valid.py --dir /path/to/INSAR_20241222
"""
import argparse
import os
import re
import sys

REQUIRED = (
    "mean_amp.flt",
    "patch.in",
    "patch_noover.in",
    "pscands.1.da",
    "pscands.1.hgt",
    "pscands.1.ij",
    "pscands.1.ij0",
    "pscands.1.ij.int",
    "pscands.1.ll",
    "pscands.1.ph",
)


def patch_key(name):
    m = re.fullmatch(r"PATCH_(\d+)", name)
    if m:
        return (0, int(m.group(1)))
    return (1, name)


def check_patch(folder):
    bad = []
    for name in REQUIRED:
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            bad.append(f"{name}  missing")
        elif os.path.getsize(path) == 0:
            bad.append(f"{name}  0 bytes")
    return bad


def main():
    p = argparse.ArgumentParser(description="Check StaMPS PATCH_* folders for empty files")
    p.add_argument("--dir", required=True, help="directory that contains PATCH_* folders")
    ns = p.parse_args()

    root = os.path.abspath(ns.dir)
    if not os.path.isdir(root):
        raise SystemExit(f"not a directory: {root}")

    names = sorted(
        (n for n in os.listdir(root) if n.startswith("PATCH_") and os.path.isdir(os.path.join(root, n))),
        key=patch_key,
    )
    if not names:
        raise SystemExit(f"no PATCH_* folders in {root}")

    ok_lines = []
    err_blocks = []
    for name in names:
        bad = check_patch(os.path.join(root, name))
        if bad:
            err_blocks.append(name + "\n" + "\n".join(f"  {item}" for item in bad))
        else:
            ok_lines.append(f"{name}   Okay")

    lines = list(ok_lines)
    if err_blocks:
        if lines:
            lines.append("")
        lines.append("Error")
        lines.extend(err_blocks)

    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    log_path = os.path.join(root, "ps_valid.log")
    with open(log_path, "w") as fh:
        fh.write(text)
    print(f"wrote {log_path}", file=sys.stderr)
    return 1 if err_blocks else 0


if __name__ == "__main__":
    sys.exit(main())
