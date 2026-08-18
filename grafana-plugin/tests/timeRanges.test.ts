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
  decodeSelection,
  encodeSelection,
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
  expect(groupSelections(targets, {}, maxAbsoluteRangeDays).map((group) => [group.key, group.targetIds])).toEqual([
    ['global', targets.map((target) => target.id)],
  ]);
});

test('only distinct time ranges create additional query groups', () => {
  const groups = groupSelections(targets, {
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
    'sbk-comparison-1234567890abcdef', targets, selections, maxAbsoluteRangeDays
  );
  expect(selectionsFromUrl(targets, url, maxAbsoluteRangeDays, 4)[targets[1].id])
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
  expect(selectionsFromUrl(targets, search, maxAbsoluteRangeDays, 1)).toEqual({
    [targets[0].id]: {mode: 'global'},
    [targets[1].id]: {mode: 'global'},
    [targets[2].id]: {mode: 'global'},
  });
});

test('descriptor validation rejects unknown endpoints', () => {
  expect(validateTargets(targets, [targets[1].id, targets[0].id], 2, 8).map((target) => target.id)).toEqual([
    targets[0].id,
    targets[1].id,
  ]);
  expect(() => validateTargets(targets, [targets[0].id, 'ffffffffffffffff'], 2, 8)).toThrow();
});
