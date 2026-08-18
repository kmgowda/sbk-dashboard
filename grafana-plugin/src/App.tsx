/*
 * Copyright (c) KMG. All Rights Reserved.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 */

import React, {useEffect, useMemo, useRef, useState} from 'react';
import {AppRootProps} from '@grafana/data';
import {getBackendSrv} from '@grafana/runtime';
import {Alert, Button, Spinner} from '@grafana/ui';
import {SceneTimeRange} from '@grafana/scenes';
import {buildGroupScene} from './scene';
import {
  DEFAULT_RELATIVE_FROM,
  RELATIVE_RANGES,
  groupSelections,
  selectionsFromUrl,
  selectionsToUrl,
  validateTargets,
} from './timeRanges';
import {ComparisonDashboard, GrafanaDashboardResponse, TargetDescriptor, TargetTimeSelection} from './types';
import './styles.css';

const COMPARISON_UID = /^sbk-comparison-[a-f0-9]{16}$/;

function App(_props: AppRootProps) {
  const initialParams = useMemo(() => new URLSearchParams(window.location.search), []);
  const comparisonUid = initialParams.get('comparisonUid') || '';
  const globalWindow = useRef({
    from: initialParams.get('from') || 'now-5m',
    to: initialParams.get('to') || 'now',
  });
  const [dashboard, setDashboard] = useState<ComparisonDashboard | null>(null);
  const [targets, setTargets] = useState<TargetDescriptor[]>([]);
  const [selections, setSelections] = useState<Record<string, TargetTimeSelection>>({});
  const [error, setError] = useState('');

  useEffect(() => {
    if (!COMPARISON_UID.test(comparisonUid)) {
      setError('Open this view from the SBK Dashboard “Compare selected” action.');
      return;
    }
    getBackendSrv()
      .get<GrafanaDashboardResponse>(`/api/dashboards/uid/${encodeURIComponent(comparisonUid)}`)
      .then((response) => {
        const selected = validateTargets(
          response.dashboard.sbkDashboardComparisonTargets,
          response.dashboard.sbkDashboardComparisonEndpointIds,
          response.dashboard.sbkDashboardComparisonPolicy.minTargets,
          response.dashboard.sbkDashboardComparisonPolicy.maxTargets
        );
        setDashboard(response.dashboard);
        setTargets(selected);
        setSelections(selectionsFromUrl(
          selected,
          window.location.search,
          response.dashboard.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays,
          response.dashboard.sbkDashboardComparisonPolicy.maxTimeGroups
        ));
      })
      .catch(() => setError('The comparison descriptor is unavailable or no longer contains registered targets.'));
  }, [comparisonUid]);

  const groups = useMemo(
    () => groupSelections(
      targets,
      selections,
      dashboard?.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays || 1,
      globalWindow.current.from,
      globalWindow.current.to
    ),
    [dashboard, targets, selections]
  );
  const scenes = useMemo(
    () => dashboard ? groups.map((group) => ({group, scene: buildGroupScene(dashboard.panels, group)})) : [],
    [dashboard, groups]
  );

  useEffect(() => {
    const globalRange = scenes.find(({group}) => group.mode === 'global')?.scene.state.$timeRange;
    if (!(globalRange instanceof SceneTimeRange) || !dashboard) return undefined;
    const subscription = globalRange.subscribeToState((state) => {
      globalWindow.current = {from: state.from, to: state.to};
      window.history.replaceState(
        null,
        '',
        selectionsToUrl(
          comparisonUid,
          targets,
          selections,
          dashboard.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays,
          state.from,
          state.to
        )
      );
    });
    return () => subscription.unsubscribe();
  }, [comparisonUid, dashboard, scenes, selections, targets]);

  const updateSelection = (targetId: string, selection: TargetTimeSelection) => {
    const currentGlobal = scenes.find(({group}) => group.mode === 'global')?.scene.state.$timeRange;
    if (currentGlobal instanceof SceneTimeRange) {
      globalWindow.current = {from: currentGlobal.state.from, to: currentGlobal.state.to};
    }
    const candidate = {...selections, [targetId]: selection};
    const maxTimeGroups = dashboard?.sbkDashboardComparisonPolicy.maxTimeGroups || 1;
    if (
      groupSelections(
        targets,
        candidate,
        dashboard?.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays || 1,
        globalWindow.current.from,
        globalWindow.current.to
      ).length > maxTimeGroups
    ) {
      setError(`A comparison can use at most ${maxTimeGroups} distinct time ranges.`);
      return;
    }
    setError('');
    setSelections(candidate);
    window.history.replaceState(
      null,
      '',
      selectionsToUrl(
        comparisonUid,
        targets,
        candidate,
        dashboard?.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays || 1,
        globalWindow.current.from,
        globalWindow.current.to
      )
    );
  };

  if (error && !dashboard) return <Alert title="Unable to open comparison">{error}</Alert>;
  if (!dashboard) return <Spinner />;

  return (
    <main className="sbk-comparison">
      <header>
        <h1>SBK/SBM live comparison</h1>
        <p>
          All targets initially follow the global live range. Detach only targets that need an independent live or
          historical range. Targets with identical ranges continue to share one query group.
        </p>
      </header>
      {error && <Alert title="Time range not changed">{error}</Alert>}
      <section className="target-time-controls" aria-label="Target time ranges">
        {targets.map((target) => (
          <TargetTimeControl
            key={target.id}
            target={target}
            selection={selections[target.id] || {mode: 'global'}}
            maxAbsoluteRangeDays={dashboard.sbkDashboardComparisonPolicy.maxAbsoluteRangeDays}
            onChange={(selection) => updateSelection(target.id, selection)}
          />
        ))}
      </section>
      <div className="comparison-summary">
        {groups.length} active time {groups.length === 1 ? 'range' : 'ranges'} · {targets.length} targets
      </div>
      {scenes.map(({group, scene}) => (
        <section className="time-group" key={group.key}>
          <div className="time-group-heading">
            <h2>{group.label}</h2>
            <span>{group.targetIds.map((id) => targets.find((target) => target.id === id)?.name || id).join(', ')}</span>
          </div>
          <scene.Component model={scene} />
        </section>
      ))}
    </main>
  );
}

interface TargetTimeControlProps {
  target: TargetDescriptor;
  selection: TargetTimeSelection;
  maxAbsoluteRangeDays: number;
  onChange: (selection: TargetTimeSelection) => void;
}

function TargetTimeControl({target, selection, maxAbsoluteRangeDays, onChange}: TargetTimeControlProps) {
  const [absoluteFrom, setAbsoluteFrom] = useState(epochToInput(selection.absoluteFrom));
  const [absoluteTo, setAbsoluteTo] = useState(epochToInput(selection.absoluteTo));
  useEffect(() => {
    setAbsoluteFrom(epochToInput(selection.absoluteFrom));
    setAbsoluteTo(epochToInput(selection.absoluteTo));
  }, [selection.absoluteFrom, selection.absoluteTo]);
  const parsedFrom = inputToEpoch(absoluteFrom);
  const parsedTo = inputToEpoch(absoluteTo);
  const absoluteValid = parsedFrom > 0 && parsedTo > parsedFrom &&
    parsedTo - parsedFrom <= maxAbsoluteRangeDays * 24 * 60 * 60 * 1000;
  const modeChange = (mode: TargetTimeSelection['mode']) => {
    const now = Date.now();
    if (mode === 'relative') onChange({mode, relativeFrom: DEFAULT_RELATIVE_FROM});
    else if (mode === 'absolute') onChange({mode, absoluteFrom: now - 60 * 60 * 1000, absoluteTo: now});
    else onChange({mode: 'global'});
  };
  return (
    <article className="target-time-card">
      <div>
        <strong>{target.name}</strong>
        <small>{target.kind} · {target.address} · {target.id}</small>
      </div>
      <label>
        Time mode
        <select value={selection.mode} onChange={(event) => modeChange(event.target.value as TargetTimeSelection['mode'])}>
          <option value="global">Follow global live range</option>
          <option value="relative">Independent live range</option>
          <option value="absolute">Fixed historical range</option>
        </select>
      </label>
      {selection.mode === 'relative' && (
        <label>
          Live window
          <select
            value={selection.relativeFrom || DEFAULT_RELATIVE_FROM}
            onChange={(event) => onChange({mode: 'relative', relativeFrom: event.target.value})}
          >
            {RELATIVE_RANGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
      )}
      {selection.mode === 'absolute' && (
        <div className="absolute-fields">
          <label>
            From
            <input
              type="datetime-local"
              value={absoluteFrom}
              onChange={(event) => setAbsoluteFrom(event.target.value)}
            />
          </label>
          <label>
            To
            <input
              type="datetime-local"
              value={absoluteTo}
              onChange={(event) => setAbsoluteTo(event.target.value)}
            />
          </label>
          <Button
            size="sm"
            variant="secondary"
            disabled={!absoluteValid}
            onClick={() => onChange({mode: 'absolute', absoluteFrom: parsedFrom, absoluteTo: parsedTo})}
          >
            Apply fixed range
          </Button>
          <small>Maximum span: {maxAbsoluteRangeDays} days.</small>
        </div>
      )}
      {selection.mode !== 'global' && (
        <Button size="sm" variant="secondary" onClick={() => onChange({mode: 'global'})}>Follow global</Button>
      )}
    </article>
  );
}

function epochToInput(value: number | undefined): string {
  if (!value || !Number.isFinite(value)) return '';
  const date = new Date(value - new Date(value).getTimezoneOffset() * 60_000);
  return date.toISOString().slice(0, 16);
}

function inputToEpoch(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

export default App;
