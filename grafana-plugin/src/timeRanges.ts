/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {ComparisonLane, TargetDescriptor, TargetTimeSelection, TimeGroup} from './types';

export const DEFAULT_RELATIVE_FROM = 'now-1h';
export const MILLISECONDS_PER_MINUTE = 60 * 1000;
export const MILLISECONDS_PER_HOUR = 60 * MILLISECONDS_PER_MINUTE;
export const MILLISECONDS_PER_DAY = 24 * MILLISECONDS_PER_HOUR;
export const RELATIVE_RANGES = Object.freeze([
  ['now-5m', 'Last 5 minutes'],
  ['now-15m', 'Last 15 minutes'],
  ['now-1h', 'Last 1 hour'],
  ['now-6h', 'Last 6 hours'],
  ['now-24h', 'Last 24 hours'],
  ['now-7d', 'Last 7 days'],
] as const);
const RELATIVE_VALUES: ReadonlySet<string> = new Set(RELATIVE_RANGES.map(([value]) => value));
const TARGET_ID = /^[a-f0-9]{16}$/;
const DEFAULT_SINGLE_TARGET_LANES = 2;

export function comparisonLanes(
  targets: TargetDescriptor[], search: string, minimum = DEFAULT_SINGLE_TARGET_LANES, maximum = 8
): ComparisonLane[] {
  if (targets.length !== 1) {
    return targets.map((target) => ({id: target.id, label: target.name, target}));
  }
  const requested = Number(new URLSearchParams(search).get('lanes'));
  const count = Number.isSafeInteger(requested) && requested >= minimum && requested <= maximum
    ? requested
    : minimum;
  return Array.from({length: count}, (_, index) => ({
    id: `${targets[0].id}-range-${index + 1}`,
    label: `Range ${index + 1}`,
    target: targets[0],
  }));
}

export function defaultSelection(): TargetTimeSelection {
  return {mode: 'global'};
}

export function selectionKey(selection: TargetTimeSelection, maxAbsoluteRangeDays: number): string {
  if (selection.mode === 'global') return 'global';
  if (selection.mode === 'relative') {
    const from = selection.relativeFrom && RELATIVE_VALUES.has(selection.relativeFrom)
      ? selection.relativeFrom
      : DEFAULT_RELATIVE_FROM;
    return `relative|${from}|now`;
  }
  const from = Number(selection.absoluteFrom);
  const to = Number(selection.absoluteTo);
  const maximumSpan = maxAbsoluteRangeDays * MILLISECONDS_PER_DAY;
  if (
    !Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from >= to ||
    !Number.isSafeInteger(maximumSpan) || maximumSpan <= 0 || to - from > maximumSpan
  ) return 'global';
  return `absolute|${from}|${to}`;
}

export function groupSelections(
  lanes: ComparisonLane[],
  selections: Record<string, TargetTimeSelection>,
  maxAbsoluteRangeDays: number,
  globalFrom = 'now-5m',
  globalTo = 'now'
): TimeGroup[] {
  const groups = new Map<string, TimeGroup>();
  for (const lane of lanes) {
    const key = selectionKey(selections[lane.id] || defaultSelection(), maxAbsoluteRangeDays);
    const [mode, fromValue, toValue] = key.split('|');
    let group = groups.get(key);
    if (!group) {
      const absolute = mode === 'absolute';
      group = {
        key,
        label: mode === 'global'
          ? 'Global live range'
          : mode === 'relative'
            ? `Independent live · ${relativeLabel(fromValue)}`
            : `${formatEpoch(Number(fromValue))} – ${formatEpoch(Number(toValue))}`,
        mode: mode as TimeGroup['mode'],
        from: mode === 'global'
          ? globalFrom
          : absolute ? new Date(Number(fromValue)).toISOString() : fromValue,
        to: mode === 'global'
          ? globalTo
          : absolute ? new Date(Number(toValue)).toISOString() : toValue,
        targetIds: [],
        laneIds: [],
      };
      groups.set(key, group);
    }
    if (!group.targetIds.includes(lane.target.id)) group.targetIds.push(lane.target.id);
    group.laneIds.push(lane.id);
  }
  return [...groups.values()].sort((left, right) => {
    if (left.mode === 'global') return -1;
    if (right.mode === 'global') return 1;
    return left.key.localeCompare(right.key);
  });
}

export function exceedsTimeGroupLimit(
  lanes: ComparisonLane[],
  selections: Record<string, TargetTimeSelection>,
  maxAbsoluteRangeDays: number,
  maxTimeGroups: number,
  globalFrom = 'now-5m',
  globalTo = 'now'
): boolean {
  return groupSelections(
    lanes,
    selections,
    maxAbsoluteRangeDays,
    globalFrom,
    globalTo
  ).length > maxTimeGroups;
}

export function encodeSelection(selection: TargetTimeSelection, maxAbsoluteRangeDays: number): string | null {
  const key = selectionKey(selection, maxAbsoluteRangeDays);
  if (key === 'global') return null;
  const [mode, from, to] = key.split('|');
  return mode === 'relative' ? `r:${from}` : `a:${from}:${to}`;
}

export function decodeSelection(value: string | null, maxAbsoluteRangeDays: number): TargetTimeSelection {
  if (!value) return defaultSelection();
  const parts = value.split(':');
  if (parts.length === 2 && parts[0] === 'r' && RELATIVE_VALUES.has(parts[1])) {
    return {mode: 'relative', relativeFrom: parts[1]};
  }
  if (parts.length === 3 && parts[0] === 'a') {
    const absoluteFrom = Number(parts[1]);
    const absoluteTo = Number(parts[2]);
    const selection = {mode: 'absolute' as const, absoluteFrom, absoluteTo};
    if (selectionKey(selection, maxAbsoluteRangeDays) !== 'global') return selection;
  }
  return defaultSelection();
}

export function selectionsFromUrl(
  lanes: ComparisonLane[], search: string, maxAbsoluteRangeDays: number, maxTimeGroups: number
): Record<string, TargetTimeSelection> {
  const params = new URLSearchParams(search);
  const selections = Object.fromEntries(lanes.map((lane) => [
    lane.id,
    decodeSelection(params.get(`tr-${lane.id}`), maxAbsoluteRangeDays),
  ]));
  if (exceedsTimeGroupLimit(lanes, selections, maxAbsoluteRangeDays, maxTimeGroups)) {
    return Object.fromEntries(lanes.map((lane) => [lane.id, defaultSelection()]));
  }
  return selections;
}

export function selectionsToUrl(
  comparisonUid: string,
  lanes: ComparisonLane[],
  selections: Record<string, TargetTimeSelection>,
  maxAbsoluteRangeDays: number,
  globalFrom = 'now-5m',
  globalTo = 'now'
): string {
  const params = new URLSearchParams({comparisonUid, from: globalFrom, to: globalTo});
  if (lanes.length >= DEFAULT_SINGLE_TARGET_LANES && new Set(lanes.map((lane) => lane.target.id)).size === 1) {
    params.set('lanes', String(lanes.length));
  }
  for (const lane of lanes) {
    const encoded = encodeSelection(selections[lane.id] || defaultSelection(), maxAbsoluteRangeDays);
    if (encoded) params.set(`tr-${lane.id}`, encoded);
  }
  return `?${params.toString()}`;
}

export function validateTargets(
  targets: TargetDescriptor[], endpointIds: string[], minTargets: number, maxTargets: number
): TargetDescriptor[] {
  if (!Array.isArray(targets) || !Array.isArray(endpointIds)) throw new Error('Comparison descriptor is incomplete');
  const expected = [...new Set(endpointIds)].sort();
  const byId = new Map(targets.map((target) => [target.id, target]));
  if (
    expected.length < minTargets || expected.length > maxTargets ||
    expected.some((id) => !TARGET_ID.test(id) || !byId.has(id))
  ) {
    throw new Error('Comparison descriptor contains invalid endpoints');
  }
  return expected.map((id) => byId.get(id)!);
}

function relativeLabel(value: string): string {
  return RELATIVE_RANGES.find(([candidate]) => candidate === value)?.[1] || value;
}

function formatEpoch(value: number): string {
  return new Date(value).toLocaleString();
}
