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
  RELATIVE_RANGES,
  decodeSelection,
  encodeSelection,
  comparisonLanes,
  exceedsTimeGroupLimit,
  groupSelections,
  selectionsFromUrl,
  selectionsToUrl,
  validateTargets,
} from '../src/timeRanges';
import {TargetDescriptor} from '../src/types';

const targets: TargetDescriptor[] = [
  {id: '1111111111111111', name: 'Board A', kind: 'SBK', address: 'a:1/metrics'},
  {id: '2222222222222222', name: 'Board B', kind: 'SBM', address: 'b:2/metrics'},
  {id: '3333333333333333', name: 'Board C', kind: 'SBK', address: 'c:3/metrics'},
];
const maxAbsoluteRangeDays = 31;

test('all targets initially share one global live group', () => {
  const lanes = comparisonLanes(targets, '');
  expect(groupSelections(lanes, {}, maxAbsoluteRangeDays).map((group) => [group.key, group.targetIds])).toEqual([
    ['global', targets.map((target) => target.id)],
  ]);
});

test('only distinct time ranges create additional query groups', () => {
  const lanes = comparisonLanes(targets, '');
  const groups = groupSelections(lanes, {
    [targets[1].id]: {mode: 'relative', relativeFrom: 'now-15m'},
    [targets[2].id]: {mode: 'relative', relativeFrom: 'now-15m'},
  }, maxAbsoluteRangeDays);
  expect(groups).toHaveLength(2);
  expect(groups[1].targetIds).toEqual([targets[1].id, targets[2].id]);
});

test('absolute selections round trip through shareable URL state', () => {
  const selections = {
    [targets[1].id]: {mode: 'absolute' as const, absoluteFrom: 1000, absoluteTo: 2000},
  };
  const url = selectionsToUrl(
    'sbk-comparison-1234567890abcdef', comparisonLanes(targets, ''), selections, maxAbsoluteRangeDays
  );
  expect(selectionsFromUrl(comparisonLanes(targets, ''), url, maxAbsoluteRangeDays, 4)[targets[1].id])
    .toEqual(selections[targets[1].id]);
  expect(encodeSelection(decodeSelection('a:1000:2000', maxAbsoluteRangeDays), maxAbsoluteRangeDays))
    .toBe('a:1000:2000');
});

test('malformed time state safely follows global range', () => {
  expect(decodeSelection('a:2000:1000', maxAbsoluteRangeDays)).toEqual({mode: 'global'});
  expect(decodeSelection('r:forever', maxAbsoluteRangeDays)).toEqual({mode: 'global'});
  expect(decodeSelection(`a:1:${32 * 24 * 60 * 60 * 1000}`, maxAbsoluteRangeDays)).toEqual({mode: 'global'});
});

test('URL state cannot exceed the time-group policy', () => {
  const search = `?tr-${targets[0].id}=r:now-5m&tr-${targets[1].id}=r:now-15m`;
  expect(selectionsFromUrl(comparisonLanes(targets, ''), search, maxAbsoluteRangeDays, 1)).toEqual({
    [targets[0].id]: {mode: 'global'},
    [targets[1].id]: {mode: 'global'},
    [targets[2].id]: {mode: 'global'},
  });
});

test('one target creates two deterministic independently selectable time lanes', () => {
  const lanes = comparisonLanes([targets[0]], '');
  expect(lanes.map((lane) => [lane.id, lane.label])).toEqual([
    [`${targets[0].id}-range-1`, 'Range 1'],
    [`${targets[0].id}-range-2`, 'Range 2'],
  ]);
  const selections = {[lanes[1].id]: {mode: 'relative' as const, relativeFrom: 'now-15m'}};
  const groups = groupSelections(lanes, selections, maxAbsoluteRangeDays);
  expect(groups).toHaveLength(2);
  expect(groups.every((group) => group.targetIds[0] === targets[0].id)).toBe(true);
  expect(groups.flatMap((group) => group.laneIds).sort()).toEqual(lanes.map((lane) => lane.id).sort());
});

test('single-target lane count and selections round trip through bounded URL state', () => {
  const lanes = comparisonLanes([targets[0]], '?lanes=3', 2, 8);
  const selections = {[lanes[2].id]: {mode: 'relative' as const, relativeFrom: 'now-6h'}};
  const url = selectionsToUrl('sbk-comparison-1234567890abcdef', lanes, selections, maxAbsoluteRangeDays);
  expect(new URLSearchParams(url).get('lanes')).toBe('3');
  const restoredLanes = comparisonLanes([targets[0]], url, 2, 8);
  expect(restoredLanes).toHaveLength(3);
  expect(selectionsFromUrl(restoredLanes, url, maxAbsoluteRangeDays, 4)[lanes[2].id])
    .toEqual(selections[lanes[2].id]);
  expect(comparisonLanes([targets[0]], '?lanes=99', 2, 8)).toHaveLength(2);
});

test('adding a global lane cannot exceed the distinct time-group policy', () => {
  const lanes = comparisonLanes([targets[0]], '?lanes=4', 2, 8);
  const selections = Object.fromEntries(lanes.map((lane, index) => [
    lane.id,
    {mode: 'relative' as const, relativeFrom: RELATIVE_RANGES[index][0]},
  ]));
  const candidateLanes = comparisonLanes([targets[0]], '?lanes=5', 2, 8);
  const candidateSelections = Object.fromEntries(candidateLanes.map((lane) => [
    lane.id,
    selections[lane.id] || {mode: 'global'},
  ]));
  expect(exceedsTimeGroupLimit(candidateLanes, candidateSelections, maxAbsoluteRangeDays, 4)).toBe(true);
  expect(exceedsTimeGroupLimit(candidateLanes, candidateSelections, maxAbsoluteRangeDays, 5)).toBe(false);
});

test('descriptor validation rejects unknown endpoints', () => {
  expect(validateTargets(targets, [targets[1].id, targets[0].id], 2, 8).map((target) => target.id)).toEqual([
    targets[0].id,
    targets[1].id,
  ]);
  expect(() => validateTargets(targets, [targets[0].id, 'ffffffffffffffff'], 2, 8)).toThrow();
});
