/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {DashboardPanel} from './types';
import {SceneDataQuery} from '@grafana/scenes';

const ENDPOINT_VARIABLE = '${sbk_endpoints:regex}';

export function visualPanels(panels: DashboardPanel[]): DashboardPanel[] {
  const output: DashboardPanel[] = [];
  for (const panel of panels || []) {
    if (panel.type === 'row') output.push(...visualPanels(panel.panels || []));
    else if (panel.type && Array.isArray(panel.targets)) output.push(panel);
  }
  return output;
}

export function scopedQueries(panel: DashboardPanel, targetIds: string[]): SceneDataQuery[] {
  const matcher = targetIds.join('|');
  return (panel.targets || []).map((query) => {
    const copy = {...query};
    if (typeof copy.expr === 'string') copy.expr = copy.expr.split(ENDPOINT_VARIABLE).join(matcher);
    return copy as SceneDataQuery;
  });
}

export function countQueries(panels: DashboardPanel[]): number {
  return visualPanels(panels).reduce((count, panel) => count + (panel.targets?.length || 0), 0);
}
