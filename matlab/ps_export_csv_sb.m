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


function ps_export_csv_sbas(plot_type, outfile)
    % --------------------------------------------------
    % Step 1: Load StaMPS version and build file paths
    % --------------------------------------------------
    load psver
    psname      = ['./ps',       num2str(psver)];
    phuwname    = ['./phuw',     num2str(psver)];
    sclaname    = ['./scla',     num2str(psver)];
    sclasbname  = ['./scla_sb',  num2str(psver)];
    bpname      = ['./bp',       num2str(psver)];
    scnname     = ['./scn',      num2str(psver)];
    pmname      = ['./pm',       num2str(psver)];

    % --------------------------------------------------
    % Step 2: Load parameters
    % --------------------------------------------------
    lambda           = getparm('lambda');
    scla_deramp      = getparm('scla_deramp');
    drop_ifg_index   = getparm('drop_ifg_index');
    ref_centre_lonlat = getparm('ref_centre_lonlat');

    % --------------------------------------------------
    % Step 3: Load core PS data
    % --------------------------------------------------
    ps      = load(psname);
    lonlat  = ps.lonlat;
    day     = ps.day;
    n_ps    = ps.n_ps;

    % --------------------------------------------------
    % Step 4: Run ps_plot to get ph_disp (mean velocity)
    % --------------------------------------------------

    if contains(plot_type, 'a')
        fprintf('a parameter detected\n');
        ps_plot(plot_type, 'a_linear', -1);
    else
        ps_plot(plot_type, -1);
    end

    plot_type_file = plot_type;
    plot_type_file(1) = lower(plot_type_file(1));


    matfile_name = ['ps_plot_' plot_type_file '.mat'];
    if ~exist(matfile_name, 'file')
        error('Missing MAT file: %s', matfile_name);
    end
    S       = load(matfile_name);
    ph_disp = S.ph_disp;   % mean velocity [n_ps x 1]

    % --------------------------------------------------
    % --------------------------------------------------
    % Step 5: Load unwrapped phase and get ifg index
    % --------------------------------------------------
    phuwname   = ['./phuw', num2str(psver)];
    scnname    = ['./scn',  num2str(psver)];
    sclaname   = ['./scla', num2str(psver)];
    sclasbname = ['./scla_sb', num2str(psver)];
    bpname     = ['./bp',   num2str(psver)];
    apsname    = ['./tca',  num2str(psver)];

    scla_deramp = getparm('scla_deramp');
    subtr_tropo = getparm('subtr_tropo');

    fprintf('DEBUG: Tropospheric Value\n');
    fprintf(subtr_tropo);

    uw = load(phuwname);

    if isfield(uw, 'unwrap_ifg_index_sm')
        unwrap_ifg_index = uw.unwrap_ifg_index_sm;
    else
        unwrap_ifg_index = setdiff(1:ps.n_ifg, drop_ifg_index);
    end

    % --------------------------------------------------
    % Step 6: Build SBAS baseline inversion (always needed)
    % --------------------------------------------------
    sclasb = load(sclasbname);
    bp     = load(bpname);

    G = zeros(ps.n_ifg, ps.n_image);
    for i = 1:ps.n_ifg
        G(i, ps.ifgday_ix(i,1)) = -1;
        G(i, ps.ifgday_ix(i,2)) =  1;
    end

    G_sub     = G(:, unwrap_ifg_index);
    bperp_mat = zeros(ps.n_ps, ps.n_image, 'single');
    bperp_some = [G_sub \ double(bp.bperp_mat')]';
    bperp_mat(:, unwrap_ifg_index) = bperp_some;

    % SBAS DEM phase correction (always applied in SBAS)
    ph_scla = repmat(sclasb.K_ps_uw, 1, size(bperp_mat, 2)) .* bperp_mat;

    % --------------------------------------------------
    % Step 7: Auto-select correction based on StaMPS parameters
    % --------------------------------------------------

    
    if strcmp(subtr_tropo, 'y')
        fprintf('Applying u-dao + SBAS DEM correction (subtr_tropo=y)\n');
        aps      = load(apsname);
        aps_corr = aps.ph_tropo_linear;

        if strcmp(scla_deramp, 'y')
            scla = load(sclaname);
            ts = uw.ph_uw ...
                - aps_corr ...
                - ph_scla ...
                - scla.ph_ramp;
        else
            ts = uw.ph_uw ...
                - aps_corr ...
                - ph_scla;
        end

    else
        fprintf('Applying u-dsbmos correction (subtr_tropo=n)\n');
        scn  = load(scnname);
        scla = load(sclaname);

        if strcmp(scla_deramp, 'y')
            ts = uw.ph_uw ...
                - scn.ph_scn_slave ...
                - repmat(scla.C_ps_uw, 1, size(uw.ph_uw, 2)) ...
                - ph_scla ...
                - scla.ph_ramp;
        else
            ts = uw.ph_uw ...
                - scn.ph_scn_slave ...
                - repmat(scla.C_ps_uw, 1, size(uw.ph_uw, 2)) ...
                - ph_scla;
        end
    end

    ts(:, ps.master_ix) = 0;

    % --------------------------------------------------
    % Step 7: Subset to valid interferograms and convert to mm
    % --------------------------------------------------
    ts  = ts(:, unwrap_ifg_index);
    day = ps.day(unwrap_ifg_index, :);

    % Reference to first acquisition
    ts = ts - repmat(ts(:,1), 1, size(ts,2));

    % Convert phase to mm
    ph_mm = ts / 4 / pi * lambda * 1000;

    % --------------------------------------------------
    % Step 8: DEM error and coherence
    % --------------------------------------------------
 
    if ~exist('scla', 'var')
        scla = load(sclaname);
    end
    demifg_m = K2q(sclasb.K_ps_uw);   % SBAS DEM error in metres
    demts_m  = K2q(scla.K_ps_uw);     % single-master DEM error in metres

    pm = load(pmname);
    if isfield(pm, 'coh_ps')
        coherence     = pm.coh_ps;
        has_coherence = true;
    else
        warning('coh_ps not found in %s (MERGED run?). Coherence column will be omitted.', pmname);
        has_coherence = false;
    end

    % --------------------------------------------------
    % Step 9: Build export matrix
    % --------------------------------------------------
    lon2 = lonlat(:, 1);
    lat2 = lonlat(:, 2);

    % Build date_header FIRST, before the conditional block
    formatOut   = 'yyyymmdd';
    dates       = datestr(day, formatOut);
    date_header = strcat({'d'}, cellstr(dates))';

    if has_coherence
        export_data = [lon2, lat2, ph_disp, demts_m, demifg_m, coherence, ph_mm];
        metarow     = [ref_centre_lonlat, NaN, NaN, NaN, NaN, transpose(day)-1];
        col_header  = [{'lon','lat','v','dem_ts','dem_ifg','coherence'}, date_header];
    else
        export_data = [lon2, lat2, ph_disp, demts_m, demifg_m, ph_mm];
        metarow     = [ref_centre_lonlat, NaN, NaN, NaN, transpose(day)-1];
        col_header  = [{'lon','lat','v','dem_ts','dem_ifg'}, date_header];
    end

    k = 0;
    export_res = [export_data(1:k, :); metarow; export_data(k+1:end, :)];

    % --------------------------------------------------
    % Step 10: Write CSV with header
    % --------------------------------------------------
    fid = fopen(outfile, 'wt');
    fprintf(fid, '%s,', col_header{1:end-1});
    fprintf(fid, '%s\n', col_header{end});
    fclose(fid);
    dlmwrite(outfile, export_res, 'precision', 10, '-append');
    fprintf('SBAS CSV exported to: %s\n', outfile);
end