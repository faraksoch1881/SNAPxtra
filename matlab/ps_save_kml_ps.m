function ps_save_kml_ps(plot_type, outfile, step, opacity)
    % --------------------------------------------------
    % Step 1: Load StaMPS version and build file paths
    % --------------------------------------------------
    load psver
    psname = ['./ps', num2str(psver)];

    % --------------------------------------------------
    % Step 2: Load core PS data (for reference if needed)
    % --------------------------------------------------
    ps = load(psname);

    % --------------------------------------------------
    % Step 3: Run ps_plot
    % --------------------------------------------------
    if contains(plot_type, 'a')
        fprintf('a parameter detected\n');
        ps_plot(plot_type, 'a_linear', -1);
    else
        ps_plot(plot_type, -1);
    end

    % --------------------------------------------------
    % Step 4: Load MAT file via struct (safe inside function)
    % --------------------------------------------------
    matfile_name = ['ps_plot_' plot_type '.mat'];
    if ~exist(matfile_name, 'file')
        error('Missing MAT file: %s', matfile_name);
    end
    S = load(matfile_name);

    if ~isfield(S, 'ph_disp')
        error('ph_disp not found in %s', matfile_name);
    end
    ph_disp = S.ph_disp;

    % --------------------------------------------------
    % Step 5: Export KML
    % --------------------------------------------------
    ps_gescatter(outfile, ph_disp, step, opacity);
    fprintf('KML exported to: %s\n', outfile);
end