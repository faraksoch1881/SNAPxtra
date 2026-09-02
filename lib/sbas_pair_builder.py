#!/usr/bin/env python3
"""SBAS pair list, connect_sb bridging, and network plots."""
import os
import numpy as np
from collections import defaultdict
from datetime import datetime
import matplotlib.pyplot as plt

class SBASPairBuilder:

    def __init__(self, baselines_file, proj_root, config, insar_method=1, insar_target=1):
        self.baselines_file = baselines_file
        self.proj_root = proj_root
        self.config = config
        self.insar_method = insar_method
        self.insar_target = insar_target
        self.no_loop = int(config.get('no_loop', 1))
        self.max_loop = int(config.get('max_loop', 999))
        self.connect_network = config.get('connect_network', True)
        self.temporal_threshold = float(config.get('temporal', 90))
        self.baseline_threshold = float(config.get('baseline', 200))
        if isinstance(self.connect_network, str):
            self.connect_network = self.connect_network.lower() in ['true', '1', 'yes']
        self.metadata_dir = os.path.join(self.proj_root, 'metadata_info')
        if not os.path.exists(self.metadata_dir):
            os.makedirs(self.metadata_dir, exist_ok=True)
        self.scenes = []
        self.baseline_dict = {}
        self.all_pairs = []
        self.sbas_pairs = []
        self.connect_pairs = []
        self.bridging_pairs = []
        self.master_date = None

    def load_baselines(self):
        if not os.path.exists(self.baselines_file):
            print(f'ERROR: Baselines file not found: {self.baselines_file}')
            return False
        print(f'Loading baselines from: {self.baselines_file}')
        try:
            data = np.loadtxt(self.baselines_file, dtype=str)
        except:
            print(f'ERROR: Failed to load baselines file')
            return False
        self.scenes = []
        seen_dates = set()
        if len(data) > 0:
            first_row = data[0] if len(data.shape) > 1 else data
            self.master_date = first_row[0]
        for row in data:
            if len(row) < 4:
                continue
            master_date = row[0]
            slave_date = row[1]
            temporal = float(row[2])
            perp = float(row[3])
            if master_date not in seen_dates:
                self.scenes.append({'date': master_date, 'temporal': 0.0, 'perp': 0.0})
                seen_dates.add(master_date)
            if slave_date not in seen_dates:
                self.scenes.append({'date': slave_date, 'temporal': temporal, 'perp': perp})
                seen_dates.add(slave_date)
        self.scenes.sort(key=lambda x: x['date'])
        for scene in self.scenes:
            self.baseline_dict[scene['date']] = scene
        print(f'  Loaded {len(self.scenes)} unique dates')
        return True

    def create_all_pairs(self):
        self.all_pairs = []
        for i in range(len(self.scenes)):
            for j in range(i + 1, len(self.scenes)):
                master = self.scenes[i]
                slave = self.scenes[j]
                temp_diff = abs(slave['temporal'] - master['temporal'])
                baseline_diff = abs(slave['perp'] - master['perp'])
                self.all_pairs.append({'date1': master['date'], 'date2': slave['date'], 'temp_diff': temp_diff, 'baseline_diff': baseline_diff, 'temporal1': master['temporal'], 'perp1': master['perp'], 'temporal2': slave['temporal'], 'perp2': slave['perp']})
        print(f'Created {len(self.all_pairs)} potential pairs')
        return len(self.all_pairs)

    def apply_threshold_filtering(self, pairs):
        print(f'Applying threshold filtering (temporal={self.temporal_threshold} days, baseline={self.baseline_threshold} m)...')
        filtered = []
        removed = []
        for pair in pairs:
            if pair['temp_diff'] <= self.temporal_threshold and pair['baseline_diff'] <= self.baseline_threshold:
                filtered.append(pair)
            else:
                removed.append(pair)
        print(f'  After threshold filtering: {len(filtered)} pairs kept')
        print(f'  Removed: {len(removed)} pairs (exceeded temporal or baseline threshold)')
        return (filtered, removed)

    def apply_no_loop_filtering(self):
        return self.apply_no_loop_filtering_on_pairs(self.all_pairs)

    def apply_no_loop_filtering_on_pairs(self, pairs):
        if not self.no_loop or self.no_loop <= 0:
            print(f'Skipping no_loop filtering (no_loop={self.no_loop})')
            return (pairs, [])
        print(f'Applying no_loop={self.no_loop} filtering...')
        adj = defaultdict(set)
        for pair in pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            adj[d1].add(d2)
            adj[d2].add(d1)

        def is_connected_without_edge(nodes, adjacency, skip_edge):
            if len(nodes) == 0:
                return True
            temp_adj = defaultdict(set)
            for node in nodes:
                for neighbor in adjacency[node]:
                    if (node, neighbor) != skip_edge and (neighbor, node) != skip_edge:
                        temp_adj[node].add(neighbor)
            start = next(iter(nodes))
            visited = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                for neighbor in temp_adj[node]:
                    if neighbor not in visited:
                        stack.append(neighbor)
            return len(visited) == len(nodes)
        all_nodes = set(adj.keys())
        kept_pairs = []
        pairs_in_triangles = set()
        for pair in pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            common_neighbors = adj[d1].intersection(adj[d2])
            forms_triangle = len(common_neighbors) >= self.no_loop
            if forms_triangle:
                kept_pairs.append(pair)
                pairs_in_triangles.add(tuple(sorted([d1, d2])))
        changed = True
        while changed:
            changed = False
            temp_adj = defaultdict(set)
            for pair in kept_pairs:
                d1, d2 = (pair['date1'], pair['date2'])
                temp_adj[d1].add(d2)
                temp_adj[d2].add(d1)
            pairs_to_remove = []
            for pair in kept_pairs:
                d1, d2 = (pair['date1'], pair['date2'])
                pair_key = tuple(sorted([d1, d2]))
                if pair_key in pairs_in_triangles:
                    continue
                degree_a = len(temp_adj[d1])
                degree_b = len(temp_adj[d2])
                if degree_a == 1 or degree_b == 1:
                    temp_nodes = set(temp_adj.keys())
                    temp_nodes.discard(d1 if degree_a == 1 else d2)
                    if len(temp_nodes) > 0:
                        pairs_to_remove.append(pair)
                        changed = True
            for pair in pairs_to_remove:
                kept_pairs.remove(pair)
        critical_bridges = []
        temp_adj = defaultdict(set)
        for pair in kept_pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            temp_adj[d1].add(d2)
            temp_adj[d2].add(d1)
        remaining_nodes = set(temp_adj.keys())
        for pair in kept_pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            pair_key = tuple(sorted([d1, d2]))
            if pair_key not in pairs_in_triangles:
                if not is_connected_without_edge(remaining_nodes, temp_adj, (d1, d2)):
                    critical_bridges.append(pair)
        kept_pair_keys = {tuple(sorted([p['date1'], p['date2']])) for p in kept_pairs}
        dropped_pairs = [p for p in pairs if tuple(sorted([p['date1'], p['date2']])) not in kept_pair_keys]
        print(f'  After no_loop filtering: {len(kept_pairs)} pairs ({len(pairs_in_triangles)} in triangles, {len(critical_bridges)} critical bridges)')
        print(f'  Dropped: {len(dropped_pairs)} pairs')
        return (kept_pairs, dropped_pairs)

    def apply_max_loop_filtering(self, pairs):
        if not self.max_loop or self.max_loop <= 0:
            print(f'Skipping max_loop filtering (max_loop={self.max_loop} = unlimited)')
            return (pairs, [])
        print(f'Applying max_loop={self.max_loop} filtering...')
        date_neighbors = defaultdict(list)
        for pair in pairs:
            temp_diff = pair['temp_diff']
            date_neighbors[pair['date1']].append((pair['date2'], temp_diff))
            date_neighbors[pair['date2']].append((pair['date1'], temp_diff))
        keep_pairs = set()
        for date, neighbors in date_neighbors.items():
            neighbors.sort(key=lambda x: x[1])
            for n, _ in neighbors[:self.max_loop]:
                keep_pairs.add(tuple(sorted([date, n])))
        kept_pairs = [p for p in pairs if tuple(sorted([p['date1'], p['date2']])) in keep_pairs]
        removed_pairs = [p for p in pairs if tuple(sorted([p['date1'], p['date2']])) not in keep_pairs]
        print(f'  After max_loop filtering: {len(kept_pairs)} pairs kept')
        print(f'  Removed: {len(removed_pairs)} pairs')
        return (kept_pairs, removed_pairs)

    def connect_fragmented_networks(self, pairs):
        if not self.connect_network:
            print('Network connection disabled (connect_network=False)')
            return (pairs, [])
        dates = set()
        for pair in pairs:
            dates.add(pair['date1'])
            dates.add(pair['date2'])
        adj = defaultdict(set)
        for pair in pairs:
            adj[pair['date1']].add(pair['date2'])
            adj[pair['date2']].add(pair['date1'])
        visited = set()
        components = []

        def dfs(node, comp):
            visited.add(node)
            comp.add(node)
            for neigh in adj[node]:
                if neigh not in visited:
                    dfs(neigh, comp)
        for date in dates:
            if date not in visited:
                comp = set()
                dfs(date, comp)
                components.append(comp)
        print(f'Network analysis: {len(components)} component(s)')
        if len(components) <= 1:
            print('Network is fully connected - no bridging needed')
            return (pairs, [])
        components.sort(key=lambda x: min(x))
        print(f'Found {len(components)} disconnected components - adding bridging pairs')
        bridging_pairs = []
        for i in range(len(components) - 1):
            comp1 = components[i]
            comp2 = components[i + 1]
            date1 = max(comp1)
            date2 = min(comp2)
            print(f'  Component {i + 1}: {min(comp1)} to {max(comp1)} ({len(comp1)} dates)')
            print(f'  Component {i + 2}: {min(comp2)} to {max(comp2)} ({len(comp2)} dates)')
            if date1 in self.baseline_dict and date2 in self.baseline_dict:
                scene1 = self.baseline_dict[date1]
                scene2 = self.baseline_dict[date2]
                temp_diff = abs(scene2['temporal'] - scene1['temporal'])
                baseline_diff = abs(scene2['perp'] - scene1['perp'])
                bridging_pair = {'date1': date1, 'date2': date2, 'temp_diff': temp_diff, 'baseline_diff': baseline_diff, 'temporal1': scene1['temporal'], 'perp1': scene1['perp'], 'temporal2': scene2['temporal'], 'perp2': scene2['perp']}
                bridging_pairs.append(bridging_pair)
                pairs.append(bridging_pair)
                print(f'  → Bridging: {date1} to {date2} (dt={temp_diff:.1f} days, db={baseline_diff:.1f} m)')
        if bridging_pairs:
            print(f'Added {len(bridging_pairs)} bridging pair(s)')
        return (pairs, bridging_pairs)

    def remove_leaf_nodes(self, pairs, bridging_pairs):
        if not pairs:
            return (pairs, bridging_pairs)
        adj = defaultdict(set)
        edge_set = set()
        for pair in pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            adj[d1].add(d2)
            adj[d2].add(d1)
            edge_set.add((d1, d2))
            edge_set.add((d2, d1))
        pairs_in_triangles = set()
        for pair in pairs:
            d1, d2 = (pair['date1'], pair['date2'])
            common_neighbors = adj[d1].intersection(adj[d2])
            for c in common_neighbors:
                if (d1, c) in edge_set and (d2, c) in edge_set:
                    pairs_in_triangles.add(tuple(sorted([d1, d2])))
                    break
        bridging_keys = set()
        for bp in bridging_pairs:
            bridging_keys.add(tuple(sorted([bp['date1'], bp['date2']])))
        changed = True
        removed_pairs = []
        while changed:
            changed = False
            temp_adj = defaultdict(set)
            for pair in pairs:
                d1, d2 = (pair['date1'], pair['date2'])
                temp_adj[d1].add(d2)
                temp_adj[d2].add(d1)
            pairs_to_remove = []
            for pair in pairs:
                d1, d2 = (pair['date1'], pair['date2'])
                pair_key = tuple(sorted([d1, d2]))
                if pair_key in pairs_in_triangles:
                    continue
                if pair_key in bridging_keys:
                    continue
                degree_a = len(temp_adj[d1])
                degree_b = len(temp_adj[d2])
                if degree_a == 1 or degree_b == 1:
                    temp_nodes = set(temp_adj.keys())
                    temp_nodes.discard(d1 if degree_a == 1 else d2)
                    if len(temp_nodes) > 0:
                        pairs_to_remove.append(pair)
                        removed_pairs.append(pair)
                        changed = True
            for pair in pairs_to_remove:
                pairs.remove(pair)
        if removed_pairs:
            print(f'  Removed {len(removed_pairs)} leaf pair(s) created by baseline filtering:')
            for pair in removed_pairs:
                print(f"    {pair['date1']} - {pair['date2']} (dt={pair['temp_diff']:.0f} days, db={pair['baseline_diff']:.0f} m)")
        else:
            print(f'  No leaf nodes found - network is clean ✓')
        print(f'SBAS pairs after leaf removal: {len(pairs)} pairs')
        print(f'\nRemoving isolated dates at component extremes...')
        removed_bridges = []
        changed = True
        while changed:
            changed = False
            adj_no_bridges = defaultdict(set)
            bridging_keys = {tuple(sorted([bp['date1'], bp['date2']])) for bp in bridging_pairs}
            for pair in pairs:
                pair_key = tuple(sorted([pair['date1'], pair['date2']]))
                if pair_key not in bridging_keys:
                    d1, d2 = (pair['date1'], pair['date2'])
                    adj_no_bridges[d1].add(d2)
                    adj_no_bridges[d2].add(d1)
            all_dates = set()
            for pair in pairs:
                all_dates.add(pair['date1'])
                all_dates.add(pair['date2'])
            isolated_dates = set()
            for date in all_dates:
                if len(adj_no_bridges[date]) == 0:
                    isolated_dates.add(date)
            if isolated_dates:
                print(f'  Found {len(isolated_dates)} isolated date(s): {sorted(isolated_dates)}')
                bridges_to_remove = []
                for bp in bridging_pairs:
                    if bp['date1'] in isolated_dates or bp['date2'] in isolated_dates:
                        bridges_to_remove.append(bp)
                        removed_bridges.append(bp)
                        print(f"    Removing bridge: {bp['date1']} - {bp['date2']} (connects to isolated date)")
                for bp in bridges_to_remove:
                    bridging_pairs.remove(bp)
                    pairs.remove(bp)
                if bridges_to_remove:
                    changed = True
            else:
                print(f'  No isolated dates found - network is clean ✓')
        if removed_bridges:
            print(f'Final SBAS pairs after removing isolated extremes: {len(pairs)} pairs')
            print(f'Remaining bridging pairs: {len(bridging_pairs)}')
        return (pairs, bridging_pairs)

    def generate_sbas_pairs(self):
        if not self.load_baselines():
            return False
        self.create_all_pairs()
        threshold_filtered, _ = self.apply_threshold_filtering(self.all_pairs)
        filtered_pairs, _ = self.apply_max_loop_filtering(threshold_filtered)
        self.sbas_pairs, _ = self.apply_no_loop_filtering_on_pairs(filtered_pairs)
        sbas_before_baseline = len(self.sbas_pairs)
        self.sbas_pairs = [p for p in self.sbas_pairs if abs(p['perp1']) < self.baseline_threshold and abs(p['perp2']) < self.baseline_threshold]
        sbas_after_baseline = len(self.sbas_pairs)
        if sbas_before_baseline != sbas_after_baseline:
            print(f'Applying baseline filtering to SBAS pairs...')
            print(f'  Before baseline filtering: {sbas_before_baseline} pairs')
            print(f'  After baseline filtering: {sbas_after_baseline} pairs')
            print(f'  Removed: {sbas_before_baseline - sbas_after_baseline} pairs (scene baseline exceeded)')
        self.sbas_pairs, self.connect_pairs = self.connect_fragmented_networks(self.sbas_pairs)
        print(f'\nRemoving leaf nodes created by baseline filtering...')
        self.sbas_pairs, self.connect_pairs = self.remove_leaf_nodes(self.sbas_pairs, self.connect_pairs)
        sbas_pairs_file = os.path.join(self.metadata_dir, 'sbas_pairs.txt')
        with open(sbas_pairs_file, 'w') as f:
            for pair in self.sbas_pairs:
                f.write(f"{pair['date1']} {pair['date2']} {pair['temp_diff']:.1f} {pair['baseline_diff']:.1f}\n")
        print(f'\n✓ Saved {len(self.sbas_pairs)} SBAS pairs to: {sbas_pairs_file}')
        if self.connect_pairs:
            connect_file = os.path.join(self.metadata_dir, 'connect_sb.txt')
            with open(connect_file, 'w') as f:
                for pair in self.connect_pairs:
                    f.write(f"{pair['date1']} {pair['date2']} {pair['temp_diff']:.1f} {pair['baseline_diff']:.1f}\n")
            print(f'✓ Saved {len(self.connect_pairs)} bridging pairs to: {connect_file}')
        else:
            connect_file = os.path.join(self.proj_root, 'connect_sb.txt')
            if os.path.exists(connect_file):
                os.remove(connect_file)
                print(f'✓ Cleared connect_sb.txt (no bridging pairs)')
        return True

    def generate_network_plot(self, plot_mode=None, print_stats=True):
        if not self.sbas_pairs:
            print('WARNING: No SBAS pairs to plot')
            return False
        if plot_mode == 'ps':
            is_ps_method = True
            prefix = 'network_ps'
            label = 'PS (Point Select) network'
        elif plot_mode == 'sbas':
            is_ps_method = False
            prefix = 'network_sbas'
            label = 'SBAS network'
        else:
            is_ps_method = self.insar_target == 2 and self.insar_method == 2
            prefix = 'network_ps' if is_ps_method else 'network_sbas'
            label = 'network'
        if print_stats:
            print(f'Generating {label} plots...')
        connected_dates = set()
        for pair in self.sbas_pairs:
            connected_dates.add(pair['date1'])
            connected_dates.add(pair['date2'])
        connected_dates_list = sorted(list(connected_dates))
        connected_dates_dt = [datetime.strptime(d, '%Y%m%d') for d in connected_dates_list]
        connected_perp = [self.baseline_dict[d]['perp'] for d in connected_dates_list]
        if print_stats:
            print(f'  Total dates: {len(self.baseline_dict)}')
            print(f'  Connected dates: {len(connected_dates_list)}')
            print(f'  Isolated dates: {len(self.baseline_dict) - len(connected_dates_list)}')
        fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
        if is_ps_method:
            master_dt = datetime.strptime(self.master_date, '%Y%m%d')
            master_perp = self.baseline_dict[self.master_date]['perp']
            for d in connected_dates_list:
                if d == self.master_date:
                    continue
                dt = datetime.strptime(d, '%Y%m%d')
                perp = self.baseline_dict[d]['perp']
                ax.plot([master_dt, dt], [master_perp, perp], color='gray', linewidth=0.8, alpha=0.6, zorder=1)
            ax.scatter(connected_dates_dt, connected_perp, s=100, c='red', edgecolors='black', linewidths=0.5, alpha=0.7, zorder=3)
            for dt, perp, date_str in zip(connected_dates_dt, connected_perp, connected_dates_list):
                label = f'{date_str[6:8]}/{date_str[4:6]}'
                ax.annotate(label, xy=(dt, perp), xytext=(4, 4), textcoords='offset points', fontsize=7, color='black', zorder=5)
            ax.set_title('PS (Point Select) Baseline Network - Star Topology')
        else:
            for pair in self.sbas_pairs:
                d1, d2 = (pair['date1'], pair['date2'])
                if d1 not in self.baseline_dict or d2 not in self.baseline_dict:
                    continue
                dt1 = datetime.strptime(d1, '%Y%m%d')
                dt2 = datetime.strptime(d2, '%Y%m%d')
                p1 = self.baseline_dict[d1]['perp']
                p2 = self.baseline_dict[d2]['perp']
                is_bridge = any((p['date1'] == d1 and p['date2'] == d2 for p in self.connect_pairs)) or any((p['date1'] == d2 and p['date2'] == d1 for p in self.connect_pairs))
                if is_bridge:
                    ax.plot([dt1, dt2], [p1, p2], color='red', linewidth=1.5, alpha=0.8, zorder=2, label='Bridging' if 'Bridging' not in [l.get_label() for l in ax.get_lines()] else '')
                else:
                    ax.plot([dt1, dt2], [p1, p2], color='gray', linewidth=0.8, alpha=0.6, zorder=1)
            ax.scatter(connected_dates_dt, connected_perp, s=100, c='red', edgecolors='black', linewidths=0.5, alpha=0.7, zorder=3)
            for dt, perp, date_str in zip(connected_dates_dt, connected_perp, connected_dates_list):
                label = f'{date_str[6:8]}/{date_str[4:6]}'
                ax.annotate(label, xy=(dt, perp), xytext=(4, 4), textcoords='offset points', fontsize=7, color='black', zorder=5)
            ax.set_title('SBAS (Small Baseline Subset) Network')
            if self.connect_pairs:
                ax.legend(loc='best', fontsize=8)
        import matplotlib.dates as mdates
        date_range_days = (max(connected_dates_dt) - min(connected_dates_dt)).days
        if date_range_days < 365:
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
            ax.set_xlabel('Month')
        elif date_range_days < 730:
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
            ax.set_xlabel('Date')
        else:
            years = sorted(set([dt.year for dt in connected_dates_dt]))
            ax.set_xticks([datetime(year, 1, 1) for year in years])
            ax.set_xticklabels([str(y) for y in years])
            ax.set_xlabel('Year')
        ax.set_ylabel('Perpendicular Baseline (m)')
        fig.tight_layout()
        png_file = os.path.join(self.metadata_dir, f'{prefix}.png')
        fig.savefig(png_file, dpi=150, bbox_inches='tight')
        print(f'  ✓ Saved: {png_file}')
        pdf_file = os.path.join(self.metadata_dir, f'{prefix}.pdf')
        fig.savefig(pdf_file, dpi=150, bbox_inches='tight')
        print(f'  ✓ Saved: {pdf_file}')
        plt.close(fig)
        return True

    def generate_both_network_plots(self):
        ok_sbas = self.generate_network_plot(plot_mode='sbas', print_stats=True)
        ok_ps = self.generate_network_plot(plot_mode='ps', print_stats=False)
        return ok_sbas and ok_ps

def create_sbas_pairs_and_plot(baselines_file, proj_root, config):
    builder = SBASPairBuilder(baselines_file, proj_root, config)
    if not builder.generate_sbas_pairs():
        return False
    if not builder.generate_network_plot():
        print('WARNING: Failed to generate network plot')
        return False
    return True
