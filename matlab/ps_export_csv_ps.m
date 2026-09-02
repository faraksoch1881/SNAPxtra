% Summary of all 4 correction paths now handled in both scripts:
%
% subtr_tropo   scla_deramp   PS script (_ps)                              SBAS script (_sbas)
% ------------------------------------------------------------------------------------------------
% 'n'           'n'           ph_uw - scn - C_ps_uw                        ph_uw - scn - C_ps_uw - ph_scla
%
% 'n'           'y'           ph_uw - scn - C_ps_uw - ph_scla - ph_ramp   ph_uw - scn - C_ps_uw - ph_scla - ph_ramp
%
% 'y'           'n'           ph_uw - aps_corr                             ph_uw - aps_corr - ph_scla
%
% 'y'           'y'           ph_uw - aps_corr - ph_scla - ph_ramp        ph_uw - aps_corr - ph_scla - ph_ramp
function ps_export_csv_ps(plot_type, outfile)
% PS_EXPORT_CSV_PS  Export StaMPS PS results to a clean CSV file.
%
%   ps_export_csv_ps(plot_type, outfile)
%
%   Inputs:
%     plot_type : StaMPS plot type string, e.g. 'v', 'vs', 'va', etc.
%     outfile   : output CSV filename (will be written clean, no raw temp file visible)
%
%   Output CSV columns:
%     longitude, latitude, velocity, YYYYMMDD, YYYYMMDD, ...
%
%   Andy Hooper (original), integrated post-processing added.
%
%   ======================================================
%   Steps:
%     1. Load StaMPS PS files
%     2. Run ps_plot to generate ph_disp
%     3. Load ph_disp from MAT file
%     4. Build time series (ph_mm) with corrections
%     5. Export to <basename>_raw.csv
%   Post-processing (date column renaming etc.) is handled
%   separately by running fix_stamps_csv.py from the terminal.
%   ======================================================

    % --------------------------------------------------
    % Step 1: Load StaMPS files
    % --------------------------------------------------
    load psver
    psname   = ['./ps',   num2str(psver)];
    sclaname = ['./scla', num2str(psver)];

    ps                = load(psname);
    lonlat            = ps.lonlat;
    day               = ps.day;
    master_day        = ps.master_day;
    n_ps              = ps.n_ps;
    ref_centre_lonlat = getparm('ref_centre_lonlat');

    fprintf('Loaded PS file: %s  (%d PS points)\n', psname, n_ps);

    % --------------------------------------------------
    % Step 2: Run ps_plot to get ph_disp
    % --------------------------------------------------
    if contains(plot_type, 'a')
        fprintf('plot_type contains ''a'' — running ps_plot with a_linear\n');
        ps_plot(plot_type, 'a_linear', -1);
    else
        ps_plot(plot_type, -1);
    end

    % --------------------------------------------------
    % Step 3: Load ph_disp from MAT file
    % --------------------------------------------------
    matfile_name = ['ps_plot_' plot_type '.mat'];
    if ~exist(matfile_name, 'file')
        error('Missing MAT file: %s — did ps_plot run successfully?', matfile_name);
    end
    S       = load(matfile_name);
    ph_disp = S.ph_disp;   % [n_ps x 1] mean LOS velocity (mm/year)

    fprintf('Loaded ph_disp from: %s  (size: %dx%d)\n', ...
            matfile_name, size(ph_disp,1), size(ph_disp,2));

    % --------------------------------------------------
    % Step 4: lon/lat aligned directly with ph_disp
    % --------------------------------------------------
    lon2 = lonlat(:, 1);
    lat2 = lonlat(:, 2);

    % --------------------------------------------------
    % Step 5: Build time series with corrections
    % --------------------------------------------------
    phuwname = ['./phuw', num2str(psver)];
    scnname  = ['./scn',  num2str(psver)];
    sclaname = ['./scla', num2str(psver)];
    apsname  = ['./tca',  num2str(psver)];

    scla_deramp = getparm('scla_deramp');
    subtr_tropo = getparm('subtr_tropo');
    lambda      = getparm('lambda');

    fprintf('lambda      = %.7f m\n', lambda);
    fprintf('subtr_tropo = %s\n',     subtr_tropo);
    fprintf('scla_deramp = %s\n',     scla_deramp);

    uw = load(phuwname);

    if strcmp(subtr_tropo, 'y')
        fprintf('Applying tropospheric correction (subtr_tropo=y)\n');
        aps      = load(apsname);
        aps_corr = aps.ph_tropo_linear;

        if strcmp(scla_deramp, 'y')
            scla = load(sclaname);
            ts = uw.ph_uw ...
                - aps_corr ...
                - scla.ph_scla ...
                - scla.ph_ramp;
        else
            ts = uw.ph_uw - aps_corr;
        end

    else
        fprintf('No tropospheric correction (subtr_tropo=n)\n');

        % --------------------------------------------------
        % Check for scn file before attempting to load
        % --------------------------------------------------
        if ~exist(scnname, 'file') && ~exist([scnname '.mat'], 'file')
            fprintf('\n');
            fprintf('=========================================\n');
            fprintf('ERROR: Missing file: %s\n', scnname);
            fprintf('=========================================\n');
            fprintf('This file contains spatially-correlated noise (SCN) estimates\n');
            fprintf('required when subtr_tropo=n.\n\n');
            fprintf('To fix, choose ONE of the following options:\n\n');
            fprintf('  OPTION 1 — Re-run StaMPS step 8 to generate %s:\n', scnname);
            fprintf('    >> stamps(8,8)\n\n');
            fprintf('  OPTION 2 — Switch to APS linear correction (uses TRAIN output):\n');
            fprintf('    >> setparm(''subtr_tropo'', ''y'')\n');
            fprintf('    Then re-run this export.\n');
            fprintf('    (Requires TRAIN-derived APS correction in %s.mat)\n', apsname);
            fprintf('\n');
            error('Missing %s — see options printed above.', scnname);
        end

        scn  = load(scnname);
        scla = load(sclaname);

        if strcmp(scla_deramp, 'y')
            ts = uw.ph_uw ...
                - scn.ph_scn_slave ...
                - repmat(scla.C_ps_uw, 1, size(uw.ph_uw, 2)) ...
                - scla.ph_scla ...
                - scla.ph_ramp;
        else
            ts = uw.ph_uw ...
                - scn.ph_scn_slave ...
                - repmat(scla.C_ps_uw, 1, size(uw.ph_uw, 2));
        end
    end

    ts(:, ps.master_ix) = 0;
    ph_mm = ts / 4 / pi * lambda * 1000;   % convert radians -> mm

    fprintf('Time series matrix size: %dx%d\n', size(ph_mm,1), size(ph_mm,2));

    % --------------------------------------------------
    % Step 6: Build export matrix
    %   Row 1 (metarow): ref_lon, ref_lat, NaN, day1, day2, ...
    %   Rows 2+:         lon, lat, velocity, ts_day1, ts_day2, ...
    % --------------------------------------------------
    export_data = [lon2, lat2, ph_disp, ph_mm];
    metarow     = [ref_centre_lonlat, NaN, transpose(day) - 1];

    % metarow at top (k=0 means before all data rows)
    k = 0;
    export_res = [export_data(1:k, :); metarow; export_data(k+1:end, :)];

    % --------------------------------------------------
    % Step 7: Build output filename  ->  <basename>_raw.csv
    % --------------------------------------------------
    [fdir, fname, fext] = fileparts(outfile);
    if isempty(fdir)
        fdir = '.';
    end
    if isempty(fext)
        fext = '.csv';
    end
    raw_outfile = fullfile(fdir, [fname '_raw' fext]);

    % --------------------------------------------------
    % Step 8: Write raw CSV
    % --------------------------------------------------
    % Build column headers for raw CSV
    fixed_headers = {'lon', 'lat', 'vel'};
    date_headers  = arrayfun(@(d) num2str(round(d)), day' - 1, 'UniformOutput', false);
    all_headers   = [fixed_headers, date_headers];

    fid = fopen(raw_outfile, 'w');
    if fid == -1
        error('Cannot open file for writing: %s', raw_outfile);
    end

    % Write header line
    fprintf(fid, '%s\n', strjoin(all_headers, ','));

    % Write metarow (ref point info)
    fprintf(fid, '%.6f,%.6f,%.6f', metarow(1), metarow(2), metarow(3));
    for c = 4:length(metarow)
        fprintf(fid, ',%.4f', metarow(c));
    end
    fprintf(fid, '\n');

    % Write data rows
    fprintf('Writing %d PS points to raw CSV...\n', size(export_data,1));
    for r = 1:size(export_data, 1)
        fprintf(fid, '%.6f,%.6f,%.6f', export_data(r,1), export_data(r,2), export_data(r,3));
        for c = 4:size(export_data, 2)
            fprintf(fid, ',%.4f', export_data(r,c));
        end
        fprintf(fid, '\n');
    end
    fclose(fid);
    fprintf('Raw CSV written: %s\n', raw_outfile);

end