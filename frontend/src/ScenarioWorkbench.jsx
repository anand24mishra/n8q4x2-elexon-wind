import React, { useState, useEffect, useMemo } from 'react';
import { fmt, dayFormat, timeFormat, Icon, lineSections, Tag } from './App';

const scenarioCache = new Map();

function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function buildExportDocument(scenarioData, selectedStrategy) {
  if (!scenarioData?.report) return null;
  const detail = selectedStrategy === 'all' ? null : scenarioData.exAnte?.strategies?.[selectedStrategy] || null;
  const report = {
    ...scenarioData.report,
    selectedStrategy,
    exportedAt: new Date().toISOString(),
    scenarioDefinition: {
      ...scenarioData.report.scenarioDefinition,
      selectedStrategyDetail: detail,
    },
  };
  const selectionLine = `Selected view at export: ${selectedStrategy}. Cutoff ${scenarioData.identity.cutoff} UK on ${scenarioData.identity.decisionDate}.`;
  report.markdown = `${selectionLine}\n\n${scenarioData.report.markdown}`;
  return report;
}

export function exportScenarioReport(scenarioData, selectedStrategy) {
  const report = buildExportDocument(scenarioData, selectedStrategy);
  if (!report) return;
  const filename = `windward-decision-scenario-${scenarioData.identity.decisionDate}-${scenarioData.identity.cutoff.replace(':', '')}.json`;
  downloadBlob(filename, new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
}

export function exportScenarioMarkdown(scenarioData, selectedStrategy) {
  const report = buildExportDocument(scenarioData, selectedStrategy);
  if (!report) return;
  const filename = `windward-decision-scenario-${scenarioData.identity.decisionDate}-${scenarioData.identity.cutoff.replace(':', '')}.md`;
  downloadBlob(filename, new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' }));
}

export function ScenarioChart({ periods, selectedStrategy = 'all', title = 'Forecast scenarios vs outturn' }) {
  const [active, setActive] = useState(null);
  const rows = periods || [];
  if (!rows.length) return <p className="bt-empty">No period data available for this decision day.</p>;

  const values = rows.flatMap(r => [
    r.actual,
    r.rawForecast,
    (selectedStrategy === 'all' || selectedStrategy === 'expanding') ? r.expandingForecast : null,
    (selectedStrategy === 'all' || selectedStrategy === 'rolling7Day') ? r.rollingForecast : null,
  ].filter(v => v != null));

  const max = Math.max(100, ...values) * 1.14;
  const W = 950, H = 290, left = 58, right = 20, top = 20, bottom = 38;
  const x = i => left + (i / Math.max(rows.length - 1, 1)) * (W - left - right);
  const y = v => top + (1 - v / max) * (H - top - bottom);
  const validIndex = active != null && rows[active] ? active : null;
  const precision = Math.max(1, Math.floor(rows.length / 8));

  const showRaw = selectedStrategy === 'all' || selectedStrategy === 'original';
  const showExp = selectedStrategy === 'all' || selectedStrategy === 'expanding';
  const showRoll = selectedStrategy === 'all' || selectedStrategy === 'rolling7Day';

  return (
    <div className="chart-wrap">
      <svg className="line-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseLeave={() => setActive(null)}>
        {Array.from({ length: 5 }, (_, i) => (
          <g key={i}>
            <line x1={left} x2={W - right} y1={y(max * i / 4)} y2={y(max * i / 4)} stroke="#e6edf0" strokeDasharray="4 5" />
            <text x={left - 12} y={y(max * i / 4) + 4} textAnchor="end" className="axis">
              {fmt(max * i / 4 / 1000, 1)}k
            </text>
          </g>
        ))}
        {rows.map((row, i) => (i % precision === 0 || i === rows.length - 1) ? (
          <text key={i} x={x(i)} y={H - 12} textAnchor="middle" className="axis">
            {new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(row.startTime))}
          </text>
        ) : null)}

        {/* Actual generation (half-hourly FUELHH) */}
        {lineSections(rows, 'actual', x, y, 1).map((points, i) => (
          <polyline key={'act_' + i} fill="none" stroke="#1bb7a4" strokeWidth="2.8" strokeLinejoin="round" strokeLinecap="round" points={points.join(' ')} />
        ))}

        {/* Original WINDFOR (hourly) */}
        {showRaw && lineSections(rows, 'rawForecast', x, y, 2).map((points, i) => (
          <polyline key={'raw_' + i} fill="none" stroke="#f0a357" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" strokeDasharray="5 4" points={points.join(' ')} />
        ))}
        {showRaw && rows.map((row, i) => row.rawForecast != null ? (
          <circle key={'raw_m_' + i} cx={x(i)} cy={y(row.rawForecast)} r="3.5" fill="#f0a357" stroke="#ffffff" strokeWidth="1.5" />
        ) : null)}

        {/* Expanding Mean Adjusted Forecast */}
        {showExp && lineSections(rows, 'expandingForecast', x, y, 2).map((points, i) => (
          <polyline key={'exp_' + i} fill="none" stroke="#2b82ba" strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" points={points.join(' ')} />
        ))}
        {showExp && rows.map((row, i) => row.expandingForecast != null ? (
          <rect key={'exp_m_' + i} x={x(i) - 3} y={y(row.expandingForecast) - 3} width="6" height="6" fill="#2b82ba" stroke="#ffffff" strokeWidth="1" />
        ) : null)}

        {/* Rolling 7-Day Adjusted Forecast */}
        {showRoll && lineSections(rows, 'rollingForecast', x, y, 2).map((points, i) => (
          <polyline key={'roll_' + i} fill="none" stroke="#8b5cf6" strokeWidth="2.2" strokeLinejoin="round" strokeLinecap="round" strokeDasharray="3 3" points={points.join(' ')} />
        ))}
        {showRoll && rows.map((row, i) => row.rollingForecast != null ? (
          <circle key={'roll_m_' + i} cx={x(i)} cy={y(row.rollingForecast)} r="3" fill="#8b5cf6" stroke="#ffffff" strokeWidth="1" />
        ) : null)}

        {/* Interactive hit areas */}
        {rows.map((row, i) => (
          <rect
            key={'hit_' + i}
            x={x(i) - (W - left - right) / Math.max(rows.length - 1, 1) / 2}
            y={top}
            width={(W - left - right) / Math.max(rows.length - 1, 1)}
            height={H - top - bottom}
            fill="transparent"
            onMouseEnter={() => setActive(i)}
          >
            <title>{`${timeFormat(row.startTime)} delivery. Raw forecast ${row.rawForecast != null ? `${fmt(row.rawForecast)} MW` : 'missing (hourly WINDFOR; this half-hour is not observed)'}. Actual ${row.actual != null ? `${fmt(row.actual)} MW` : 'missing'}. Published ${row.publishTime ? timeFormat(row.publishTime) : 'not published'}.`}</title>
          </rect>
        ))}

        {/* Hover crosshair and dots */}
        {validIndex != null && (
          <g>
            <line x1={x(validIndex)} x2={x(validIndex)} y1={top} y2={H - bottom} stroke="#8da5af" strokeDasharray="3 4" />
            {rows[validIndex].actual != null && (
              <circle cx={x(validIndex)} cy={y(rows[validIndex].actual)} r="5" fill="#1bb7a4" stroke="white" strokeWidth="2" />
            )}
            {showRaw && rows[validIndex].rawForecast != null && (
              <circle cx={x(validIndex)} cy={y(rows[validIndex].rawForecast)} r="5" fill="#f0a357" stroke="white" strokeWidth="2" />
            )}
            {showExp && rows[validIndex].expandingForecast != null && (
              <circle cx={x(validIndex)} cy={y(rows[validIndex].expandingForecast)} r="5" fill="#2b82ba" stroke="white" strokeWidth="2" />
            )}
            {showRoll && rows[validIndex].rollingForecast != null && (
              <circle cx={x(validIndex)} cy={y(rows[validIndex].rollingForecast)} r="5" fill="#8b5cf6" stroke="white" strokeWidth="2" />
            )}
          </g>
        )}
      </svg>

      {validIndex != null && (
        <div className="chart-tooltip" style={{ minWidth: 240 }}>
          <strong>Delivery {timeFormat(rows[validIndex].startTime)} (period {rows[validIndex].period})</strong>
          <span>Forecast published <b>{rows[validIndex].publishTime ? timeFormat(rows[validIndex].publishTime) : 'Not published'}</b></span>
          <span>Actual generation <b>{rows[validIndex].actual != null ? `${fmt(rows[validIndex].actual)} MW` : 'Missing'}</b></span>
          {showRaw && (
            <span>Raw WINDFOR <b>{rows[validIndex].rawForecast != null ? `${fmt(rows[validIndex].rawForecast)} MW` : 'Missing half-hour'}</b></span>
          )}
          {showExp && (
            <span>Expanding adjusted <b>{rows[validIndex].expandingForecast != null ? `${fmt(rows[validIndex].expandingForecast)} MW` : 'Unavailable'}</b></span>
          )}
          {showRoll && (
            <span>Rolling 7-day adjusted <b>{rows[validIndex].rollingForecast != null ? `${fmt(rows[validIndex].rollingForecast)} MW` : 'Unavailable'}</b></span>
          )}
          <span>Missing-data status <b>{rows[validIndex].rawForecast == null ? 'No hourly WINDFOR at this half-hour' : rows[validIndex].actual == null ? 'Actual not in the matched set' : 'Forecast and actual both present'}</b></span>
          {rows[validIndex].actual != null && rows[validIndex].rawForecast != null && (
            <span style={{ borderTop: '1px solid rgba(255,255,255,.15)', paddingTop: 4, marginTop: 2 }}>
              Retrospective raw error <b className={rows[validIndex].rawError > 0 ? 'pos-error' : 'neg-error'}>{rows[validIndex].rawError > 0 ? '+' : ''}{fmt(rows[validIndex].rawError)} MW</b>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export default function ScenarioWorkbench({
  cutoff,
  selectedDay,
  onSelectDay,
  onCutoffChange,
  availableDays = [],
  revision = 0,
  strategy: strategyProp,
  onStrategyChange,
}) {
  const [localStrategy, setLocalStrategy] = useState('all');
  const strategy = strategyProp ?? localStrategy;
  const setStrategy = onStrategyChange ?? setLocalStrategy;
  const [data, setData] = useState(null);
  const [dataKey, setDataKey] = useState(null);
  const [error, setError] = useState(null);
  const [errorKey, setErrorKey] = useState(null);
  const requestKey = `${selectedDay}|${cutoff}|${revision}`;

  useEffect(() => {
    const cached = scenarioCache.get(requestKey);
    if (cached) {
      setData(cached);
      setDataKey(requestKey);
      setError(null);
      return undefined;
    }
    const controller = new AbortController();
    let active = true;
    setError(null);
    fetch(`/api/scenarios?date=${encodeURIComponent(selectedDay)}&cutoff=${encodeURIComponent(cutoff)}`, {
      signal: controller.signal,
    })
      .then(async res => {
        const json = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = json.detail;
          throw new Error(typeof detail === 'string' ? detail : 'Failed to load scenario data');
        }
        return json;
      })
      .then(result => {
        if (!active) return;
        scenarioCache.set(requestKey, result);
        setData(result);
        setDataKey(requestKey);
        setError(null);
      })
      .catch(err => {
        if (!active || err.name === 'AbortError') return;
        setError(err.message);
        setErrorKey(requestKey);
      })
    return () => {
      active = false;
      controller.abort();
    };
  }, [requestKey, selectedDay, cutoff]);

  const resolved = useMemo(() => {
    if (dataKey === requestKey && data) return data;
    return scenarioCache.get(requestKey) || null;
  }, [data, dataKey, requestKey]);
  const activeError = errorKey === requestKey ? error : null;

  const exAnte = resolved?.exAnte;
  const retro = resolved?.retrospective;
  const strategies = exAnte?.strategies || {};
  const periods = resolved?.periods || [];
  const dayComparison = retro?.dayComparison;
  const monthSummary = resolved?.monthSummary;

  const monthReading = useMemo(() => {
    if (!monthSummary?.commonEvaluationObservations) {
      return 'There is no common evaluation set yet. A correction is left out of the month comparison until it has at least 72 causally eligible training pairs, so the three methods are not scored on different observations.';
    }
    const ranked = [
      ['the original forecast', monthSummary.commonOriginalMetrics?.maeMw],
      ['expanding mean correction', monthSummary.commonExpandingMetrics?.maeMw],
      ['rolling 7-day correction', monthSummary.commonRollingMetrics?.maeMw],
    ].filter((row) => row[1] != null).sort((a, b) => a[1] - b[1]);
    const best = ranked[0];
    return `Retrospective only, on ${monthSummary.eligibleDaysBoth} decision days and ${monthSummary.commonEvaluationObservations} identical matched observations: lowest MAE was ${best ? best[0] : 'unavailable'}${best ? ` at ${fmt(best[1])} MW` : ''}. Expanding correction improved ${monthSummary.expandingDaysImproved} of those days and worsened ${monthSummary.expandingDaysWorsened}. Rolling 7-day correction improved ${monthSummary.rollingDaysImproved} and worsened ${monthSummary.rollingDaysWorsened}. Those outcomes were not known at decision time.`;
  }, [monthSummary]);

  return (
    <div className="wb-shell">
      {/* ── Scenario Controls Bar ── */}
      <div className="wb-controls-bar">
        <div className="wb-controls-left">
          <div className="wb-control-item">
            <span className="filter-label">DECISION DATE</span>
            <select
              value={selectedDay}
              onChange={e => onSelectDay(e.target.value)}
              aria-label="Select decision date"
              className="day-select"
            >
              {availableDays.length > 0
                ? availableDays.map(d => (
                    <option key={d.decisionDate} value={d.decisionDate}>
                      {dayFormat(d.decisionDate, { year: 'numeric' })} decision
                    </option>
                  ))
                : Array.from({ length: 31 }, (_, i) => {
                    const day = `2026-01-${String(i + 1).padStart(2, '0')}`;
                    return (
                      <option key={day} value={day}>
                        {dayFormat(day, { year: 'numeric' })} decision
                      </option>
                    );
                  })}
            </select>
          </div>

          <div className="wb-control-item">
            <span className="filter-label">PURCHASE CUTOFF</span>
            <select
              value={cutoff}
              onChange={e => onCutoffChange(e.target.value)}
              aria-label="Select UK purchase cutoff"
              className="day-select"
            >
              <option value="08:30">08:30</option>
              <option value="09:20">09:20</option>
              <option value="09:50">09:50 (N2EX example)</option>
              <option value="10:30">10:30</option>
              <option value="12:30">12:30</option>
              <option value="15:30">15:30</option>
            </select>
          </div>

          <div className="wb-control-item">
            <span className="filter-label">STRATEGY FILTER</span>
            <div className="wb-pill-group" role="group" aria-label="Strategy selection">
              <button
                type="button"
                className={'wb-pill ' + (strategy === 'all' ? 'active' : '')}
                onClick={() => setStrategy('all')}
              >
                All strategies
              </button>
              <button
                type="button"
                className={'wb-pill ' + (strategy === 'original' ? 'active' : '')}
                onClick={() => setStrategy('original')}
              >
                Original (Raw)
              </button>
              <button
                type="button"
                className={'wb-pill ' + (strategy === 'expanding' ? 'active' : '')}
                onClick={() => setStrategy('expanding')}
              >
                Expanding Mean
              </button>
              <button
                type="button"
                className={'wb-pill ' + (strategy === 'rolling7Day' ? 'active' : '')}
                onClick={() => setStrategy('rolling7Day')}
              >
                Rolling 7-Day
              </button>
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            type="button"
            className="wb-report-btn"
            onClick={() => exportScenarioReport(resolved, strategy)}
            disabled={!resolved}
            title="Download the JSON analysis report for this decision date, cutoff, and selected strategy"
          >
            <Icon name="download" size={16} /> Export JSON report
          </button>
          <button
            type="button"
            className="outline-btn"
            onClick={() => exportScenarioMarkdown(resolved, strategy)}
            disabled={!resolved}
          >
            <Icon name="file" size={16} /> Markdown
          </button>
        </div>
      </div>

      {!resolved && !activeError && (
        <div className="bt-loading">
          <Icon name="refresh" size={18} /> Reconstructing decision scenarios and causal history…
        </div>
      )}

      {activeError && (
        <div className="notice error">
          <Icon name="info" /> <span>{activeError}</span>
        </div>
      )}

      {resolved && (
        <>
          {/* ── SECTION A: EX-ANTE PANEL (KNOWN BEFORE COMMITMENT) ── */}
          <section className="wb-panel">
            <div className="wb-panel-header">
              <div>
                <span className="wb-section-badge wb-badge-exante">
                  <Icon name="clock" size={12} /> Known at Decision Time
                </span>
                <h2>Ex-Ante Information &amp; Bias Adjustments</h2>
                <p>
                  Reconstructed state visible to a buyer at {cutoff} UK time on {dayFormat(exAnte.decisionDate, { year: 'numeric' })} for power delivery on {dayFormat(exAnte.deliveryDate, { year: 'numeric' })}.
                </p>
              </div>
                <div className="daily-stat" style={{ marginTop: 8 }}>
                  <span>Latest WINDFOR publication (UK)</span>
                  <strong>{timeFormat(exAnte.latestForecastPublishTime)}</strong>
                </div>
                <div className="risk-pills" style={{ marginTop: 12 }}>
                <div className="risk-pill risk-neutral">
                  <span className="risk-label">Mean Raw Forecast</span>
                  <strong className="risk-value">
                    {exAnte.rawForecastSummary?.meanMw != null ? `${fmt(exAnte.rawForecastSummary.meanMw)} MW` : '—'}
                  </strong>
                </div>
                <div className="risk-pill risk-neutral">
                  <span className="risk-label">Hourly Availability</span>
                  <strong className="risk-value">
                    {exAnte.rawForecastSummary?.periodsWithForecast} / {exAnte.rawForecastSummary?.totalPeriods} ({fmt(exAnte.rawForecastSummary?.coveragePct, 1)}%)
                  </strong>
                </div>
                <div className="risk-pill risk-ok">
                  <span className="risk-label">Data Freshness</span>
                  <strong className="risk-value">
                    {exAnte.dataFreshnessHours != null ? `${fmt(exAnte.dataFreshnessHours, 1)}h before cutoff` : '—'}
                  </strong>
                </div>
              </div>
            </div>

            {/* Strategy Cards */}
            <div className="wb-strategy-cards">
              {/* 1. Original WINDFOR */}
              <div
                className={'wb-strat-card ' + (strategy === 'original' || strategy === 'all' ? 'selected' : '')}
                onClick={() => setStrategy('original')}
                style={{ cursor: 'pointer' }}
              >
                <div className="wb-strat-top">
                  <span className="wb-strat-name">Original WINDFOR</span>
                  <span className="wb-status-tag ready">Baseline Active</span>
                </div>
                <div className="wb-strat-bias">
                  {strategies.original?.deliveryDayMeanMw != null ? fmt(strategies.original.deliveryDayMeanMw) : '—'} <small>MW mean raw forecast</small>
                </div>
                <p className="wb-strat-desc">
                  Raw national wind forecast published at or before {cutoff}. No causal error correction applied.
                </p>
                <div className="wb-strat-meta">
                  <span>Prior training pairs: <strong>0 (unadjusted)</strong></span>
                  <span>Adjustment rule: <strong>raw_forecast</strong></span>
                  <span>Clamping: <strong>None</strong></span>
                </div>
              </div>

              {/* 2. Expanding Mean Correction */}
              <div
                className={'wb-strat-card ' + (strategy === 'expanding' || strategy === 'all' ? 'selected' : '')}
                onClick={() => setStrategy('expanding')}
                style={{ cursor: 'pointer' }}
              >
                <div className="wb-strat-top">
                  <span className="wb-strat-name">Expanding Mean Correction</span>
                  <span className={'wb-status-tag ' + (strategies.expanding?.hasEnoughHistory ? 'ready' : 'insufficient')}>
                    {strategies.expanding?.hasEnoughHistory ? 'Calibrated' : 'Insufficient history'}
                  </span>
                </div>
                <div className="wb-strat-bias">
                  {strategies.expanding?.hasEnoughHistory && strategies.expanding?.deliveryDayMeanMw != null
                    ? fmt(strategies.expanding.deliveryDayMeanMw)
                    : '—'}
                  <small>MW adjusted mean{strategies.expanding?.meanBiasMw != null ? ` · bias ${strategies.expanding.meanBiasMw > 0 ? '+' : ''}${fmt(strategies.expanding.meanBiasMw)}` : ''}</small>
                </div>
                <p className="wb-strat-desc">
                  Subtracts the cumulative mean signed error from all causally eligible prior delivery slots observed before cutoff.
                </p>
                <div className="wb-strat-meta">
                  <span>
                    Prior training pairs:{' '}
                    <strong>
                      {strategies.expanding?.trainingPairs} / {strategies.expanding?.minTrainingPairs} req.
                    </strong>
                  </span>
                  <span>Adjustment rule: <strong>max(0, raw − bias)</strong></span>
                  <span>Prior error MAE: <strong>{strategies.expanding?.trainingError?.maeMw != null ? `${fmt(strategies.expanding.trainingError.maeMw)} MW` : '—'}</strong></span>
                  <span>History window: <strong>All prior Jan days</strong></span>
                </div>
              </div>

              {/* 3. Rolling 7-Day Mean Correction */}
              <div
                className={'wb-strat-card ' + (strategy === 'rolling7Day' || strategy === 'all' ? 'selected' : '')}
                onClick={() => setStrategy('rolling7Day')}
                style={{ cursor: 'pointer' }}
              >
                <div className="wb-strat-top">
                  <span className="wb-strat-name">Rolling 7-Day Correction</span>
                  <span className={'wb-status-tag ' + (strategies.rolling7Day?.hasEnoughHistory ? 'ready' : 'insufficient')}>
                    {strategies.rolling7Day?.hasEnoughHistory ? 'Calibrated' : 'Insufficient history'}
                  </span>
                </div>
                <div className="wb-strat-bias">
                  {strategies.rolling7Day?.hasEnoughHistory && strategies.rolling7Day?.deliveryDayMeanMw != null
                    ? fmt(strategies.rolling7Day.deliveryDayMeanMw)
                    : '—'}
                  <small>MW adjusted mean{strategies.rolling7Day?.meanBiasMw != null ? ` · bias ${strategies.rolling7Day.meanBiasMw > 0 ? '+' : ''}${fmt(strategies.rolling7Day.meanBiasMw)}` : ''}</small>
                </div>
                <p className="wb-strat-desc">
                  Subtracts the mean signed error from causally eligible observations in the previous 7 decision dates [D−7, D−1].
                </p>
                <div className="wb-strat-meta">
                  <span>
                    Prior training pairs:{' '}
                    <strong>
                      {strategies.rolling7Day?.trainingPairs} / {strategies.rolling7Day?.minTrainingPairs} req.
                    </strong>
                  </span>
                  <span>Adjustment rule: <strong>max(0, raw − bias)</strong></span>
                  <span>Prior error MAE: <strong>{strategies.rolling7Day?.trainingError?.maeMw != null ? `${fmt(strategies.rolling7Day.trainingError.maeMw)} MW` : '—'}</strong></span>
                  <span>History window: <strong>Prior 7 decision days</strong></span>
                </div>
              </div>
            </div>

            {(!strategies.expanding?.hasEnoughHistory || !strategies.rolling7Day?.hasEnoughHistory) && (
              <div className="wb-insufficient-callout">
                <Icon name="info" size={17} />
                <div>
                  <strong>Insufficient causal history.</strong>{' '}
                  Each correction needs at least 72 matched observations whose actuals were published at or before this cutoff.
                  Expanding has {strategies.expanding?.trainingPairs ?? 0} of 72
                  ({strategies.expanding?.hasEnoughHistory ? 'eligible' : 'not applied'}).
                  Rolling 7-day has {strategies.rolling7Day?.trainingPairs ?? 0} of 72
                  ({strategies.rolling7Day?.hasEnoughHistory ? 'eligible' : 'not applied'}).
                  The original forecast stays available. No adjustment is filled in for a method below the threshold.
                </div>
              </div>
            )}
          </section>

          {/* ── SECTION B: SCENARIO FORECAST PROFILE CHART ── */}
          <section className="surface chart-card">
            <div className="surface-head">
              <div>
                <div className="section-eyebrow">PREDICTIVE COMPARISON</div>
                <h2>Forecast scenarios vs. realized outturn</h2>
                <p>
                  Delivery {dayFormat(exAnte.deliveryDate, { year: 'numeric' })}, forecasts visible by {cutoff} on {dayFormat(exAnte.decisionDate, { year: 'numeric' })}.
                </p>
              </div>
            </div>

            <div className="legend">
              <span><i className="key-actual" />Actual generation (half-hourly FUELHH)</span>
              {(strategy === 'all' || strategy === 'original') && (
                <span>
                  <i className="key-forecast" />
                  <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#f0a357', marginRight: 4, verticalAlign: 'middle' }} />
                  Original (Raw WINDFOR)
                </span>
              )}
              {(strategy === 'all' || strategy === 'expanding') && (
                <span>
                  <i className="key-expanding" />
                  <span style={{ display: 'inline-block', width: 6, height: 6, background: '#2b82ba', marginRight: 4, verticalAlign: 'middle' }} />
                  Expanding Mean Adj.
                </span>
              )}
              {(strategy === 'all' || strategy === 'rolling7Day') && (
                <span>
                  <i className="key-rolling" />
                  <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#8b5cf6', marginRight: 4, verticalAlign: 'middle' }} />
                  Rolling 7-Day Adj.
                </span>
              )}
            </div>

            <ScenarioChart periods={periods} selectedStrategy={strategy} title="Scenario forecasts vs outturn" />

            <div className="chart-under">
              <div>
                <Icon name="clock" size={15} />
                Hourly WINDFOR points are connected across the unobserved half-hour. No synthetic record is inserted. The teal actual line is learned after delivery and was not known at the cutoff.
              </div>
            </div>
          </section>

          {/* ── SECTION C: RETROSPECTIVE EVALUATION (LEARNED AFTER DELIVERY) ── */}
          <section className="wb-panel" style={{ marginTop: 20 }}>
            <div className="wb-panel-header">
              <div>
                <span className="wb-section-badge wb-badge-retro">
                  <Icon name="shield" size={12} /> Learned After Delivery
                </span>
                <h2>Retrospective Outturn Evaluation</h2>
                <p>
                  Evaluated strictly on identical matched timestamps ({retro.matchedPeriodCount} periods) where actual generation and all active forecasts coexist. Realized outcomes were observable only after delivery.
                </p>
              </div>
            </div>

            {/* Winner Banner */}
            {dayComparison && (
              <div className="wb-winner-banner">
                <div className="wb-winner-icon">
                  <Icon name="zap" size={20} />
                </div>
                <div className="wb-winner-text">
                  <strong>Lowest retrospective MAE this delivery day: {strategies[dayComparison.bestStrategy]?.name || dayComparison.bestStrategy}</strong>
                  <span>{dayComparison.summary}</span>
                </div>
              </div>
            )}

            {/* Scorecard Table */}
            <div className="wb-comp-grid">
              {/* Original */}
              <div className={'wb-comp-cell ' + (dayComparison?.bestStrategy === 'original' ? 'winner-cell' : '')}>
                <div className="wb-comp-title">
                  <span>Original WINDFOR</span>
                  {dayComparison?.bestStrategy === 'original' && <Tag tone="ok">Lowest MAE</Tag>}
                </div>
                <div className="wb-comp-stat">
                  <span>Mean absolute error (MAE)</span>
                  <strong>{retro.metrics?.original?.maeMw != null ? `${fmt(retro.metrics.original.maeMw)} MW` : '—'}</strong>
                </div>
                <div className="wb-comp-stat">
                  <span>Root mean square error (RMSE)</span>
                  <strong>{retro.metrics?.original?.rmseMw != null ? `${fmt(retro.metrics.original.rmseMw)} MW` : '—'}</strong>
                </div>
                <div className="wb-comp-stat">
                  <span>Mean signed bias</span>
                  <strong className={retro.metrics?.original?.biasMw > 0 ? 'pos-error' : 'neg-error'}>
                    {retro.metrics?.original?.biasMw != null ? `${retro.metrics.original.biasMw > 0 ? '+' : ''}${fmt(retro.metrics.original.biasMw)} MW` : '—'}
                  </strong>
                </div>
                <div className="wb-comp-stat">
                  <span>90th percentile error</span>
                  <strong>{retro.metrics?.original?.p90AbsoluteErrorMw != null ? `${fmt(retro.metrics.original.p90AbsoluteErrorMw)} MW` : '—'}</strong>
                </div>
                <div className="wb-comp-stat">
                  <span>Identical evaluation sample</span>
                  <strong>{retro.metrics?.original?.count ?? 0} matched periods</strong>
                </div>
              </div>

              {/* Expanding */}
              <div className={'wb-comp-cell ' + (dayComparison?.bestStrategy === 'expanding' ? 'winner-cell' : '')}>
                <div className="wb-comp-title">
                  <span>Expanding Mean Correction</span>
                  {dayComparison?.bestStrategy === 'expanding' && <Tag tone="ok">Lowest MAE</Tag>}
                </div>
                {strategies.expanding?.hasEnoughHistory ? (
                  <>
                    <div className="wb-comp-stat">
                      <span>Mean absolute error (MAE)</span>
                      <strong>{retro.metrics?.expanding?.maeMw != null ? `${fmt(retro.metrics.expanding.maeMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Root mean square error (RMSE)</span>
                      <strong>{retro.metrics?.expanding?.rmseMw != null ? `${fmt(retro.metrics.expanding.rmseMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Mean signed bias</span>
                      <strong className={retro.metrics?.expanding?.biasMw > 0 ? 'pos-error' : 'neg-error'}>
                        {retro.metrics?.expanding?.biasMw != null ? `${retro.metrics.expanding.biasMw > 0 ? '+' : ''}${fmt(retro.metrics.expanding.biasMw)} MW` : '—'}
                      </strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>90th percentile error</span>
                      <strong>{retro.metrics?.expanding?.p90AbsoluteErrorMw != null ? `${fmt(retro.metrics.expanding.p90AbsoluteErrorMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Period improvements</span>
                      <strong>
                        {dayComparison?.expandingImprovement?.improvedPeriods ?? 0} improved / {dayComparison?.expandingImprovement?.worsenedPeriods ?? 0} worsened
                      </strong>
                    </div>
                  </>
                ) : (
                  <p className="na-text" style={{ padding: '24px 0', textAlign: 'center' }}>
                    Unavailable (insufficient history &lt; 72 pairs)
                  </p>
                )}
              </div>

              {/* Rolling 7-Day */}
              <div className={'wb-comp-cell ' + (dayComparison?.bestStrategy === 'rolling7Day' ? 'winner-cell' : '')}>
                <div className="wb-comp-title">
                  <span>Rolling 7-Day Correction</span>
                  {dayComparison?.bestStrategy === 'rolling7Day' && <Tag tone="ok">Lowest MAE</Tag>}
                </div>
                {strategies.rolling7Day?.hasEnoughHistory ? (
                  <>
                    <div className="wb-comp-stat">
                      <span>Mean absolute error (MAE)</span>
                      <strong>{retro.metrics?.rolling7Day?.maeMw != null ? `${fmt(retro.metrics.rolling7Day.maeMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Root mean square error (RMSE)</span>
                      <strong>{retro.metrics?.rolling7Day?.rmseMw != null ? `${fmt(retro.metrics.rolling7Day.rmseMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Mean signed bias</span>
                      <strong className={retro.metrics?.rolling7Day?.biasMw > 0 ? 'pos-error' : 'neg-error'}>
                        {retro.metrics?.rolling7Day?.biasMw != null ? `${retro.metrics.rolling7Day.biasMw > 0 ? '+' : ''}${fmt(retro.metrics.rolling7Day.biasMw)} MW` : '—'}
                      </strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>90th percentile error</span>
                      <strong>{retro.metrics?.rolling7Day?.p90AbsoluteErrorMw != null ? `${fmt(retro.metrics.rolling7Day.p90AbsoluteErrorMw)} MW` : '—'}</strong>
                    </div>
                    <div className="wb-comp-stat">
                      <span>Period improvements</span>
                      <strong>
                        {dayComparison?.rollingImprovement?.improvedPeriods ?? 0} improved / {dayComparison?.rollingImprovement?.worsenedPeriods ?? 0} worsened
                      </strong>
                    </div>
                  </>
                ) : (
                  <p className="na-text" style={{ padding: '24px 0', textAlign: 'center' }}>
                    Unavailable (insufficient history &lt; 72 pairs)
                  </p>
                )}
              </div>
            </div>
          </section>

          <section className="wb-panel">
            <div className="wb-panel-header">
              <div>
                <span className="wb-section-badge wb-badge-retro">
                  <Icon name="shield" size={12} /> Learned after delivery
                </span>
                <h2>Historical score comparison</h2>
                <p>
                  Month scores use one shared observation set: only days where both corrections had at least 72 causal training pairs. A method with insufficient history is marked unavailable instead of being scored on a smaller sample.
                </p>
              </div>
            </div>
            <p style={{ fontSize: 12, color: '#4d6b76', lineHeight: 1.7, marginBottom: 14 }}>{monthReading}</p>
            {resolved.evaluationPopulations && (
              <div className="wb-comp-grid" style={{ marginBottom: 14 }}>
                {[
                  ['Expanding-eligible set', resolved.evaluationPopulations.expandingEligible],
                  ['Rolling-eligible set', resolved.evaluationPopulations.rollingEligible],
                ].map(([label, block]) => (
                  <div className="wb-comp-cell" key={label}>
                    <div className="wb-comp-title"><span>{label}</span></div>
                    <p className="wb-strat-desc">{block.note}</p>
                    <div className="wb-comp-stat"><span>Eligible days</span><strong>{block.eligibleDays}</strong></div>
                    <div className="wb-comp-stat"><span>Matched observations</span><strong>{block.matchedObservations}</strong></div>
                    <div className="wb-comp-stat"><span>Original MAE</span><strong>{block.original?.maeMw != null ? `${fmt(block.original.maeMw)} MW` : '—'}</strong></div>
                    <div className="wb-comp-stat"><span>Adjusted MAE</span><strong>{block.adjusted?.maeMw != null ? `${fmt(block.adjusted.maeMw)} MW` : 'Unavailable'}</strong></div>
                    <div className="wb-comp-stat"><span>Days improved / worsened</span><strong>{block.daysImproved} / {block.daysWorsened}</strong></div>
                  </div>
                ))}
                <div className="wb-comp-cell">
                  <div className="wb-comp-title"><span>Shared three-way set</span></div>
                  <p className="wb-strat-desc">{resolved.evaluationPopulations.commonThreeWay.note}</p>
                  <div className="wb-comp-stat"><span>Eligible days</span><strong>{resolved.evaluationPopulations.commonThreeWay.eligibleDays}</strong></div>
                  <div className="wb-comp-stat"><span>Matched observations</span><strong>{resolved.evaluationPopulations.commonThreeWay.matchedObservations}</strong></div>
                  <div className="wb-comp-stat"><span>Not the dashboard matched count</span><strong>Separate population</strong></div>
                </div>
              </div>
            )}
            {monthSummary?.commonEvaluationObservations > 0 && (
              <div className="wb-comp-grid">
                {[
                  ['Original', monthSummary.commonOriginalMetrics],
                  ['Expanding', monthSummary.commonExpandingMetrics],
                  ['Rolling 7-day', monthSummary.commonRollingMetrics],
                ].map(([label, block]) => (
                  <div className="wb-comp-cell" key={label}>
                    <div className="wb-comp-title"><span>{label}</span></div>
                    <div className="wb-comp-stat"><span>MAE</span><strong>{block?.maeMw != null ? `${fmt(block.maeMw)} MW` : '—'}</strong></div>
                    <div className="wb-comp-stat"><span>RMSE</span><strong>{block?.rmseMw != null ? `${fmt(block.rmseMw)} MW` : '—'}</strong></div>
                    <div className="wb-comp-stat"><span>Signed bias</span><strong>{block?.biasMw != null ? `${block.biasMw > 0 ? '+' : ''}${fmt(block.biasMw)} MW` : '—'}</strong></div>
                    <div className="wb-comp-stat"><span>P90 absolute error</span><strong>{block?.p90AbsoluteErrorMw != null ? `${fmt(block.p90AbsoluteErrorMw)} MW` : '—'}</strong></div>
                    <div className="wb-comp-stat"><span>Matched observations</span><strong>{block?.count ?? 0}</strong></div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* ── SECTION D: 31-DAY WALK-FORWARD BENCHMARK TABLE ── */}
          {resolved.dailyHistory?.length > 0 && (
            <section className="surface daily-table" style={{ marginTop: 20 }}>
              <div className="surface-head">
                <div>
                  <div className="section-eyebrow">MONTH-WIDE BENCHMARK</div>
                  <h2>Full January 2026 Walk-Forward Comparison</h2>
                  <p>
                    All 31 decision days evaluated side-by-side. Click any row to inspect its scenario profile.
                  </p>
                </div>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Decision Day</th>
                      <th>Delivery Day</th>
                      <th className="number">Exp. Training Pairs</th>
                      <th className="number">Roll. Training Pairs</th>
                      <th className="number">Orig. MAE (MW)</th>
                      <th className="number">Exp. MAE (MW)</th>
                      <th className="number">Roll. MAE (MW)</th>
                      <th>Lowest retrospective MAE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {resolved.dailyHistory.map(d => {
                      const isSelected = d.decisionDate === selectedDay;
                      const hasExp = d.hasEnoughExpanding && d.expandingMaeMw != null;
                      const hasRoll = d.hasEnoughRolling && d.rollingMaeMw != null;
                      let winnerText = 'Original';
                      let winnerTone = 'neutral';
                      let minMae = d.origMaeMw;
                      if (hasExp && d.expandingMaeMw < (minMae ?? Infinity)) {
                        winnerText = 'Expanding';
                        winnerTone = 'ok';
                        minMae = d.expandingMaeMw;
                      }
                      if (hasRoll && d.rollingMaeMw < (minMae ?? Infinity)) {
                        winnerText = 'Rolling 7D';
                        winnerTone = 'info';
                      }
                      return (
                        <tr
                          key={d.decisionDate}
                          className={'click-row ' + (isSelected ? 'selected-row' : '')}
                          onClick={() => onSelectDay(d.decisionDate)}
                        >
                          <td><strong>{dayFormat(d.decisionDate, { year: 'numeric' })}</strong></td>
                          <td>{dayFormat(d.deliveryDate, { year: 'numeric' })}</td>
                          <td className="number">{d.trainingPairsExpanding}</td>
                          <td className="number">{d.trainingPairsRolling}</td>
                          <td className="number">{d.origMaeMw != null ? fmt(d.origMaeMw) : '—'}</td>
                          <td className="number">
                            {hasExp ? fmt(d.expandingMaeMw) : <span className="na-text">—</span>}
                          </td>
                          <td className="number">
                            {hasRoll ? fmt(d.rollingMaeMw) : <span className="na-text">—</span>}
                          </td>
                          <td>
                            <Tag tone={winnerTone}>{winnerText}</Tag>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {/* ── SECTION E: REPRODUCIBILITY & LIMITATIONS ── */}
          <div className="method-grid" style={{ marginTop: 20 }}>
            <section className="surface methodology">
              <div className="section-eyebrow">REPRODUCIBILITY &amp; PROVENANCE</div>
              <h2>Stable Source Provenance</h2>
              <p>
                Every scenario calculation is verifiable and deterministic. Source inputs are normalized from Elexon BMRS WINDFOR and FUELHH streams.
              </p>
              <div className="daily-stat">
                <span>Source Snapshot SHA-256</span>
                <strong style={{ fontFamily: 'monospace', fontSize: 10 }}>{resolved.provenance?.contentHash?.slice(0, 24)}…</strong>
              </div>
              <div className="daily-stat">
                <span>WINDFOR request</span>
                <strong style={{ fontSize: 10, maxWidth: 280, whiteSpace: 'normal', textAlign: 'right' }}>{resolved.provenance?.windforUrl}</strong>
              </div>
              <div className="daily-stat">
                <span>FUELHH request</span>
                <strong style={{ fontSize: 10, maxWidth: 280, whiteSpace: 'normal', textAlign: 'right' }}>{resolved.provenance?.fuelhhUrl}</strong>
              </div>
              <div className="daily-stat">
                <span>Dataset retrieval</span>
                <strong>{resolved.provenance?.retrievedAt ? timeFormat(resolved.provenance.retrievedAt) : 'Not recorded'}</strong>
              </div>
              <div className="daily-stat">
                <span>FUELHH Filter</span>
                <strong>fuelType = WIND only</strong>
              </div>
            </section>

            <section className="surface methodology">
              <div className="section-eyebrow">METHODOLOGICAL ASSUMPTIONS</div>
              <h2>Scope &amp; Operating Constraints</h2>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 14 }}>
                {resolved.limitations?.map((lim, idx) => (
                  <div key={idx} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 11, color: '#68828f', lineHeight: 1.6 }}>
                    <span style={{ color: '#279888', fontWeight: 800 }}>•</span>
                    <span>{lim}</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}
