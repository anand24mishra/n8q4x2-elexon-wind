import React, { useEffect, useMemo, useState } from 'react';
import ScenarioWorkbench from './ScenarioWorkbench';

export const fmt = (value, digit = 0) => value == null ? 'Not available' : new Intl.NumberFormat('en-GB', { maximumFractionDigits: digit, minimumFractionDigits: digit }).format(value);
export const dayFormat = (dateString, opts = {}) => new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', timeZone: 'UTC', ...opts }).format(new Date(`${dateString}T12:00:00Z`));
export const timeFormat = dateTime => dateTime ? new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Europe/London' }).format(new Date(dateTime)) : 'Not published';
const Icons = {
  wind: <><path d="M3 8h11a3 3 0 1 0-3-3" /><path d="M2 12h17a3 3 0 1 1-3 3" /><path d="M4 16h6a3 3 0 1 1-3 3" /></>,
  chart: <><path d="M4 19V5" /><path d="M4 19h16" /><path d="m8 15 4-5 3 2 5-7" /></>,
  grid: <><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></>,
  file: <><path d="M7 2h7l5 5v15H7z" /><path d="M14 2v6h5" /><path d="M10 13h6M10 17h6" /></>,
  refresh: <><path d="M20 11a8 8 0 0 0-14-5L3 9" /><path d="M3 4v5h5" /><path d="M4 13a8 8 0 0 0 14 5l3-3" /><path d="M21 20v-5h-5" /></>,
  calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
  chevron: <path d="m9 18 6-6-6-6" />,
  down: <path d="m6 9 6 6 6-6" />,
  arrow: <><path d="M5 12h14m-7-7 7 7-7 7" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 11v5M12 7h.01" /></>,
  download: <><path d="M12 3v12m-4-4 4 4 4-4M5 17v3h14v-3" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  shield: <><path d="M12 2 4 5v6c0 6 4 9 8 11 4-2 8-5 8-11V5z" /><path d="m9 12 2 2 4-4" /></>,
  zap: <><path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" /></>,
  alert: <><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><path d="M12 9v4M12 17h.01" /></>,
  layers: <><path d="m12 2 10 5-10 5L2 7z" /><path d="m2 12 10 5 10-5" /><path d="m2 17 10 5 10-5" /></>,
};
export function Icon({ name, size = 18, className = '' }) { return <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{Icons[name]}</svg>; }

export function lineSections(rows, field, x, y, maxGap = 1) {
  const sections = []; let current = []; let lastIdx = -1;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i][field] == null) continue;
    if (lastIdx !== -1 && (i - lastIdx) > maxGap) {
      if (current.length > 1) sections.push(current);
      current = [];
    }
    current.push(`${x(i)},${y(rows[i][field])}`);
    lastIdx = i;
  }
  if (current.length > 1) sections.push(current);
  return sections;
}
function ForecastChart({ rows, title = 'Wind generation profile', compact = false }) {
  const [active, setActive] = useState(null);
  const max = Math.max(100, ...rows.flatMap(r => [r.actual, r.forecast].filter(v => v != null))) * 1.12;
  const W = 950, H = compact ? 180 : 280, left = 58, right = 20, top = 18, bottom = 38;
  const x = i => left + (i / Math.max(rows.length - 1, 1)) * (W - left - right);
  const y = v => top + (1 - v / max) * (H - top - bottom);
  const validIndex = active != null && rows[active] ? active : null;
  const precision = Math.max(1, Math.floor(rows.length / 6));
  const fcMaxGap = compact ? 1 : 2;
  return <div className={'chart-wrap ' + (compact ? 'smallchart' : '')}>
    <svg className="line-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={compact ? `${title} in MW` : `${title}: actual generation versus forecast, in MW`} onMouseLeave={() => setActive(null)}>
      {Array.from({ length: 5 }, (_, i) => <g key={i}><line x1={left} x2={W - right} y1={y(max * i / 4)} y2={y(max * i / 4)} stroke="#e6edf0" strokeDasharray="4 5" /><text x={left - 12} y={y(max * i / 4) + 4} textAnchor="end" className="axis">{compact ? fmt(max * i / 4) : `${fmt(max * i / 4 / 1000, 1)}k`}</text></g>)}
      {rows.map((row, i) => i % precision === 0 || i === rows.length - 1 ? <text key={i} x={x(i)} y={H - 12} textAnchor="middle" className="axis">{compact ? dayFormat(row.deliveryDate) : new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(new Date(row.startTime))}</text> : null)}
      {lineSections(rows, 'actual', x, y, 1).map((points, i) => <polyline key={'a' + i} fill="none" stroke="#1bb7a4" strokeWidth="2.8" strokeLinejoin="round" strokeLinecap="round" points={points.join(' ')} />)}
      {lineSections(rows, 'forecast', x, y, fcMaxGap).map((points, i) => <polyline key={'f' + i} fill="none" stroke="#f0a357" strokeWidth="2.6" strokeLinejoin="round" strokeLinecap="round" strokeDasharray="6 4" points={points.join(' ')} />)}
      {rows.map((row, i) => row.forecast != null ? <circle key={'fm' + i} cx={x(i)} cy={y(row.forecast)} r={compact ? 2.5 : 3.5} fill="#f0a357" stroke="#ffffff" strokeWidth={compact ? 1 : 1.5} /> : null)}
      {rows.map((row, i) => <rect key={i} x={x(i) - (W - left - right) / Math.max(rows.length - 1, 1) / 2} y={top} width={(W - left - right) / Math.max(rows.length - 1, 1)} height={H - top - bottom} fill="transparent" onMouseEnter={() => setActive(i)}><title>{compact ? `${dayFormat(row.deliveryDate)}: P90 ${fmt(row.forecast)} MW, MAE ${fmt(row.actual)} MW` : `${timeFormat(row.startTime)} — Forecast (hourly): ${row.forecast != null ? `${fmt(row.forecast)} MW` : 'No half-hr forecast'}, Actual (half-hourly): ${row.actual != null ? `${fmt(row.actual)} MW` : 'N/A'}`}</title></rect>)}
      {validIndex != null && <g><line x1={x(validIndex)} x2={x(validIndex)} y1={top} y2={H - bottom} stroke="#8da5af" strokeDasharray="3 4" />
        {['actual', 'forecast'].map(field => rows[validIndex][field] != null ? <circle key={field} cx={x(validIndex)} cy={y(rows[validIndex][field])} r="5" fill={field === 'actual' ? '#1bb7a4' : '#f0a357'} stroke="white" strokeWidth="2" /> : null)}</g>}
    </svg>
    {validIndex != null && <div className="chart-tooltip">
      <strong>{compact ? dayFormat(rows[validIndex].deliveryDate, { year: 'numeric' }) : timeFormat(rows[validIndex].startTime)}</strong>
      {compact ? <>
        <span>P90 abs. error <b>{fmt(rows[validIndex].forecast)} MW</b></span>
        <span>MAE <b>{fmt(rows[validIndex].actual)} MW</b></span>
      </> : <>
        <span>Forecast (hourly) <b>{rows[validIndex].forecast != null ? `${fmt(rows[validIndex].forecast)} MW` : <i style={{ color: '#8da5af', fontStyle: 'normal' }}>No half-hr WINDFOR</i>}</b></span>
        <span>Forecast published <b>{rows[validIndex].publishTime ? timeFormat(rows[validIndex].publishTime) : 'Not published'}</b></span>
        <span>Actual (half-hourly) <b>{rows[validIndex].actual != null ? `${fmt(rows[validIndex].actual)} MW` : 'Missing'}</b></span>
        <span>Missing-data status <b>{rows[validIndex].forecast == null ? 'Hourly forecast not observed at this half-hour' : rows[validIndex].actual == null ? 'Actual missing' : 'Matched'}</b></span>
        {rows[validIndex].forecast != null && rows[validIndex].actual != null && <span>Error (fc − act) <b className={rows[validIndex].forecast - rows[validIndex].actual > 0 ? 'pos-error' : 'neg-error'}>{rows[validIndex].forecast - rows[validIndex].actual > 0 ? '+' : ''}{fmt(rows[validIndex].forecast - rows[validIndex].actual)} MW</b></span>}
      </>}
    </div>}
  </div>;
}

function MetricCard({ label, value, unit, detail, chip, positive = false }) { return <div className="metric-card"><div className="metric-heading">{label}<Icon name="info" size={14} /></div><div className="metric-value">{value}<small>{unit}</small></div><div className="metric-foot"><span className={positive ? 'positive' : ''}>{chip}</span>{detail}</div></div>; }
export function Tag({ children, tone = 'neutral' }) { return <span className={'tag ' + tone}>{children}</span>; }

// ── Backtest comparison bar chart ──────────────────────────────────────────
function BacktestBars({ daily }) {
  const [hover, setHover] = useState(null);
  const eligible = daily.filter(d => d.hasEnoughHistory && d.originalMetrics?.maeMw != null);
  if (!eligible.length) return <p className="bt-empty">No days with sufficient training history (≥72 pairs).</p>;
  const maxVal = Math.max(...eligible.flatMap(d => [d.originalMetrics.maeMw, d.adjustedMetrics?.maeMw ?? 0])) * 1.15;
  const W = 900, H = 200, left = 52, right = 10, top = 12, bottom = 36;
  const barW = Math.max(4, (W - left - right) / eligible.length - 4);
  const x = i => left + (i + 0.5) * ((W - left - right) / eligible.length);
  const y = v => top + (1 - v / maxVal) * (H - top - bottom);
  return (
    <div className="chart-wrap">
      <svg className="line-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Original vs adjusted MAE by decision day" onMouseLeave={() => setHover(null)}>
        {Array.from({ length: 4 }, (_, i) => {
          const v = maxVal * (i + 1) / 4;
          return <g key={i}><line x1={left} x2={W - right} y1={y(v)} y2={y(v)} stroke="#e6edf0" strokeDasharray="3 4" /><text x={left - 6} y={y(v) + 4} textAnchor="end" className="axis">{fmt(v)}</text></g>;
        })}
        {eligible.map((d, i) => {
          const orig = d.originalMetrics.maeMw;
          const adj = d.adjustedMetrics?.maeMw;
          const bx = x(i);
          return <g key={d.decisionDate}>
            <rect x={bx - barW / 2 - 1} y={y(orig)} width={barW / 2} height={H - bottom - y(orig)} fill="#f0a357" opacity={hover === i ? 1 : 0.75} onMouseEnter={() => setHover(i)} />
            {adj != null && <rect x={bx + 1} y={y(adj)} width={barW / 2} height={H - bottom - y(adj)} fill="#1bb7a4" opacity={hover === i ? 1 : 0.75} onMouseEnter={() => setHover(i)} />}
            {i % 4 === 0 && <text x={bx} y={H - bottom + 14} textAnchor="middle" className="axis">{dayFormat(d.decisionDate)}</text>}
          </g>;
        })}
      </svg>
      {hover != null && eligible[hover] && <div className="chart-tooltip">
        <strong>{dayFormat(eligible[hover].decisionDate, { year: 'numeric' })}</strong>
        <span>Original MAE <b>{fmt(eligible[hover].originalMetrics?.maeMw)} MW</b></span>
        <span>Adjusted MAE <b>{fmt(eligible[hover].adjustedMetrics?.maeMw)} MW</b></span>
        <span>Bias used <b>{eligible[hover].meanBiasMw != null ? `${eligible[hover].meanBiasMw > 0 ? '+' : ''}${fmt(eligible[hover].meanBiasMw)} MW` : '—'}</b></span>
        <span>Training pairs <b>{eligible[hover].trainingPairs} (prior matched hourly)</b></span>
      </div>}
      <div className="legend" style={{ marginTop: 8 }}>
        <span><i className="key-forecast" />Original MAE</span>
        <span><i className="key-actual" />Bias-adjusted MAE</span>
      </div>
    </div>
  );
}

// ── Risk indicator pill ────────────────────────────────────────────────────
function RiskPill({ label, value, tone }) {
  return <div className={`risk-pill risk-${tone}`}>
    <span className="risk-label">{label}</span>
    <strong className="risk-value">{value}</strong>
  </div>;
}

function tendencyMeta(label) {
  if (label === 'systematic_overforecast') return { text: 'Systematic overforecast', tone: 'warn', note: 'Historical mean signed error is positive (> +50 MW). Forecast has tended to overstate observed wind.' };
  if (label === 'systematic_underforecast') return { text: 'Systematic underforecast', tone: 'info', note: 'Historical mean signed error is negative (< −50 MW). Forecast has tended to understate observed wind.' };
  if (label === 'insufficient_history') return { text: 'Insufficient history', tone: 'neutral', note: 'Fewer than 10 prior matched pairs available. No reliable tendency can be reported.' };
  return { text: 'Near-neutral bias', tone: 'ok', note: 'Historical mean signed error is within ±50 MW. No strong systematic tendency observed.' };
}

export default function App() {
  const [cutoff, setCutoff] = useState('09:50');
  const [selectedDay, setSelectedDay] = useState('2026-01-15');
  const [tab, setTab] = useState('overview');
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [revision, setRevision] = useState(0);
  const [backtest, setBacktest] = useState(null);
  const [btBusy, setBtBusy] = useState(false);
  const [riskBrief, setRiskBrief] = useState(null);
  const [rbBusy, setRbBusy] = useState(false);
  const [btError, setBtError] = useState(null);
  const [rbError, setRbError] = useState(null);
  const [strategy, setStrategy] = useState('all');

  useEffect(() => {
    let subscribed = true;
    const poll = async () => { try { const r = await fetch('/api/status'); if (!r.ok) throw new Error('Status request failed'); const json = await r.json(); if (subscribed) { setStatus(current => { if (current?.status === 'loading' && json.status === 'ready') setRevision(n => n + 1); return json; }); if (json.status === 'idle' && !json.lastSynced) { fetch('/api/refresh', { method: 'POST' }).catch(() => { }); } } } catch { if (subscribed) setStatus({ status: 'unreachable' }); } };
    poll(); const timer = setInterval(poll, 2200); return () => { subscribed = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setBusy(true);
    setError(null);
    fetch(`/api/dashboard?cutoff=${encodeURIComponent(cutoff)}`, { signal: controller.signal })
      .then(async r => { const body = await r.json(); if (!r.ok) throw new Error(body.detail || 'Could not load dashboard'); return body; })
      .then(j => { if (alive) setData(j); })
      .catch(e => { if (!alive || e.name === 'AbortError') return; setData(null); setError(e.message); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; controller.abort(); };
  }, [cutoff, revision]);

  // Load backtest when tab is opened
  useEffect(() => {
    if (tab !== 'backtest' || !data) return undefined;
    const controller = new AbortController();
    let alive = true;
    setBtBusy(true);
    setBtError(null);
    fetch(`/api/backtest?cutoff=${encodeURIComponent(cutoff)}`, { signal: controller.signal }).then(async r => { const body = await r.json(); if (!r.ok) throw new Error(body.detail || 'Backtest failed'); return body; }).then(j => { if (alive) setBacktest(j); }).catch(e => { if (!alive || e.name === 'AbortError') return; setBtError(e.message); }).finally(() => { if (alive) setBtBusy(false); });
    return () => { alive = false; controller.abort(); };
  }, [tab, cutoff, revision]);

  // Load risk brief when tab is opened or date changes
  useEffect(() => {
    if (tab !== 'risk' || !data) return undefined;
    const controller = new AbortController();
    let alive = true;
    setRbBusy(true);
    setRbError(null);
    fetch(`/api/risk-brief?cutoff=${encodeURIComponent(cutoff)}&date=${selectedDay}`, { signal: controller.signal }).then(async r => { const body = await r.json(); if (!r.ok) throw new Error(body.detail || 'Risk brief failed'); return body; }).then(j => { if (alive) setRiskBrief(j); }).catch(e => { if (!alive || e.name === 'AbortError') return; setRbError(e.message); }).finally(() => { if (alive) setRbBusy(false); });
    return () => { alive = false; controller.abort(); };
  }, [tab, cutoff, revision, selectedDay]);

  const refresh = async () => { try { const r = await fetch('/api/refresh', { method: 'POST' }); if (!r.ok) throw new Error('Could not start Elexon refresh'); setStatus(await r.json()); } catch (e) { setError(e.message); } };
  const report = useMemo(() => data?.daily.find(d => d.decisionDate === selectedDay) || data?.daily[0], [data, selectedDay]);
  const periods = useMemo(() => data?.periods.filter(p => p.decisionDate === report?.decisionDate) || [], [data, report]);
  const trend = useMemo(() => data?.daily.map(r => ({ deliveryDate: r.deliveryDate, actual: r.maeMw, forecast: r.p90AbsoluteErrorMw })) || [], [data]);
  const dlCSV = () => { if (!data) return; const header = ['decisionDate', 'deliveryDate', 'startTimeUTC', 'forecastPublishedUTC', 'actualPublishedUTC', 'forecastMW', 'actualMW', 'forecastMinusActualMW', 'forecastMinusActualMWh']; const body = data.periods.map(r => [r.decisionDate, r.deliveryDate, r.startTime, r.publishTime || '', r.actualPublishTime || '', r.forecast ?? '', r.actual ?? '', r.error ?? '', r.error == null ? '' : (r.error * 0.5).toFixed(2)].join(',')); const blob = new Blob([[header.join(','), ...body].join('\n')], { type: 'text/csv;charset=utf-8' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `windward-elexon-jan2026-${cutoff.replace(':', '')}.csv`; a.click(); URL.revokeObjectURL(url); };
  const showDetails = () => setTab('explorer');

  const navItems = [
    ['overview', 'grid', 'Overview'],
    ['workbench', 'layers', 'Scenario workbench'],
    ['explorer', 'chart', 'Forecast explorer'],
    ['accuracy', 'shield', 'Accuracy review'],
    ['backtest', 'zap', 'Bias backtest'],
    ['risk', 'alert', 'Risk brief'],
    ['method', 'file', 'Methodology'],
  ];

  const pageTitle = { overview: 'Clarity before commitment.', workbench: 'Day-ahead scenario workbench.', explorer: 'Explore each delivery day.', accuracy: 'See where forecasts missed.', backtest: 'Walk-forward bias correction.', risk: 'Decision risk brief.', method: 'Transparent by design.' };
  const pageSub = { overview: 'Reconstruct the wind forecast visible when your purchasing decision had to be made.', workbench: 'Compare the raw forecast with two causal bias corrections using one shared decision date and cutoff.', explorer: 'Inspect the timestamp-level forecast, actual generation and published revision.', accuracy: 'Evaluate historical forecast bias, magnitude of errors and observation coverage.', backtest: 'Exploratory retrospective: what would a simple mean-bias adjustment have changed?', risk: 'Summarise the observable forecast position and historical tendencies for a specific decision day.', method: 'Every calculation is traceable to publication timestamps and matching generation periods.' };

  const btSummary = backtest?.daysWithEnoughHistory > 0 ? backtest : null;

  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-symbol"><Icon name="wind" size={26} /></div><div><strong>windward<span>.</span></strong><small>ENERGY INTELLIGENCE</small></div></div>
      <div className="sidebar-section">WORKSPACE</div>
      <nav aria-label="Primary navigation">
        {navItems.map(([key, icon, label]) => <button key={key} className={'nav-item ' + (tab === key ? 'nav-active' : '')} onClick={() => setTab(key)}><Icon name={icon} />{label}{tab === key && <span className="nav-dot" />}</button>)}
      </nav>
      <div className="sidebar-foot"><div className="sidebar-period"><div className="side-icon"><Icon name="calendar" /></div><div><span>Analysis period</span><strong>January 2026</strong></div></div><div className="sidebar-note">Historical decision-time analysis<br />United Kingdom <span className="period-dot" /> UTC / GMT</div></div>
    </aside>
    <main className="workspace">
      <header className="topbar"><div className="breadcrumb">Market analytics <Icon name="chevron" size={14} /> <strong>{pageTitle[tab]?.split('.')[0]}</strong></div><div className="top-actions"><div className="as-of"><span className="pulse" /> {data ? 'ELEXON DATA IMPORTED' : 'ELEXON IMPORT REQUIRED'}</div><button className="top-link" onClick={() => setTab('method')}><Icon name="info" size={16} /> How this works</button></div></header>
      <div className="page-content">
        <div className="intro-kicker"><span className="accent-line" /> JANUARY 2026 HISTORICAL PILOT <Tag>DAY-AHEAD VIEW</Tag></div>
        <div className="page-head"><div><h1>{pageTitle[tab]}</h1><p>{pageSub[tab]}</p></div><button className="outline-btn" onClick={dlCSV} disabled={!data}><Icon name="download" size={17} /> Export CSV</button></div>
        <div className="filter-bar"><div className="filter-pair"><span className="filter-label">SOURCE</span><strong className="locked-source">Elexon BMRS · WIND</strong></div><div className="filter-divider" /><div className="filter-pair"><span className="filter-label">PURCHASE CUTOFF (UK TIME)</span><select value={cutoff} onChange={e => setCutoff(e.target.value)} aria-label="Purchase decision cutoff"><option value="08:30">08:30</option><option value="09:20">09:20</option><option value="09:50">09:50 (N2EX example)</option><option value="10:30">10:30</option><option value="12:30">12:30</option><option value="15:30">15:30</option></select></div><div className="filter-spacer" /><button className="refresh-btn" onClick={refresh} disabled={status?.status === 'loading'}><Icon name="refresh" size={16} />{status?.status === 'loading' ? 'Importing…' : 'Sync Elexon'}</button></div>
        {status?.status === 'loading' && <div className="import-bar"><div className="import-text"><Icon name="refresh" size={16} /><span>Importing January 2026 Elexon records</span><strong>{status.done || 0} / {status.total || 2} datasets</strong></div><div className="progress"><div style={{ width: `${100 * (status.done || 0) / (status.total || 2)}%` }} /></div></div>}
        {status?.status === 'failed' && <div className="notice error"><Icon name="info" /><span>Elexon import failed. {status.error}. The previous successful cache, if any, remains available.</span></div>}
        {!data && <section className="empty-panel"><div className="empty-mark"><Icon name="wind" size={32} /></div><h2>{busy ? 'Loading forecast analysis…' : 'Import the January market record'}</h2><p>{busy ? 'Reconstructing historical as-of forecasts.' : 'Only original Elexon records are accepted. The first visit starts a historical import; if it fails, retry using Sync Elexon. No sample figures are substituted.'}</p><div className="empty-actions"><button className="primary-btn" onClick={refresh} disabled={status?.status === 'loading'}><Icon name="refresh" /> Import from Elexon</button></div>{error && <div className="empty-technical">{error}</div>}</section>}
        {data && <>
          {(tab === 'overview' || tab === 'accuracy') && <>
            <div className="section-lead"><div><h2>{tab === 'overview' ? 'January at a glance' : 'Forecast accuracy, quantified'}</h2><span>31 decision days <span className="tiny-divider">·</span> {fmt(data.metrics.count)} matched settlement periods (hourly WINDFOR vs FUELHH) <span className="tiny-divider">·</span> National wind</span></div><Tag>FORECAST − ACTUAL</Tag></div>
            <div className="metrics-grid">
              <MetricCard label="Mean absolute error" value={fmt(data.metrics.maeMw)} unit="MW" chip="MAE" detail="Average absolute deviation" />
              <MetricCard label="Forecast bias" value={data.metrics.biasMw == null ? 'Not available' : `${data.metrics.biasMw > 0 ? '+' : ''}${fmt(data.metrics.biasMw)}`} unit="MW" chip="Signed error" detail="Positive means wind overforecast" />
              <MetricCard label="90th percentile error" value={fmt(data.metrics.p90AbsoluteErrorMw)} unit="MW" chip="P90 abs." detail="90% of paired errors at or below" />
              <MetricCard label="Matched coverage" value={fmt(data.metrics.matchedCoveragePct, 1)} unit="%" chip={`${fmt(data.metrics.count)} / ${fmt(data.metrics.expectedPeriods)} half-hours`} detail="Hourly WINDFOR matched to FUELHH (max 50% / 24 of 48 half-hours per day)" />
            </div>
          </>}
          {(tab === 'overview' || tab === 'explorer') && <div className="two-col"><section className="surface chart-card"><div className="surface-head"><div><div className="section-eyebrow">DAY-AHEAD FORECAST PROFILE</div><h2>Forecast vs. outturn</h2><p>Delivery {dayFormat(report.deliveryDate, { year: 'numeric' })}, forecast visible by {cutoff} on {dayFormat(report.decisionDate)}</p></div><select className="day-select" value={report.decisionDate} onChange={e => setSelectedDay(e.target.value)} aria-label="Select January decision day">{data.daily.map(d => <option key={d.decisionDate} value={d.decisionDate}>{dayFormat(d.decisionDate, { year: 'numeric' })} decision</option>)}</select></div><div className="legend"><span><i className="key-actual" />Actual generation (half-hourly FUELHH)</span><span><i className="key-forecast" /><span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#f0a357', marginRight: 4, verticalAlign: 'middle' }} />Forecast (hourly WINDFOR markers)</span></div><ForecastChart rows={periods} /><div className="chart-under"><div><Icon name="clock" size={15} /> Hourly WINDFOR points connected; half-hour slots remain null (unimputed). As of {cutoff} UK cutoff.</div><button onClick={showDetails}>See period details <Icon name="arrow" size={15} /></button></div></section><section className="surface decision-card"><div className="section-eyebrow">DECISION-DAY QUALITY</div><h2>{dayFormat(report.decisionDate, { year: 'numeric' })}</h2><div className="decision-sub">For power delivery on {dayFormat(report.deliveryDate, { year: 'numeric' })}</div><div className="daily-number">{fmt(report.maeMw)} <small>MW MAE</small></div><div className="daily-rule" /><div className="daily-stat"><span>Mean forecast bias</span><strong>{report.biasMw != null ? (report.biasMw > 0 ? '+' : '') + fmt(report.biasMw) + ' MW' : 'N/A'}</strong></div><div className="daily-stat"><span>Prior known MAE at cutoff</span><strong>{report.priorKnownMaeMw == null ? 'Unavailable' : `${fmt(report.priorKnownMaeMw)} MW (${report.priorKnownMatchedPeriods} periods)`}</strong></div><div className="daily-stat"><span>Matched settlement periods</span><strong>{report.matched} / {report.expected} half-hours ({report.matched} hourly forecasts)</strong></div><div className="daily-stat"><span>Forecast availability</span><strong>{fmt(report.coveragePct, 1)}% (of 48 half-hours; max 24/48 = 50%)</strong></div><div className="daily-stat"><span>Net energy forecast error*</span><strong>{fmt(report.netEnergyErrorMWh)} MWh</strong></div><div className="availability-track"><div style={{ width: `${report.coveragePct}%` }} /></div><div className="interpretation"><Icon name="info" size={17} /><span>{report.count ? report.biasMw > 0 ? 'Positive bias: the selected forecast overstated observed wind on average, a potential under-procurement exposure.' : 'Negative or neutral bias: the forecast did not systematically overstate observed wind on this day.' : 'No paired timestamps for this day. No accuracy claim is possible.'} *On matched periods only; not a procurement order.</span></div></section></div>}
          {tab === 'overview' && <section className="surface daily-table"><div className="surface-head"><div><div className="section-eyebrow">DAILY PERFORMANCE</div><h2>Historical reliability by decision date</h2><p>Select a row to investigate its forecast record.</p></div><button className="plain-btn" onClick={() => setTab('accuracy')}>Review all days <Icon name="arrow" size={16} /></button></div><DailyTable daily={data.daily.slice(0, 9)} selected={report.decisionDate} onChoose={d => { setSelectedDay(d); setTab('explorer'); }} /></section>}
          {tab === 'accuracy' && <><div className="diagnostics-strip"><div><span>RMSE</span><strong>{fmt(data.metrics.rmseMw)} MW</strong></div><div><span>Overforecast frequency</span><strong>{fmt(data.metrics.overforecastRatePct, 1)}%</strong></div><div><span>Net matched energy error</span><strong>{fmt(data.metrics.netEnergyErrorMWh)} MWh</strong></div><div><span>Actual generation coverage</span><strong>{fmt(100 * data.metrics.actualPeriods / data.metrics.expectedPeriods, 1)}% ({data.metrics.actualPeriods}/{data.metrics.expectedPeriods} half-hours)</strong></div></div><section className="surface chart-card fullchart"><div className="surface-head"><div><div className="section-eyebrow">ERROR BY DECISION DAY</div><h2>Magnitude of forecasting uncertainty</h2><p>Daily mean absolute error and P90 absolute error in MW, based only on matched timestamps.</p></div></div><div className="legend"><span><i className="key-actual" />MAE</span><span><i className="key-forecast" />P90 absolute error</span></div><ForecastChart rows={trend} compact title="Daily MAE and P90 absolute error" /></section><section className="surface daily-table"><div className="surface-head"><div><div className="section-eyebrow">COMPLETE PERIOD REVIEW</div><h2>31 decision days, side by side</h2></div></div><DailyTable daily={data.daily} selected={report.decisionDate} onChoose={d => { setSelectedDay(d); setTab('explorer'); }} /></section></>}
          {tab === 'explorer' && <section className="surface daily-table"><div className="surface-head"><div><div className="section-eyebrow">AUDIT TRAIL</div><h2>Every half-hour timestamp</h2><p>Values and selected publication time are preserved; missing observations remain blank.</p></div><Tag>{periods.filter(x => x.error != null).length} / 48 settlement periods matched (hourly WINDFOR)</Tag></div><div className="table-scroll"><table><thead><tr><th>Delivery time (UK)</th><th>Selected publish time (UK)</th><th className="number">Forecast (hourly MW)</th><th className="number">Actual (half-hourly MW)</th><th className="number">Error (MW)</th></tr></thead><tbody>{periods.map(p => <tr key={p.startTime}><td>{timeFormat(p.startTime)}</td><td>{timeFormat(p.publishTime)}</td><td className="number">{p.forecast == null ? <span className="na-text" title="WINDFOR publishes hourly; no half-hour forecast published">—</span> : fmt(p.forecast)}</td><td className="number">{p.actual == null ? '—' : fmt(p.actual)}</td><td className={'number ' + (p.error > 0 ? 'pos-error' : p.error < 0 ? 'neg-error' : '')}>{p.error == null ? '—' : `${p.error > 0 ? '+' : ''}${fmt(p.error)}`}</td></tr>)}</tbody></table></div></section>}

          {tab === 'workbench' && <ScenarioWorkbench cutoff={cutoff} selectedDay={selectedDay} onSelectDay={setSelectedDay} onCutoffChange={setCutoff} availableDays={data.daily} revision={revision} strategy={strategy} onStrategyChange={setStrategy} />}

          {/* ── Walk-forward Bias Backtest ── */}
          {tab === 'backtest' && <div className="backtest-shell">
            <div className="notice" style={{ marginTop: 0, marginBottom: 18 }}><Icon name="info" size={17} /><span><strong>Exploratory retrospective:</strong> {backtest?.disclaimer ? backtest.disclaimer.replace(/^Exploratory retrospective (analysis )?only\.\s*/i, '') : "The mean bias is estimated from strictly prior days' data that would have been observable at that cutoff. This does not represent a proven forward strategy, a prediction interval, or a financial saving estimate."}</span></div>
            {btError && <div className="notice error"><Icon name="info" /><span>{btError}</span></div>}
            {btBusy && <div className="bt-loading"><Icon name="refresh" size={18} />Computing walk-forward backtest…</div>}
            {!btBusy && backtest && <>
              <div className="bt-summary-grid">
                <div className="surface bt-summary-card">
                  <div className="section-eyebrow">MONTH SUMMARY (ELIGIBLE DAYS ONLY)</div>
                  <h2>Original vs. adjusted</h2>
                  <p>Based on {backtest.daysWithEnoughHistory} of 31 decision days with ≥{backtest.minTrainingPairs} prior matched hourly pairs.</p>
                  <div className="bt-compare-row">
                    <div className="bt-compare-block">
                      <div className="bt-compare-label">Original MAE</div>
                      <div className="bt-compare-value">{fmt(backtest.summaryOriginal?.maeMw)} <small>MW</small></div>
                    </div>
                    <Icon name="arrow" size={20} className="bt-arrow" />
                    <div className="bt-compare-block adj">
                      <div className="bt-compare-label">Adjusted MAE</div>
                      <div className="bt-compare-value">{backtest.summaryAdjusted ? fmt(backtest.summaryAdjusted?.maeMw) : '—'} <small>MW</small></div>
                    </div>
                  </div>
                  <div className="bt-stat-row"><span>Original bias</span><strong className={backtest.summaryOriginal?.biasMw > 0 ? 'pos-error' : 'neg-error'}>{backtest.summaryOriginal?.biasMw != null ? `${backtest.summaryOriginal.biasMw > 0 ? '+' : ''}${fmt(backtest.summaryOriginal.biasMw)} MW` : '—'}</strong></div>
                  <div className="bt-stat-row"><span>Adjusted bias</span><strong>{backtest.summaryAdjusted?.biasMw != null ? `${backtest.summaryAdjusted.biasMw > 0 ? '+' : ''}${fmt(backtest.summaryAdjusted.biasMw)} MW` : '—'}</strong></div>
                  <div className="bt-stat-row"><span>Original P90 error</span><strong>{fmt(backtest.summaryOriginal?.p90AbsoluteErrorMw)} MW</strong></div>
                  <div className="bt-stat-row"><span>Adjusted P90 error</span><strong>{backtest.summaryAdjusted ? `${fmt(backtest.summaryAdjusted?.p90AbsoluteErrorMw)} MW` : '—'}</strong></div>
                </div>
                <div className="surface bt-summary-card">
                  <div className="section-eyebrow">ALIGNMENT NOTE</div>
                  <h2>WINDFOR vs FUELHH</h2>
                  <p style={{ fontSize: 12, lineHeight: 1.8, color: '#6c838e', marginTop: 12 }}>{backtest.alignmentNote}</p>
                  <div className="bt-stat-row" style={{ marginTop: 20 }}><span>Min. training pairs required</span><strong>{backtest.minTrainingPairs} prior matched hourly observations</strong></div>
                  <div className="bt-stat-row"><span>Days with enough history</span><strong>{backtest.daysWithEnoughHistory} / 31</strong></div>
                  <div className="bt-stat-row"><span>Days insufficient history</span><strong>{31 - backtest.daysWithEnoughHistory} / 31 (shown as N/A)</strong></div>
                </div>
              </div>
              <section className="surface chart-card fullchart" style={{ marginTop: 20 }}>
                <div className="surface-head"><div><div className="section-eyebrow">DAILY MAE COMPARISON</div><h2>Original vs. bias-adjusted MAE by decision day</h2><p>Only days with ≥{backtest.minTrainingPairs} prior matched pairs are shown. Hover for details.</p></div></div>
                <BacktestBars daily={backtest.daily} />
              </section>
              <section className="surface daily-table" style={{ marginTop: 20 }}>
                <div className="surface-head"><div><div className="section-eyebrow">DAILY BACKTEST TABLE</div><h2>Training pairs and error comparison</h2></div></div>
                <div className="table-scroll"><table><thead><tr><th>Decision day</th><th className="number">Prior training pairs</th><th className="number">Mean bias used (MW)</th><th className="number">Orig. MAE (MW)</th><th className="number">Adj. MAE (MW)</th><th className="number">Improved / Worsened periods</th></tr></thead><tbody>{backtest.daily.map(d => <tr key={d.decisionDate} className={d.hasEnoughHistory ? '' : 'dim-row'}>
                  <td><strong>{dayFormat(d.decisionDate, { year: 'numeric' })}</strong></td>
                  <td className="number" title="Prior matched hourly pairs available before cutoff">{d.trainingPairs}</td>
                  <td className={'number ' + (d.meanBiasMw > 0 ? 'pos-error' : d.meanBiasMw < 0 ? 'neg-error' : '')}>{d.meanBiasMw != null ? `${d.meanBiasMw > 0 ? '+' : ''}${fmt(d.meanBiasMw)}` : <span className="na-text">Insufficient history</span>}</td>
                  <td className="number">{d.originalMetrics?.maeMw != null ? fmt(d.originalMetrics.maeMw) : '—'}</td>
                  <td className="number">{d.hasEnoughHistory && d.adjustedMetrics?.maeMw != null ? fmt(d.adjustedMetrics.maeMw) : <span className="na-text">—</span>}</td>
                  <td className="number">{d.hasEnoughHistory ? `${d.improvedPeriods ?? 0} improved / ${d.worsenedPeriods ?? 0} worsened` : <span className="na-text">—</span>}</td>
                </tr>)}</tbody></table></div>
              </section>
            </>}
          </div>}

          {/* ── Decision Risk Brief ── */}
          {tab === 'risk' && <div className="risk-shell">
            <div className="risk-date-bar">
              <span className="filter-label">DECISION DATE</span>
              <select value={selectedDay} onChange={e => setSelectedDay(e.target.value)} aria-label="Select decision date for risk brief">
                {data.daily.map(d => <option key={d.decisionDate} value={d.decisionDate}>{dayFormat(d.decisionDate, { year: 'numeric' })} decision</option>)}
              </select>
            </div>
            {rbError && <div className="notice error"><Icon name="info" /><span>{rbError}</span></div>}
            {rbBusy && <div className="bt-loading"><Icon name="refresh" size={18} />Loading risk brief…</div>}
            {!rbBusy && riskBrief && <>
              <div className="notice" style={{ marginTop: 8, marginBottom: 18 }}><Icon name="info" size={17} /><span>{riskBrief.disclaimer}</span></div>
              <div className="two-col" style={{ marginTop: 0 }}>
                <section className="surface chart-card">
                  <div className="section-eyebrow">FORECAST POSITION AT CUTOFF</div>
                  <h2>Delivery {dayFormat(riskBrief.deliveryDate, { year: 'numeric' })}</h2>
                  <p style={{ fontSize: 11, color: '#91a3ad', marginTop: 6, lineHeight: 1.7 }}>
                    As known at {cutoff} UK time on {dayFormat(riskBrief.decisionDate, { year: 'numeric' })}.
                  </p>
                  <div className="risk-pills">
                    <RiskPill label="Mean forecast" value={riskBrief.forecastSummary.meanForecastMw != null ? `${fmt(riskBrief.forecastSummary.meanForecastMw)} MW` : '—'} tone="neutral" />
                    <RiskPill label="Periods with forecast" value={`${riskBrief.forecastSummary.periodsWithForecast} / ${riskBrief.forecastSummary.totalPeriods} (${riskBrief.forecastSummary.periodsWithForecast} hourly)`} tone="neutral" />
                    <RiskPill label="WINDFOR coverage" value={`${fmt(riskBrief.forecastSummary.coveragePct, 1)}% (hourly series)`} tone={riskBrief.forecastSummary.coveragePct < 40 ? 'warn' : 'ok'} />
                    <RiskPill label="Data freshness" value={riskBrief.forecastSummary.dataFreshnessHours != null ? `${fmt(riskBrief.forecastSummary.dataFreshnessHours, 1)} h before cutoff` : '—'} tone={riskBrief.forecastSummary.dataFreshnessHours > 3 ? 'warn' : 'ok'} />
                  </div>
                  <div className="daily-stat" style={{ marginTop: 12 }}><span>Latest WINDFOR publish (UK)</span><strong>{timeFormat(riskBrief.forecastSummary.latestPublishTime)}</strong></div>
                  <div className="daily-stat"><span style={{ fontSize: 10, color: '#9caab2' }}>{riskBrief.alignmentNote}</span></div>
                </section>
                <section className="surface decision-card">
                  <div className="section-eyebrow">PRIOR HISTORY INDICATORS</div>
                  <h2>Observed tendency</h2>
                  {(() => {
                    const meta = tendencyMeta(riskBrief.priorHistory.tendencyLabel);
                    return <>
                      <div className={`tendency-badge tendency-${meta.tone}`}>{meta.text}</div>
                      <p style={{ fontSize: 11, color: '#7a8e98', lineHeight: 1.7, marginTop: 10 }}>{meta.note}</p>
                    </>;
                  })()}
                  <div className="daily-rule" style={{ margin: '16px 0 8px' }} />
                  <div className="daily-stat"><span>Prior matched pairs</span><strong>{riskBrief.priorHistory.matchedPairs} hourly pairs</strong></div>
                  <div className="daily-stat"><span>Historical bias</span><strong className={riskBrief.priorHistory.biasMw > 0 ? 'pos-error' : 'neg-error'}>{riskBrief.priorHistory.biasMw != null ? `${riskBrief.priorHistory.biasMw > 0 ? '+' : ''}${fmt(riskBrief.priorHistory.biasMw)} MW` : '—'}</strong></div>
                  <div className="daily-stat"><span>Historical MAE</span><strong>{riskBrief.priorHistory.maeMw != null ? `${fmt(riskBrief.priorHistory.maeMw)} MW` : '—'}</strong></div>
                  <div className="daily-stat"><span>Overforecast rate</span><strong>{riskBrief.priorHistory.overforecastRatePct != null ? `${fmt(riskBrief.priorHistory.overforecastRatePct, 1)}%` : '—'}</strong></div>
                  <div className="interpretation" style={{ marginTop: 14 }}><Icon name="info" size={17} /><span style={{ fontSize: 10 }}>{riskBrief.priorHistory.note}</span></div>
                </section>
              </div>
              {riskBrief.recentDailyHistory?.length > 0 && <section className="surface daily-table" style={{ marginTop: 20 }}>
                <div className="surface-head"><div><div className="section-eyebrow">RECENT DECISION HISTORY (LAST 7 PRIOR DAYS)</div><h2>Day-level accuracy context</h2>{riskBrief.largeMissDays?.length > 0 && <p style={{ color: '#c07437', fontSize: 11, marginTop: 6 }}><Icon name="alert" size={14} /> {riskBrief.largeMissDays.length} day(s) with large misses (MAE {'>'} {fmt(riskBrief.largeMissThresholdMw)} MW, 1.5&times; historical average). Large misses may indicate conditions that challenge systematic forecasts — consider whether similar conditions apply today.</p>}</div></div>
                <div className="table-scroll"><table><thead><tr><th>Decision day</th><th className="number">Matched pairs</th><th className="number">MAE (MW)</th><th className="number">Bias (MW)</th><th className="number">Overforecast rate</th></tr></thead><tbody>{riskBrief.recentDailyHistory.map(d => <tr key={d.decisionDate} className={riskBrief.largeMissDays?.some(x => x.decisionDate === d.decisionDate) ? 'large-miss-row' : ''}>
                  <td><strong>{dayFormat(d.decisionDate, { year: 'numeric' })}</strong></td>
                  <td className="number">{d.n}</td>
                  <td className="number">{fmt(d.maeMw)}</td>
                  <td className={'number ' + (d.biasMw > 0 ? 'pos-error' : d.biasMw < 0 ? 'neg-error' : '')}>{d.biasMw > 0 ? '+' : ''}{fmt(d.biasMw)}</td>
                  <td className="number">{fmt(d.overforecastRatePct, 1)}%</td>
                </tr>)}</tbody></table></div>
              </section>}
            </>}
          </div>}

          {tab === 'method' && <div className="method-grid"><section className="surface methodology"><div className="section-eyebrow">POINT-IN-TIME LOGIC</div><h2>What could the buyer actually know?</h2><div className="steps"><div><b>01</b><span><strong>Anchor the decision</strong>Consider decision dates 1 to 31 January 2026. Each decision refers to next-day delivery, 2 January through 1 February.</span></div><div><b>02</b><span><strong>Reconstruct the forecast</strong>Choose the most recently published WINDFOR value for each target timestamp with <code>publishTime ≤ cutoff</code>.</span></div><div><b>03</b><span><strong>Compare with observed wind</strong>Join the forecast to <code>FUELHH</code> with <code>fuelType = WIND</code> on identical <code>startTime</code>.</span></div><div><b>04</b><span><strong>Quantify what is covered</strong>Calculate MAE, RMSE, bias and P90 on matched observations only. No interpolation and no zero-fill.</span></div><div><b>05</b><span><strong>Walk-forward backtest (exploratory)</strong>For each day with ≥72 prior matched pairs, compute mean signed error from strictly prior days and subtract it from the raw forecast. Evaluate on the same matched pairs. Label as retrospective only.</span></div><div><b>06</b><span><strong>Risk brief</strong>Summarise the forecast position and prior error tendency visible at the chosen cutoff. No demand, price, or procurement volume is inferred.</span></div><div><b>07</b><span><strong>Scenario workbench</strong>Hold the decision date and cutoff fixed, then compare the raw forecast with expanding and rolling 7-day corrections. Adjustments stay blank below 72 causal pairs.</span></div></div></section><section className="surface methodology"><div className="section-eyebrow">IMPORTANT SCOPE NOTES</div><h2>Not a trading instruction</h2><p>The application estimates national wind forecast performance, not a client's net procurement need. It deliberately does not include customer demand, owned-generation positions, prices, hedges or trading constraints.</p><p>WINDFOR forecasts may represent fewer target timestamps than FUELHH actuals. Coverage counts the exact half-hourly matches; missing forecast intervals are not filled.</p><p>Daily "prior known MAE" uses only earlier matched observations with FUELHH publish timestamps at or before that day's cutoff. It is a historical error indicator, not a guaranteed confidence bound.</p><p>All energy quantities use 0.5 hours × MW per matched settlement period. Net error means forecast minus actual; positive error implies potential wind shortfall, not an independently calculated power purchase order.</p><p>The January error summary is a retrospective diagnostic. It must not be passed off as a prediction interval available to a buyer at an earlier January decision time.</p><p><strong>WINDFOR/FUELHH hourly alignment:</strong> WINDFOR publishes at hourly granularity; FUELHH is half-hourly. Pairs are joined on exact startTime equality — approximately 50% of half-hourly FUELHH slots have no corresponding WINDFOR entry and are excluded from all computations. The bias backtest trains and evaluates only on slots where both datasets provide a value.</p><div className="source-links"><a href="https://data.elexon.co.uk/bmrs/api/v1/datasets/WINDFOR/stream?publishDateTimeFrom=2026-01-01T00%3A00%3A00Z&publishDateTimeTo=2026-02-01T00%3A00%3A00Z" target="_blank" rel="noreferrer">WINDFOR exact source request ↗</a><a href="https://data.elexon.co.uk/bmrs/api/v1/datasets/FUELHH/stream?settlementDateFrom=2026-01-02&settlementDateTo=2026-02-02" target="_blank" rel="noreferrer">FUELHH exact source request ↗</a></div></section></div>}
          <div className="footer-context"><Icon name="shield" size={15} /><span>Source: Elexon BMRS WINDFOR and FUELHH datasets. Latest import: {data.lastSynced ? timeFormat(data.lastSynced) : 'Not applicable'}. No demand or market price series included.</span></div>
        </>}
      </div>
    </main>
  </div>;
}

function DailyTable({ daily, selected, onChoose }) { return <div className="table-scroll"><table><thead><tr><th>Decision day</th><th>Delivery day</th><th className="number">MAE</th><th className="number">Bias</th><th className="number">Coverage</th><th className="number" title="Matched hourly forecasts out of 48 half-hourly settlement periods">Paired (24h/48hh)</th><th></th></tr></thead><tbody>{daily.map(d => <tr key={d.decisionDate} onClick={() => onChoose(d.decisionDate)} className={'click-row ' + (d.decisionDate === selected ? 'selected-row' : '')}><td><strong>{dayFormat(d.decisionDate, { year: 'numeric' })}</strong></td><td>{dayFormat(d.deliveryDate, { year: 'numeric' })}</td><td className="number">{d.maeMw == null ? '—' : `${fmt(d.maeMw)} MW`}</td><td className={'number ' + (d.biasMw > 0 ? 'pos-error' : d.biasMw < 0 ? 'neg-error' : '')}>{d.biasMw == null ? '—' : `${d.biasMw > 0 ? '+' : ''}${fmt(d.biasMw)} MW`}</td><td className="number"><div className="table-coverage"><div style={{ width: `${d.coveragePct}%` }} /></div>{fmt(d.coveragePct, 1)}%</td><td className="number" title={`${d.matched} matched hourly forecasts out of ${d.expected} half-hour settlement periods`}>{d.matched} / {d.expected}</td><td className="end-cell"><Icon name="chevron" size={17} /></td></tr>)}</tbody></table></div>; }
