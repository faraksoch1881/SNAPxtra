function insar_time_ps(startStep, endStep)
% INSAR_TIME_PS  Run StaMPS PS-InSAR processing steps
%
%   insar_time_ps()        - Run all steps (1 through 7, including APS & re-runs)
%   insar_time_ps(3)       - Run from step 3 to end
%   insar_time_ps(2, 4)    - Run steps 2 through 4 only
%   insar_time_ps(1, 1)    - Run step 1 only
%
%   Step mapping:
%     1  - PS Candidate Selection
%     2  - PS Estimation
%     3  - PS Selection
%     4  - Phase Correction
%     5  - Phase Unwrapping Prep
%     6  - Phase Unwrapping  (includes tropo flag logic)
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

assert(isnumeric(startStep) && isnumeric(endStep),      'Steps must be numeric.');
assert(startStep >= 1,                                   'startStep must be >= 1.');
assert(endStep   <= TOTAL_STEPS,                         'endStep must be <= %d.', TOTAL_STEPS);
assert(startStep <= endStep,                             'startStep must be <= endStep.');

fprintf('\n========================================\n');
fprintf(' insar_time_ps: running steps %d to %d\n', startStep, endStep);
fprintf('========================================\n');

% -------------------------------------------------------------------------
% --- Pre-flight: validate pscands.1.ij / .ll / .hgt before any stamps()
% -------------------------------------------------------------------------
validate_pscands_files(startStep);

% -------------------------------------------------------------------------
% --- Initialize Timers
% -------------------------------------------------------------------------
totalStart = tic;
stepTimes  = zeros(TOTAL_STEPS, 1);

stepLabels = {
    'PS Candidate Selection',    ... % 1
    'PS Estimation',             ... % 2
    'PS Selection',              ... % 3
    'Phase Correction',          ... % 4
    'Phase Unwrapping Prep',     ... % 5
    'Phase Unwrapping',          ... % 6
    'APS + SCLA Deramp + Re-run' ... % 7
};

% -------------------------------------------------------------------------
% --- Pre-Step 1: small_baseline_flag  (only when Step 1 is included)
% -------------------------------------------------------------------------
if startStep == 1
    flag = getparm('small_baseline_flag');
    if strcmpi(flag, 'y')
        setparm('small_baseline_flag', 'n');
    end
end

% -------------------------------------------------------------------------
% --- Steps 1-5  (straightforward stamps() calls)
% -------------------------------------------------------------------------
for s = startStep : min(endStep, 5)
    run_step(s, stepLabels{s}, totalStart, stepTimes);
    stepTimes(s) = stepTimes(s); % already updated inside run_step via return
end
set_tropo('n');   % if set 'y', then unwrapping phase cannot run
% -------------------------------------------------------------------------
% --- Step 6: Phase Unwrapping  (needs tropo flag set to 'y' before, 'n' after)
% -------------------------------------------------------------------------
if startStep <= 6 && endStep >= 6
    fprintf('\n--- Step 6: %s ---\n', stepLabels{6});

    set_tropo('n');

    

    t = tic;
    stamps(6, 6);
    stepTimes(6) = toc(t);

    fprintf('Step 06 finished in %.0fm %.0fs\n', ...
            floor(stepTimes(6)/60), rem(stepTimes(6), 60));
    print_elapsed(totalStart);

    
end

% -------------------------------------------------------------------------
% --- Step 7: APS correction, SCLA deramp, then re-run Steps 6 and 7
% -------------------------------------------------------------------------
if startStep <= 7 && endStep >= 7
    fprintf('\n--- Step 7: %s ---\n', stepLabels{7});
    t = tic;

    % -- Post-Step 6: scla_deramp --
    deramp = getparm('scla_deramp');
    if strcmpi(deramp, 'n')
        setparm('scla_deramp', 'y');
    end

    set_tropo('n');   % ensure subtr_tropo = y before unwrapping

    % -- APS path setup --
    fprintf('\nSetting paths for getparm_aps...\n');
    pwd_path = pwd;
    setparm_aps('bperp_matfile',  [pwd_path '/ps2.mat']);
    setparm_aps('demfile',        [pwd_path '/dummy.dem']);
    setparm_aps('hgt_matfile',    [pwd_path '/hgt2.mat']);
    setparm_aps('ifgday_matfile', [pwd_path '/ps2.mat']);
    setparm_aps('ll_matfile',     [pwd_path '/ps2.mat']);
    setparm_aps('look_angle',     [pwd_path '/la2.mat']);
    setparm_aps('phuw_matfile',   [pwd_path '/phuw2.mat']);

    sb_aps_flag = getparm_aps('small_baseline_flag');
    if strcmpi(sb_aps_flag, 'y')
        setparm_aps('small_baseline_flag', 'n');
    end

    fprintf('\nRunning aps_linear...\n');
    aps_linear;

    fprintf('\nRunning stamps(7,7) — first pass...\n');
    stamps(7, 7);

    % -- Re-run Step 6 with tropo correction (ramp from Step 7 subtracted) --
    fprintf('\nRe-running Step 6 with subtr_tropo=y (ramp from Step 7 subtracted)...\n');
    set_tropo('n');
    stamps(6, 6);
    %set_tropo('y');

    % -- Re-run Step 7: re-estimate SCLA deramp --
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
        fprintf('  Step %02d %-30s %.0fm %.0fs\n', s, stepLabels{s}, ...
                floor(stepTimes(s)/60), rem(stepTimes(s), 60));
    end
end
fprintf('========================================\n');

% -------------------------------------------------------------------------
% --- Further Steps Instructions (only when full run completes)
% -------------------------------------------------------------------------
if startStep == 1 && endStep == TOTAL_STEPS
    print_next_steps();
end

end % function insar_time_ps


% =========================================================================
% --- Local Helper Functions
% =========================================================================

function validate_pscands_files(startStep)
%VALIDATE_PSCANDS_FILES  Check pscands.1.ij / .ll / .hgt before stamps().
%
%   For each PATCH_* (or cwd if no patches):
%     - files exist and are non-empty (size > 0)
%     - n_ij == n_ll == n_hgt
%       n_ij  = number of lines in pscands.1.ij
%       n_ll  = filesize(pscands.1.ll)  / 8   (2 float32)
%       n_hgt = filesize(pscands.1.hgt) / 4   (1 float32)
%
%   Aborts with a clear error if anything fails. Required when startStep==1;
%   for later restarts, skips quietly if no pscands files are present.

    fprintf('\n--- Pre-flight: pscands file validation ---\n');

    patchDirs = discover_patch_dirs();
    if isempty(patchDirs)
        if startStep == 1
            error(['insar_time_ps: no PATCH_* directories and no pscands.1.ij in %s.\n' ...
                   'Run mt_prep / Step 11–12 first.'], pwd);
        end
        fprintf('  No PATCH_*/pscands found — skipping validation (startStep=%d).\n', startStep);
        return;
    end

    nFail = 0;
    for k = 1:numel(patchDirs)
        pdir = patchDirs{k};
        [ok, msg] = check_one_patch_pscands(pdir);
        if ok
            fprintf('  OK  %s: %s\n', pdir, msg);
        else
            fprintf(2, '  FAIL %s: %s\n', pdir, msg);
            nFail = nFail + 1;
        end
    end

    if nFail > 0
        error(['insar_time_ps: pscands validation failed in %d location(s).\n' ...
               '  Fix: ensure dem.grd covers the swath, re-run Step 12\n' ...
               '  (fix_pscands_SM.sh) or regenerate heights so\n' ...
               '  ij == ll == hgt counts, then re-run insar_time_ps.'], nFail);
    end

    fprintf('  All %d location(s) passed (ij == ll == hgt, non-empty).\n', numel(patchDirs));
    fprintf('--------------------------------------------\n');
end

function patchDirs = discover_patch_dirs()
% Prefer patch.list; else PATCH_* dirs; else cwd if it has pscands.1.ij.
    patchDirs = {};

    if exist('patch.list', 'file')
        fid = fopen('patch.list', 'r');
        if fid >= 0
            while true
                line = fgetl(fid);
                if ~ischar(line), break; end
                line = strtrim(line);
                if isempty(line), continue; end
                if exist(line, 'dir')
                    patchDirs{end+1} = line; %#ok<AGROW>
                end
            end
            fclose(fid);
        end
    end

    if isempty(patchDirs)
        d = dir('PATCH_*');
        names = {d([d.isdir]).name};
        if ~isempty(names)
            % natural-ish sort: PATCH_1, PATCH_2, ...
            [~, ix] = sort(names);
            patchDirs = names(ix);
        end
    end

    if isempty(patchDirs) && exist('pscands.1.ij', 'file')
        patchDirs = {'.'};
    end
end

function [ok, msg] = check_one_patch_pscands(pdir)
% Check one directory for consistent, non-empty pscands files.
    ijFile  = fullfile(pdir, 'pscands.1.ij');
    llFile  = fullfile(pdir, 'pscands.1.ll');
    hgtFile = fullfile(pdir, 'pscands.1.hgt');

    missing = {};
    if ~exist(ijFile,  'file'), missing{end+1} = 'pscands.1.ij';  end %#ok<AGROW>
    if ~exist(llFile,  'file'), missing{end+1} = 'pscands.1.ll';  end %#ok<AGROW>
    if ~exist(hgtFile, 'file'), missing{end+1} = 'pscands.1.hgt'; end %#ok<AGROW>
    if ~isempty(missing)
        ok = false;
        msg = sprintf('missing: %s', strjoin(missing, ', '));
        return;
    end

    ijInfo  = dir(ijFile);
    llInfo  = dir(llFile);
    hgtInfo = dir(hgtFile);

    zero = {};
    if ijInfo.bytes  == 0, zero{end+1} = 'pscands.1.ij (0 bytes)';  end %#ok<AGROW>
    if llInfo.bytes  == 0, zero{end+1} = 'pscands.1.ll (0 bytes)';  end %#ok<AGROW>
    if hgtInfo.bytes == 0, zero{end+1} = 'pscands.1.hgt (0 bytes)'; end %#ok<AGROW>
    if ~isempty(zero)
        ok = false;
        msg = sprintf('empty file(s): %s', strjoin(zero, ', '));
        return;
    end

    if mod(llInfo.bytes, 8) ~= 0
        ok = false;
        msg = sprintf('pscands.1.ll size %d not divisible by 8', llInfo.bytes);
        return;
    end
    if mod(hgtInfo.bytes, 4) ~= 0
        ok = false;
        msg = sprintf('pscands.1.hgt size %d not divisible by 4', hgtInfo.bytes);
        return;
    end

    n_ij  = count_text_lines(ijFile);
    n_ll  = llInfo.bytes  / 8;
    n_hgt = hgtInfo.bytes / 4;

    if n_ij <= 0
        ok = false;
        msg = 'pscands.1.ij has 0 lines';
        return;
    end

    if n_ij == n_ll && n_ll == n_hgt
        ok = true;
        msg = sprintf('ij=%d  ll=%d  hgt=%d', n_ij, n_ll, n_hgt);
    else
        ok = false;
        msg = sprintf('count mismatch: ij=%d  ll=%d  hgt=%d', n_ij, n_ll, n_hgt);
    end
end

function n = count_text_lines(fpath)
% Count non-empty lines (equivalent to wc -l for typical pscands.1.ij).
    fid = fopen(fpath, 'r');
    if fid < 0
        n = -1;
        return;
    end
    n = 0;
    while true
        line = fgetl(fid);
        if ~ischar(line), break; end
        if ~isempty(strtrim(line))
            n = n + 1;
        end
    end
    fclose(fid);
end

function run_step(s, label, totalStart, ~)
% Runs stamps(s,s) and prints timing. stepTimes updated in caller.
    fprintf('\n--- Step %d: %s ---\n', s, label);
    tic;
    stamps(s, s);
    elapsed = toc;
    fprintf('Step %02d finished in %.0fm %.0fs\n', s, floor(elapsed/60), rem(elapsed,60));
    print_elapsed(totalStart);
end

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
    fprintf('PS-InSAR FURTHER STEPS\n');
    fprintf('========================================\n');
    fprintf('\n>> Check unwrapping (unw):\n');
    fprintf('   ps_plot(''u'')\n');
    fprintf('   ps_baseline_plot\n');
    fprintf('   ps_info\n');
    fprintf('\n   Get index of inconsistent/non-smooth images. To drop:\n');
    fprintf('   setparm(''drop_ifg_index'', [73,75,77,80:83])\n');
    fprintf('\n>> Plot velocity:\n');
    fprintf('   ps_plot(''V-dao'',''a_linear'')\n');
    fprintf('\n>> Export to CSV:\n');
    fprintf('   ps_export_csv_ps\n');
    fprintf('   Then use InSAR Explorer plugin in QGIS.\n');
    fprintf('   Use https://www.epochconverter.com to convert\n');
    fprintf('   date format (days since Day 0) to YYYYMMDD.\n');
    fprintf('\n>> Export to KML:\n');
    fprintf('   ps_save_kml_ps\n');
    fprintf('\n>> Plot with Hillshade background:\n');
    fprintf('   1. Run: python stamp_dem_prep.py --geo projected_dem.par\n');
    fprintf('   2. Save *.raw and demparms.in into INSAR_YYYYMMDD folder.\n');
    fprintf('   3. Then run: ps_plot(''v'',2)\n');
    fprintf('========================================\n');
end