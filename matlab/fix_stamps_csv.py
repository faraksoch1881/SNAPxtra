#!/usr/bin/env python3
"""
fix_stamps_csv.py
-----------------
Post-processes StaMPS ps_export_csv_ps.m output CSV.

Format A (ordinal date columns like 738915):
  - Removes the junk second row (row index 0 after header)
  - Renames lon -> longitude, lat -> latitude, vel -> velocity
  - Converts MATLAB ordinal day columns to YYYYMMDD strings, +1 day offset

Format B (columns already named dYYYYMMDD like d20230130):
  - Removes the junk second row
  - Strips leading 'd' from date column headers

Usage:
    python fix_stamps_csv.py input.csv output.csv
"""

import pandas as pd
from datetime import datetime, timedelta
import sys
import os


def matlab_ordinal_to_date(n, extra_offset=1):
    """
    Convert MATLAB/Python ordinal day (days since year 0) to YYYYMMDD string.
    Python's datetime.fromordinal uses proleptic Gregorian calendar;
    MATLAB adds an offset of -366 days relative to Python ordinal.
    extra_offset adds an additional +1 day correction.
    """
    try:
        n = int(float(n))
        date = datetime.fromordinal(n) + timedelta(days=-366 + extra_offset)
        return date.strftime("%Y%m%d")
    except Exception:
        return str(n)


def is_ordinal_date(col_name):
    """Check if a column header looks like a MATLAB ordinal date (large integer)."""
    try:
        val = int(float(col_name))
        return 600000 < val < 900000
    except (ValueError, TypeError):
        return False


def is_ddate_col(col_name):
    """Check if a column header matches dYYYYMMDD format."""
    if isinstance(col_name, str) and col_name.startswith('d') and len(col_name) == 9:
        try:
            datetime.strptime(col_name[1:], "%Y%m%d")
            return True
        except ValueError:
            return False
    return False


def detect_format(df):
    """
    Return 'A' if any column looks like a MATLAB ordinal,
    return 'B' if any column looks like dYYYYMMDD,
    else return 'unknown'.
    """
    for col in df.columns:
        if is_ordinal_date(col):
            return 'A'
    for col in df.columns:
        if is_ddate_col(col):
            return 'B'
    return 'unknown'


def fix_stamps_csv(input_path, output_path):
    print(f"Reading: {input_path}")

    df = pd.read_csv(input_path, header=0, sep=None, engine='python')
    df = df.reset_index(drop=True)

    print(f"  Rows (including junk row): {len(df)}")
    print(f"  Columns found: {list(df.columns)}")

    fmt = detect_format(df)
    print(f"  Detected format: {fmt}")

    # -------------------------------------------------------
    # Remove junk second row (row index 0, right after header)
    # -------------------------------------------------------
    df = df.iloc[1:].reset_index(drop=True)
    print(f"  Rows after removing junk row: {len(df)}")

    if fmt == 'A':
        # ---------------------------------------------------
        # Format A: rename columns + convert ordinal dates +1
        # ---------------------------------------------------
        rename_map = {
            'lon': 'longitude',
            'lat': 'latitude',
            'vel': 'velocity',
        }

        for col in df.columns:
            if is_ordinal_date(col):
                new_name = matlab_ordinal_to_date(col, extra_offset=1)
                rename_map[col] = new_name
                print(f"  Date column: {col} -> {new_name}")

        df = df.rename(columns=rename_map)

        # Convert all columns to numeric
        for col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # -------------------------------------------------------
        # Normalize time series: subtract each row's first date
        # value so all time series start at zero
        # -------------------------------------------------------
        date_cols = [col for col in df.columns if col not in ('longitude', 'latitude', 'velocity')]
        if date_cols:
            first_col = date_cols[0]
            df[date_cols] = df[date_cols].subtract(df[first_col], axis=0)
            print(f"  Normalized time series to zero using first date column: {first_col}")

        df.to_csv(output_path, index=False, float_format='%.6f')

        # Re-write with mixed precision: 6dp for coords/velocity, 4dp for dates
        import io
        lines = []
        lines.append(','.join(df.columns))
        non_date_cols = ['longitude', 'latitude', 'velocity']
        for _, row in df.iterrows():
            parts = []
            for col in df.columns:
                val = row[col]
                if col in non_date_cols:
                    parts.append(f'{val:.6f}')
                else:
                    parts.append(f'{val:.4f}')
            lines.append(','.join(parts))
        with open(output_path, 'w') as f:
            f.write('\n'.join(lines) + '\n')

    elif fmt == 'B':
        # ---------------------------------------------------
        # Format B: strip leading 'd' from dYYYYMMDD headers
        # ---------------------------------------------------
        rename_map = {}
        for col in df.columns:
            if is_ddate_col(col):
                new_name = col[1:]  # strip the 'd'
                rename_map[col] = new_name
                print(f"  Date column: {col} -> {new_name}")

        df = df.rename(columns=rename_map)

        # Preserve original formatting — do NOT coerce to numeric
        df.to_csv(output_path, index=False)

    else:
        print("  Warning: unknown format, saving as-is (junk row still removed).")
        df.to_csv(output_path, index=False)


  
    # -------------------------------------------------------
    # Save separate velocity CSV using first three columns
    # -------------------------------------------------------
    df_new = df[['longitude', 'latitude', 'velocity']]

    vel_output_path = os.path.splitext(output_path)[0] + '_vel.csv'
    df_new.to_csv(vel_output_path, index=False)

    print(f"  Velocity CSV saved to: {vel_output_path}")

    print(f"\nDone! Saved to: {output_path}")
    print(f"  Total PS points: {len(df)}")
    print(f"  Final columns:   {list(df.columns)}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python fix_stamps_csv.py input.csv output.csv")
        sys.exit(1)

    input_csv  = sys.argv[1]
    output_csv = sys.argv[2]

    if not os.path.exists(input_csv):
        print(f"Error: input file not found: {input_csv}")
        sys.exit(1)

    fix_stamps_csv(input_csv, output_csv)