/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import type {DataTransformerConfig} from '@grafana/data';

export type TimeMode = 'global' | 'relative' | 'absolute';

export interface TargetDescriptor {id: string; name: string; kind: string; address: string;}
export interface ComparisonLane {id: string; label: string; target: TargetDescriptor;}
export interface TargetTimeSelection {
  mode: TimeMode;
  relativeFrom?: string;
  absoluteFrom?: number;
  absoluteTo?: number;
}
export interface TimeGroup {
  key: string;
  label: string;
  mode: TimeMode;
  from: string;
  to: string;
  targetIds: string[];
  laneIds: string[];
}
export interface DashboardPanel {
  type?: string;
  title?: string;
  collapsed?: boolean;
  description?: string;
  pluginVersion?: string;
  transparent?: boolean;
  options?: Record<string, unknown>;
  fieldConfig?: {defaults: Record<string, unknown>; overrides: unknown[]};
  transformations?: DataTransformerConfig[];
  targets?: Array<Record<string, unknown>>;
  gridPos?: {h?: number; w?: number; x?: number; y?: number};
  panels?: DashboardPanel[];
}
export interface DashboardPanelItem {kind: 'panel'; panel: DashboardPanel;}
export interface DashboardRowItem {
  kind: 'row';
  title: string;
  collapsed: boolean;
  gridPos: {h?: number; w?: number; x?: number; y?: number};
  panels: DashboardPanel[];
}
export type DashboardLayoutItem = DashboardPanelItem | DashboardRowItem;
export interface ComparisonDashboard {
  uid: string;
  title?: string;
  panels: DashboardPanel[];
  sbkDashboardComparisonSchemaVersion: number;
  sbkDashboardComparisonEndpointIds: string[];
  sbkDashboardComparisonTargets: TargetDescriptor[];
  sbkDashboardComparisonPolicy: ComparisonPolicy;
}
export interface ComparisonPolicy {
  minTargets: number;
  maxTargets: number;
  minSingleTargetTimeLanes?: number;
  maxTimeLanes?: number;
  maxTimeGroups: number;
  maxAbsoluteRangeDays: number;
}
export interface GrafanaDashboardResponse {dashboard: ComparisonDashboard;}
