function insar_time_sb(startStep, endStep)
% INSAR_TIME_SB  Run StaMPS SBAS processing steps
%
%   insar_time_sb()        - Run all steps (1 through 7, including APS & re-runs)
%   insar_time_sb(3)       - Run from step 3 to end
%   insar_time_sb(2, 4)    - Run steps 2 through 4 only
%   insar_time_sb(1, 1)    - Run step 1 only
%
%   Step mapping:
%     1  - PS Candidate Selection
%     2  - PS Estimation
%     3  - PS Selection
%     4  - Phase Correction
%     5  - Phase Unwrapping Prep
%     6  - Phase Unwrapping        (includes tropo flag logic)
%     7  - APS + SCLA Deramp + re-run Steps 6 & 7

% -------------------------------------------------------------------------
% --- Parse & Validate Arguments
% -------------------------------------------------------------------------
TOTAL_STEPS = 7;

if nargin == 0
    startStep = 1;
    endStep   = TOTAL_STEPS;
elseif nargin == 1
    endStep = TOTAL_STEPS;
end

assert(isnumeric(startStep) && isnumeric(endStep), 'Steps must be numeric.');
assert(startStep >= 1,          'startStep must be >= 1.');
assert(endStep   <= TOTAL_STEPS,'endStep must be <= %d.', TOTAL_STEPS);
assert(startStep <= endStep,    'startStep must be <= endStep.');

fprintf('\n========================================\n');
fprintf(' insar_time_sb: running steps %d to %d\n', startStep, endStep);
fprintf('========================================\n');

% -------------------------------------------------------------------------
% --- Initialize Timers
% -------------------------------------------------------------------------
totalStart = tic;
stepTimes  = zeros(TOTAL_STEPS, 1);

stepLabels = {
    'PS Candidate Selection',     ... % 1
    'PS Estimation',              ... % 2
    'PS Selection',               ... % 3
    'Phase Correction',           ... % 4
    'Phase Unwrapping Prep',      ... % 5
    'Phase Unwrapping',           ... % 6
    'APS + SCLA Deramp + Re-run'  ... % 7
};

% -------------------------------------------------------------------------
% --- Pre-Step 1: small_baseline_flag = 'y'   (SBAS: opposite of PS)
% -------------------------------------------------------------------------
if startStep == 1
    flag = getparm('small_baseline_flag');
    if strcmpi(flag, 'n')
        setparm('small_baseline_flag', 'y');
    end
end

% -------------------------------------------------------------------------
% --- Steps 1-5  (straightforward stamps() calls)
% -------------------------------------------------------------------------
for s = startStep : min(endStep, 5)
    fprintf('\n--- Step %d: %s ---\n', s, stepLabels{s});
    t = tic;
    stamps(s, s);
    stepTimes(s) = toc(t);
    fprintf('Step %02d finished in %.0fm %.0fs\n', s, ...
            floor(stepTimes(s)/60), rem(stepTimes(s), 60));
    print_elapsed(totalStart);
end
set_tropo('n');
% -------------------------------------------------------------------------
% --- Step 6: Phase Unwrapping  (tropo flag = y before, n after)
% -------------------------------------------------------------------------
if startStep <= 6 && endStep >= 6
    fprintf('\n--- Step 6: %s ---\n', stepLabels{6});

    set_tropo('n');   % subtr_tropo must be y before unwrapping

    t = tic;
    stamps(6, 6);
    stepTimes(6) = toc(t);

    fprintf('Step 06 finished in %.0fm %.0fs\n', ...
            floor(stepTimes(6)/60), rem(stepTimes(6), 60));
    print_elapsed(totalStart);

end

% -------------------------------------------------------------------------
% --- Step 7: APS correction, SCLA deramp, re-run Steps 6 and 7
% -------------------------------------------------------------------------
if startStep <= 7 && endStep >= 7
    fprintf('\n--- Step 7: %s ---\n', stepLabels{7});
    t = tic;

    % -- scla_deramp --
    deramp = getparm('scla_deramp');
    if strcmpi(deramp, 'n')
        setparm('scla_deramp', 'y');
    end

    set_tropo('n');

    % -- APS path setup --
    fprintf('\nSetting paths for getparm_aps...\n');
    pwd_path = pwd;
    setparm_aps('bperp_matfile',  [pwd_path '/ps2.mat']);
    setparm_aps('demfile',        [pwd_path '/dummy.dem']);
    setparm_aps('hgt_matfile',    [pwd_path '/hgt2.mat']);
    setparm_aps('ifgday_matfile', [pwd_path '/ps2.mat']);
    setparm_aps('ll_matfile',     [pwd_path '/ps2.mat']);
    setparm_aps('look_angle',     [pwd_path '/la2.mat']);
    setparm_aps('phuw_matfile',   [pwd_path '/phuw_sb2.mat']);  % SBAS-specific

    % -- SBAS: small_baseline_flag = 'y' for APS (opposite of PS) --
    sb_aps_flag = getparm_aps('small_baseline_flag');
    if strcmpi(sb_aps_flag, 'n')
        setparm_aps('small_baseline_flag', 'y');
    end

    fprintf('\nRunning aps_linear...\n');
    aps_linear;

    fprintf('\nRunning stamps(7,7) — first pass...\n');
    stamps(7, 7);

    % -- Re-run Step 6: unwrap with ramp from Step 7 subtracted --
    fprintf('\nRe-running Step 6 with subtr_tropo=y (ramp from Step 7 subtracted)...\n');

    stamps(6, 6);


    % -- Re-run Step 7: re-estimate SCLA deramp on cleaner phases --
    fprintf('\nRe-estimating SCLA Deramp (stamps(7,7) second pass)...\n');
    stamps(7, 7);

    stepTimes(7) = toc(t);
    fprintf('Step 07 finished in %.0fm %.0fs\n', ...
            floor(stepTimes(7)/60), rem(stepTimes(7), 60));
    print_elapsed(totalStart);
end

% -------------------------------------------------------------------------
% --- Final Summary
% -------------------------------------------------------------------------
finalTotal = toc(totalStart);
hours   = floor(finalTotal / 3600);
minutes = floor(rem(finalTotal, 3600) / 60);
seconds = rem(finalTotal, 60);

fprintf('\n========================================\n');
fprintf('Steps %d-%d completed!\n', startStep, endStep);
fprintf('Total execution time: %dh %dm %.0fs\n', hours, minutes, seconds);
fprintf('----------------------------------------\n');
for s = startStep:endStep
    if stepTimes(s) > 0
        fprintf('  Step %02d  %-30s  %.0fm %.0fs\n', s, stepLabels{s}, ...
                floor(stepTimes(s)/60), rem(stepTimes(s), 60));
    end
end
fprintf('========================================\n');

% -------------------------------------------------------------------------
% --- Further Steps Instructions (only on full run)
% -------------------------------------------------------------------------
if startStep == 1 && endStep == TOTAL_STEPS
    print_next_steps();
end

end % function insar_time_sb


% =========================================================================
% --- Local Helper Functions
% =========================================================================

function print_elapsed(totalStart)
    e = toc(totalStart);
    fprintf('Total elapsed: %.0fm %.0fs\n', floor(e/60), rem(e,60));
end

function set_tropo(val)
% Sets subtr_tropo to val ('y' or 'n') and verifies, errors if it fails.
    current = getparm('subtr_tropo');
    if ~strcmpi(current, val)
        setparm('subtr_tropo', val);
        verified = getparm('subtr_tropo');
        if ~strcmpi(verified, val)
            fprintf('\n[ERROR] Failed to set subtr_tropo = %s\n', val);
            fprintf('----------------------------------------------\n');
            fprintf('Manual fix required. Run in MATLAB:\n');
            fprintf('  setparm(''subtr_tropo'', ''%s'')\n', val);
            fprintf('Then re-run this script.\n');
            fprintf('----------------------------------------------\n');
            error('Script terminated: subtr_tropo could not be set to %s.', val);
        end
    end
    fprintf('subtr_tropo set to: %s\n', val);
end

function print_next_steps()
    fprintf('\n========================================\n');
    fprintf('SBAS-InSAR FURTHER STEPS\n');
    fprintf('========================================\n');
    fprintf('\n>> Check unwrapping (unw):\n');
    fprintf('   ps_plot(''usb'')\n');
    fprintf('   sb_baseline_plot\n');
    fprintf('   sb_info\n');
    fprintf('\n   Get index of inconsistent/non-smooth images. To drop:\n');
    fprintf('   setparm(''drop_ifg_index'', [73,75,77,80:83])\n');
    fprintf('   Note: 80:83 drops indices 80, 81, 82, 83\n');
    fprintf('\n>> Plot velocity:\n');
    fprintf('   ps_plot(''V-dao'',''a_linear'')\n');
    fprintf('   Use ''V'' to force single master network for SB as well.\n');
    fprintf('\n>> Export to CSV:\n');
    fprintf('   ps_export_csv_sb\n');
    fprintf('   Then use InSAR Explorer plugin in QGIS.\n');
    fprintf('   Use https://www.epochconverter.com to convert the first-row\n');
    fprintf('   date format (days since Day 0) to YYYYMMDD.\n');
    fprintf('\n>> Export to KML:\n');
    fprintf('   ps_save_kml_ps\n');
    fprintf('\n>> Plot with Hillshade background:\n');
    fprintf('   1. Run: python stamp_dem_prep.py --geo projected_dem.par\n');
    fprintf('   2. Save *.raw and demparms.in into the INSAR_YYYYMMDD folder.\n');
    fprintf('   3. Then run: ps_plot(''v'',2)\n');
    fprintf('========================================\n');
end