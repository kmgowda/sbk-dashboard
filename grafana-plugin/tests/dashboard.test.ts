/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {countQueries, scopedQueries, visualPanels} from '../src/dashboard';

test('row contents are flattened without losing visual panels or queries', () => {
  const panels = [
    {type: 'stat', targets: [{refId: 'A', expr: 'SBK_a{sbk_endpoint_id=~"${sbk_endpoints:regex}"}'}]},
    {type: 'row', panels: [{type: 'timeseries', targets: [{refId: 'B'}, {refId: 'C'}]}]},
  ];
  expect(visualPanels(panels)).toHaveLength(2);
  expect(countQueries(panels)).toBe(3);
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
