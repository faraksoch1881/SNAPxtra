function []=sb_info_filt()
%SB_INFO_FILT display filtered small baseline interferogram list and analyze network connectivity
%
%   Subtracts drop_ifg_index list from the full sb_info list,
%   prints remaining interferograms, then analyzes the network for
%   disconnected subsets and suggests which dropped IFGs to restore
%   to maintain a fully connected network.
%
%   For each pair of adjacent disconnected subsets, all simple paths
%   through dropped IFGs are enumerated and printed as candidate bridges.
%
%   Based on sb_info.m by Andy Hooper, January 2010
%   Extended for filtering and network analysis.

psvdata  = load('psver');
psver    = psvdata.psver;
psname   = ['ps', num2str(psver)];
ps       = load(psname);

% Load std information
ifgstdname = ['ifgstd', num2str(psver)];
if exist([ifgstdname, '.mat'], 'file')
    stdin   = load(ifgstdname);
    ifg_std = stdin.ifg_std;
    clear stdin
else
    ifg_std = zeros(ps.n_ifg, 1);
end

if ~isfield(ps, 'ifgday')
    fprintf('This is not a small baseline directory\n');
    return
end

% Get the list of dropped IFG indices
list_ifg = getparm('drop_ifg_index');

% Build kept indices (all IFGs minus dropped ones)
all_idx  = 1:ps.n_ifg;
kept_idx = setdiff(all_idx, list_ifg);

fprintf('\n=== Filtered Interferogram List (after removing drop_ifg_index) ===\n\n');
for k = 1:length(kept_idx)
    i  = kept_idx(k);
    aa = [datestr(ps.ifgday(i,1)), ' to ', datestr(ps.ifgday(i,2))];
    fprintf('%3s  %s %5s m %.3f deg\n', ...
        num2str(i), aa, num2str(round(ps.bperp(i))), ifg_std(i));
end
fprintf('\nNumber of kept interferograms: %d / %d\n', length(kept_idx), ps.n_ifg);
fprintf('Number of stable-phase pixels: %d\n', ps.n_ps);

% -----------------------------------------------------------------------
% Network connectivity analysis using Union-Find on kept IFGs
% -----------------------------------------------------------------------

% All unique dates in kept IFGs
dates_kept = unique([ps.ifgday(kept_idx,1); ps.ifgday(kept_idx,2)]);
n_nodes    = length(dates_kept);
date2node  = containers.Map(num2cell(dates_kept), num2cell(1:n_nodes));

parent = 1:n_nodes;
rnk    = zeros(1, n_nodes);

    function r = find_root(x)
        while parent(x) ~= x
            parent(x) = parent(parent(x));
            x = parent(x);
        end
        r = x;
    end

    function union_nodes(a, b)
        ra = find_root(a);
        rb = find_root(b);
        if ra == rb, return; end
        if rnk(ra) < rnk(rb), tmp = ra; ra = rb; rb = tmp; end
        parent(rb) = ra;
        if rnk(ra) == rnk(rb), rnk(ra) = rnk(ra) + 1; end
    end

for k = 1:length(kept_idx)
    i  = kept_idx(k);
    union_nodes(date2node(ps.ifgday(i,1)), date2node(ps.ifgday(i,2)));
end

roots    = arrayfun(@find_root, 1:n_nodes);
comp_ids = unique(roots);
n_comps  = length(comp_ids);

fprintf('\n=== Network Connectivity Analysis ===\n');

if n_comps == 1
    fprintf('\nThe filtered network is FULLY CONNECTED. No subsets detected.\n');
    fprintf('\n');
    return
end

fprintf('\nWARNING: The filtered network has %d DISCONNECTED SUBSETS.\n', n_comps);

% Collect and sort each subset's dates chronologically
subset_dates = cell(n_comps, 1);
for c = 1:n_comps
    comp_mask       = (roots == comp_ids(c));
    cd              = sort(dates_kept(comp_mask));
    subset_dates{c} = cd;
    fprintf('\n  Subset %d  (%d acquisitions, spanning %s to %s):\n', ...
        c, length(cd), datestr(cd(1)), datestr(cd(end)));
    fprintf('    Dates: ');
    for d = 1:length(cd)
        fprintf('%s  ', datestr(cd(d)));
    end
    fprintf('\n');
end

% Sort subsets chronologically by their first date
first_dates = cellfun(@(x) x(1), subset_dates);
[~, sort_order] = sort(first_dates);
subset_dates = subset_dates(sort_order);

% -----------------------------------------------------------------------
% Build a graph from ALL dropped IFGs (candidate bridge edges)
% Nodes = all unique dates that appear in ANY dropped IFG
% -----------------------------------------------------------------------
valid_drop = list_ifg(list_ifg >= 1 & list_ifg <= ps.n_ifg);

drop_d1 = ps.ifgday(valid_drop, 1);
drop_d2 = ps.ifgday(valid_drop, 2);

all_drop_dates = unique([drop_d1; drop_d2]);
n_dnodes       = length(all_drop_dates);
ddate2node     = containers.Map(num2cell(all_drop_dates), num2cell(1:n_dnodes));

% Adjacency list for dropped-IFG graph: adj{node} = list of [neighbour, ifg_index]
adj = cell(n_dnodes, 1);
for k = 1:length(valid_drop)
    i  = valid_drop(k);
    d1 = ps.ifgday(i, 1);
    d2 = ps.ifgday(i, 2);
    n1 = ddate2node(d1);
    n2 = ddate2node(d2);
    adj{n1} = [adj{n1}; n2, i];
    adj{n2} = [adj{n2}; n1, i];   % undirected: SAR IFGs connect both directions
end

% -----------------------------------------------------------------------
% For each adjacent subset pair, enumerate all simple paths via dropped IFGs
% from the END node of subset A to the START node of subset B
% -----------------------------------------------------------------------
fprintf('\n=== Suggested IFGs to RESTORE (from drop_ifg_index) to fix connectivity ===\n');

all_bridging = [];

for pair = 1:(length(subset_dates)-1)
    sA = subset_dates{pair};
    sB = subset_dates{pair+1};

    end_A   = sA(end);    % latest date in subset A
    start_B = sB(1);      % earliest date in subset B

    fprintf('\nSubset %d -- Subset %d\n', pair, pair+1);
    fprintf('  Bridging from %s  -->  %s\n', datestr(end_A), datestr(start_B));

    % Check that both boundary nodes exist in the dropped-IFG graph
    if ~isKey(ddate2node, end_A) || ~isKey(ddate2node, start_B)
        fprintf('  No dropped IFGs touch these boundary dates. Cannot bridge automatically.\n');
        fprintf('%s\n', repmat('=',1,63));
        continue
    end

    src = ddate2node(end_A);
    dst = ddate2node(start_B);

    % DFS to find all simple paths from src to dst using dropped IFGs only
    % Each path is stored as a list of IFG indices
    paths = find_all_paths(adj, n_dnodes, src, dst, all_drop_dates);

    if isempty(paths)
        fprintf('  No path found through dropped IFGs between these two dates.\n');
    else
        for p = 1:length(paths)
            path_ifgs  = paths{p};        % IFG indices along this path
            path_nodes = path_node_dates(path_ifgs, ps, end_A);

            fprintf('\n  Bridge %d:\n', p);
            fprintf('  ');
            for nd = 1:length(path_nodes)
                if nd < length(path_nodes)
                    fprintf('%s --> ', datestr(path_nodes(nd)));
                else
                    fprintf('%s\n', datestr(path_nodes(nd)));
                end
            end
            fprintf('\n  Keep:\n');
            for ki = 1:length(path_ifgs)
                ii = path_ifgs(ki);
                aa = [datestr(ps.ifgday(ii,1)), ' to ', datestr(ps.ifgday(ii,2))];
                fprintf('  %3d  %s %5s m %.3f deg\n', ...
                    ii, aa, num2str(round(ps.bperp(ii))), ifg_std(ii));
                all_bridging(end+1) = ii; %#ok<AGROW>
            end
        end
    end
    fprintf('\n%s\n', repmat('=',1,63));
end

all_bridging = unique(all_bridging);

if ~isempty(all_bridging)

    fprintf('\n  Suggested command (restore ALL bridge IFGs):\n');

    fprintf('\n Restore ifg to Network:\n');
    fprintf('    setparm(''drop_ifg_index'', setdiff(getparm(''drop_ifg_index''), [');
    fprintf('%d ', all_bridging);
    fprintf(']))\n');


    fprintf('\n  Remove from Network:\n');
    fprintf('    setparm(''drop_ifg_index'', union(getparm(''drop_ifg_index''), [');
    fprintf('%d ', all_bridging);
    fprintf(']))\n');
end

fprintf('\n');
end  % main function


% -----------------------------------------------------------------------
% DFS: find all simple paths from src to dst in adjacency list adj
% Returns cell array of paths, each path = vector of IFG indices
% -----------------------------------------------------------------------
function paths = find_all_paths(adj, n_nodes, src, dst, all_dates)
    paths   = {};
    visited = false(1, n_nodes);
    dfs(src, []);

    function dfs(node, ifg_path)
        if node == dst
            paths{end+1} = ifg_path; %#ok<AGROW>
            return
        end
        visited(node) = true;
        nbrs = adj{node};
        for ni = 1:size(nbrs, 1)
            nb      = nbrs(ni, 1);
            ifg_idx = nbrs(ni, 2);
            % Only traverse forward in time (IFGs are ordered master < slave)
            if ~visited(nb) && all_dates(nb) > all_dates(node)
                dfs(nb, [ifg_path, ifg_idx]);
            end
        end
        visited(node) = false;
    end
end


% -----------------------------------------------------------------------
% Reconstruct the ordered node-date sequence for a path of IFGs
% starting from start_date
% -----------------------------------------------------------------------
function node_dates = path_node_dates(ifg_path, ps, start_date)
    node_dates = start_date;
    current    = start_date;
    for k = 1:length(ifg_path)
        ii = ifg_path(k);
        d1 = ps.ifgday(ii, 1);
        d2 = ps.ifgday(ii, 2);
        if d1 == current
            current = d2;
        else
            current = d1;
        end
        node_dates(end+1) = current; %#ok<AGROW>
    end
end