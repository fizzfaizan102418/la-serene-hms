import React, { useEffect, useState } from 'react';
import './reports.css';
import NightAuditView from './NightAuditView';

type ReportsApi = <T>(path: string, options?: RequestInit) => Promise<T>;
type Report = {
  from_date: string;
  to_date: string;
  period_days: number;
  rooms: { total: number; operational: number; available_room_nights: number; booked_room_nights: number; occupied_room_nights: number; occupancy_rate: number };
  operations: { scheduled_arrivals: number; scheduled_departures: number; actual_check_ins: number; actual_check_outs: number; checked_in_guests: number; completed_stays: number; stays_overlapping_period: number; legacy_lifecycle_records: number };
  revenue: { gross: number; discounts: number; net: number; payments_received: number; outstanding_balance: number };
  payment_breakdown: { method: string; amount: number }[];
  top_guests: { guest_name: string; stays: number }[];
};
const today = () => new Date().toISOString().slice(0, 10);
const money = (value: number) => value.toFixed(2);

export default function ReportsView({ api }: { api: ReportsApi }) {
  const [mode, setMode] = useState<'reports' | 'night-audit'>('reports');
  const [fromDate, setFromDate] = useState(today());
  const [toDate, setToDate] = useState(today());
  const [report, setReport] = useState<Report | null>(null);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(false);

  async function load() {
    setMessage(''); setLoading(true);
    try {
      if (toDate < fromDate) throw new Error('To date must be on or after from date.');
      setReport(await api<Report>(`/api/reports/summary?from_date=${fromDate}&to_date=${toDate}`));
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load report'); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);

  if (mode === 'night-audit') return <section className="page"><div className="page-heading"><div><p className="muted">Management controls</p><h2>Daily Closing</h2></div><button className="secondary-button" onClick={() => setMode('reports')}>← Reports & Analytics</button></div><NightAuditView api={api}/></section>;

  return <section className="page">
    <div className="page-heading"><div><p className="muted">Management overview</p><h2>Reports & Analytics</h2></div><div className="desk-actions"><button className="secondary-button" onClick={() => setMode('night-audit')}>Night Audit / Daily Closing</button><button className="secondary-button" onClick={load} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh report'}</button></div></div>
    <div className="panel report-filters"><label>From<input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} /></label><label>To<input type="date" value={toDate} onChange={e => setToDate(e.target.value)} /></label><button className="primary-button" onClick={load} disabled={loading}>Run report</button></div>
    {message && <p className="notice">{message}</p>}
    {report && <><section className="report-stat-grid"><article className="stat"><span>Occupancy</span><strong>{report.rooms.occupancy_rate.toFixed(1)}%</strong></article><article className="stat"><span>Net revenue</span><strong>{money(report.revenue.net)}</strong></article><article className="stat"><span>Payments received</span><strong>{money(report.revenue.payments_received)}</strong></article><article className="stat"><span>Outstanding</span><strong>{money(report.revenue.outstanding_balance)}</strong></article><article className="stat"><span>Actual check-ins</span><strong>{report.operations.actual_check_ins}</strong></article><article className="stat"><span>Actual check-outs</span><strong>{report.operations.actual_check_outs}</strong></article></section>
      <div className="report-grid"><section className="panel"><div className="panel-head"><h2>Room performance</h2><span>{report.period_days} day(s)</span></div><div className="report-list"><div><span>Total rooms</span><strong>{report.rooms.total}</strong></div><div><span>Operational rooms</span><strong>{report.rooms.operational}</strong></div><div><span>Available room-nights</span><strong>{report.rooms.available_room_nights}</strong></div><div><span>Booked room-nights</span><strong>{report.rooms.booked_room_nights}</strong></div><div><span>Occupied room-nights</span><strong>{report.rooms.occupied_room_nights}</strong></div></div></section>
        <section className="panel"><div className="panel-head"><h2>Guest movement</h2><span>{report.operations.stays_overlapping_period} overlapping stays</span></div><div className="report-list"><div><span>Scheduled arrivals</span><strong>{report.operations.scheduled_arrivals}</strong></div><div><span>Scheduled departures</span><strong>{report.operations.scheduled_departures}</strong></div><div><span>Actual check-ins</span><strong>{report.operations.actual_check_ins}</strong></div><div><span>Actual check-outs</span><strong>{report.operations.actual_check_outs}</strong></div><div><span>Completed stays</span><strong>{report.operations.completed_stays}</strong></div></div>{report.operations.legacy_lifecycle_records > 0 && <p className="report-note">{report.operations.legacy_lifecycle_records} legacy reservation record(s) do not have lifecycle timestamps and use compatibility handling.</p>}</section>
        <section className="panel"><div className="panel-head"><h2>Revenue</h2><span>{report.from_date} → {report.to_date}</span></div><div className="report-list"><div><span>Gross charges</span><strong>{money(report.revenue.gross)}</strong></div><div><span>Discounts</span><strong>{money(report.revenue.discounts)}</strong></div><div><span>Net charges</span><strong>{money(report.revenue.net)}</strong></div><div><span>Payments received</span><strong>{money(report.revenue.payments_received)}</strong></div><div><span>Outstanding balance</span><strong>{money(report.revenue.outstanding_balance)}</strong></div></div></section>
        <section className="panel"><div className="panel-head"><h2>Payment mix</h2><span>{report.payment_breakdown.length} methods</span></div>{report.payment_breakdown.length ? <div className="report-list">{report.payment_breakdown.map(row => <div key={row.method}><span>{row.method.replace(/_/g, ' ')}</span><strong>{money(row.amount)}</strong></div>)}</div> : <p className="muted">No payments recorded for this period.</p>}</section>
        <section className="panel"><div className="panel-head"><h2>Top guests</h2><span>By stays</span></div>{report.top_guests.length ? <div className="report-list">{report.top_guests.map(row => <div key={row.guest_name}><span>{row.guest_name}</span><strong>{row.stays}</strong></div>)}</div> : <p className="muted">No guest stays in this period.</p>}</section>
      </div></>}
  </section>;
}
