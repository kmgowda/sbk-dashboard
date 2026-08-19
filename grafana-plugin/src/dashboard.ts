/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {DashboardLayoutItem, DashboardPanel, DashboardRowItem} from './types';
import {SceneDataQuery} from '@grafana/scenes';

const ENDPOINT_VARIABLE = '${sbk_endpoints:regex}';
const DEFAULT_PANEL_WIDTH = 12;
const DEFAULT_PANEL_HEIGHT = 8;

export function gridPlacement(panel: DashboardPanel) {
  return {
    x: panel.gridPos?.x || 0,
    y: panel.gridPos?.y || 0,
    width: panel.gridPos?.w || DEFAULT_PANEL_WIDTH,
    height: panel.gridPos?.h || DEFAULT_PANEL_HEIGHT,
  };
}

export function visualPanels(panels: DashboardPanel[]): DashboardPanel[] {
  return dashboardLayout(panels).flatMap((item) => item.kind === 'panel' ? [item.panel] : item.panels);
}

export function dashboardLayout(panels: DashboardPanel[]): DashboardLayoutItem[] {
  const output: DashboardLayoutItem[] = [];
  let currentRow: DashboardRowItem | undefined;
  for (const panel of panels || []) {
    if (panel.type === 'row') {
      currentRow = {
        kind: 'row',
        title: panel.title || 'Dashboard row',
        collapsed: panel.collapsed === true,
        gridPos: panel.gridPos || {},
        panels: visualPanels(panel.panels || []),
      };
      output.push(currentRow);
    } else if (panel.type && Array.isArray(panel.targets)) {
      if (currentRow) currentRow.panels.push(panel);
      else output.push({kind: 'panel', panel});
    }
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
