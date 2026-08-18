/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {countQueries, dashboardLayout, gridPlacement, scopedQueries, visualPanels} from '../src/dashboard';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {DashboardPanel} from '../src/types';

test('expanded and collapsed rows retain their groups, panels, and queries', () => {
  const panels = [
    {type: 'stat', targets: [{refId: 'A', expr: 'SBK_a{sbk_endpoint_id=~"${sbk_endpoints:regex}"}'}]},
    {type: 'row', title: 'Expanded', collapsed: false, gridPos: {y: 1}, panels: []},
    {type: 'timeseries', title: 'Expanded child', gridPos: {y: 2}, targets: [{refId: 'B'}]},
    {
      type: 'row', title: 'Collapsed', collapsed: true, gridPos: {y: 3},
      panels: [{type: 'timeseries', title: 'Collapsed child', gridPos: {y: 4}, targets: [{refId: 'C'}]}],
    },
  ];
  const layout = dashboardLayout(panels);
  expect(layout).toHaveLength(3);
  expect(layout[1]).toMatchObject({kind: 'row', title: 'Expanded', collapsed: false});
  expect(layout[1].kind === 'row' && layout[1].panels.map((panel) => panel.title)).toEqual(['Expanded child']);
  expect(layout[2]).toMatchObject({kind: 'row', title: 'Collapsed', collapsed: true});
  expect(layout[2].kind === 'row' && layout[2].panels.map((panel) => panel.title)).toEqual(['Collapsed child']);
  expect(visualPanels(panels)).toHaveLength(3);
  expect(countQueries(panels)).toBe(3);
  expect(gridPlacement(panels[2])).toEqual({x: 0, y: 2, width: 12, height: 8});
});

test('queries are cloned and scoped to the selected time group', () => {
  const panel = {
    type: 'timeseries',
    targets: [{refId: 'A', expr: 'SBK_a{sbk_endpoint_id=~"${sbk_endpoints:regex}"}'}],
  };
  const selected = scopedQueries(panel, ['1111111111111111', '2222222222222222']);
  expect(selected[0].expr).toContain('1111111111111111|2222222222222222');
  expect(panel.targets[0].expr).toContain('${sbk_endpoints:regex}');
});

test('canonical dashboard retains every row and its exact panel membership', () => {
  const source = resolve(process.cwd(), '../src/sbk_dashboard/resources/grafana/dashboards/sbk-dashboard.json');
  const canonical = JSON.parse(readFileSync(source, 'utf-8')) as {panels: DashboardPanel[]};
  const layout = dashboardLayout(canonical.panels);
  const rows = layout.filter((item) => item.kind === 'row');
  expect(layout).toHaveLength(7);
  expect(rows.map((row) => row.title)).toEqual([
    'SBK Connections',
    'SBK Readers and Writers',
    'Write Performance Benchmarking',
    'Read Performance Benchmarking',
    'Write-Read Performance Benchmarking (End to End Latencies)',
    'Write-Only Read Benchmarking',
  ]);
  expect(rows.map((row) => row.panels.length)).toEqual([2, 4, 10, 10, 10, 10]);
  expect(rows.map((row) => row.collapsed)).toEqual([false, false, true, true, true, false]);
  expect(visualPanels(canonical.panels)).toHaveLength(47);
});
