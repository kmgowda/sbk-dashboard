/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {
  EmbeddedScene,
  SceneControlsSpacer,
  SceneFlexItem,
  SceneFlexLayout,
  SceneQueryRunner,
  SceneRefreshPicker,
  SceneObject,
  SceneDataProvider,
  SceneDataTransformer,
  SceneTimePicker,
  SceneTimeRange,
  VizPanel,
} from '@grafana/scenes';
import {DataSourceRef} from '@grafana/schema';
import {FieldConfigSource} from '@grafana/data';
import {scopedQueries, visualPanels} from './dashboard';
import {DashboardPanel, TimeGroup} from './types';

const DATASOURCE: DataSourceRef = {type: 'prometheus', uid: 'PBFA97CFB590B2093'};

export function buildGroupScene(panels: DashboardPanel[], group: TimeGroup): EmbeddedScene {
  const controls: SceneObject[] = [new SceneControlsSpacer()];
  if (group.mode === 'global') controls.push(new SceneTimePicker({isOnCanvas: true}));
  if (group.mode !== 'absolute') {
    controls.push(new SceneRefreshPicker({intervals: ['5s', '10s', '30s', '1m', '5m'], isOnCanvas: true}));
  }
  return new EmbeddedScene({
    $timeRange: new SceneTimeRange({from: group.from, to: group.to}),
    body: new SceneFlexLayout({
      direction: 'row',
      wrap: 'wrap',
      children: visualPanels(panels).map((panel) => buildPanel(panel, group)),
    }),
    controls,
  });
}

function buildPanel(panel: DashboardPanel, group: TimeGroup): SceneFlexItem {
  const runner = new SceneQueryRunner({datasource: DATASOURCE, queries: scopedQueries(panel, group.targetIds)});
  let data: SceneDataProvider = runner;
  if (panel.transformations?.length) {
    data = new SceneDataTransformer({
      $data: runner,
      transformations: panel.transformations,
    });
  }
  const visualization = new VizPanel({
    $data: data,
    pluginId: panel.type || 'timeseries',
    title: panel.title || 'SBK metric',
    description: panel.description,
    pluginVersion: panel.pluginVersion,
    displayMode: panel.transparent ? 'transparent' : 'default',
    options: panel.options || {},
    fieldConfig: (panel.fieldConfig || {defaults: {}, overrides: []}) as FieldConfigSource,
  });
  const width = `${Math.max(25, Math.min(100, ((panel.gridPos?.w || 12) / 24) * 100))}%`;
  const height = Math.max(180, Math.min(640, (panel.gridPos?.h || 8) * 30));
  return new SceneFlexItem({body: visualization, width, height});
}
