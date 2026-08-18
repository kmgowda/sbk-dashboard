/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import {TargetDescriptor, TargetTimeSelection, TimeGroup} from './types';

export const DEFAULT_RELATIVE_FROM = 'now-1h';
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
  const maximumSpan = maxAbsoluteRangeDays * 24 * 60 * 60 * 1000;
  if (
    !Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from >= to ||
    !Number.isSafeInteger(maximumSpan) || maximumSpan <= 0 || to - from > maximumSpan
  ) return 'global';
  return `absolute|${from}|${to}`;
}

export function groupSelections(
  targets: TargetDescriptor[],
  selections: Record<string, TargetTimeSelection>,
  maxAbsoluteRangeDays: number,
  globalFrom = 'now-5m',
  globalTo = 'now'
): TimeGroup[] {
  const groups = new Map<string, TimeGroup>();
  for (const target of targets) {
    const key = selectionKey(selections[target.id] || defaultSelection(), maxAbsoluteRangeDays);
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
      };
      groups.set(key, group);
    }
    group.targetIds.push(target.id);
  }
  return [...groups.values()].sort((left, right) => {
    if (left.mode === 'global') return -1;
    if (right.mode === 'global') return 1;
    return left.key.localeCompare(right.key);
  });
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
  targets: TargetDescriptor[], search: string, maxAbsoluteRangeDays: number, maxTimeGroups: number
): Record<string, TargetTimeSelection> {
  const params = new URLSearchParams(search);
  const selections = Object.fromEntries(targets.map((target) => [
    target.id,
    decodeSelection(params.get(`tr-${target.id}`), maxAbsoluteRangeDays),
  ]));
  if (groupSelections(targets, selections, maxAbsoluteRangeDays).length > maxTimeGroups) {
    return Object.fromEntries(targets.map((target) => [target.id, defaultSelection()]));
  }
  return selections;
}

export function selectionsToUrl(
  comparisonUid: string,
  targets: TargetDescriptor[],
  selections: Record<string, TargetTimeSelection>,
  maxAbsoluteRangeDays: number,
  globalFrom = 'now-5m',
  globalTo = 'now'
): string {
  const params = new URLSearchParams({comparisonUid, from: globalFrom, to: globalTo});
  for (const target of targets) {
    const encoded = encodeSelection(selections[target.id] || defaultSelection(), maxAbsoluteRangeDays);
    if (encoded) params.set(`tr-${target.id}`, encoded);
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
