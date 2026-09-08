import React, { useEffect, useState } from 'react';

type Summary = {
  business_date: string;
  generated_at: string;
  occupancy: { total_rooms: number; occupied_rooms: number; reserved_rooms: number; available_rooms: number; dirty_rooms: number; out_of_order_rooms: number; in_house_reservations: number };
  movement: { arrivals: number; departures: number; no_shows: number };
  revenue: { room: number; food: number; food_service_charge: number; other: number; gross: number };
  payments: { cash?: number; card?: number; bank_transfer?: number; other?: number; total: number };
  outstanding: number;
  expenses: number;
  net_operating: number;
};

export default function NightAuditView({ api }: { api: <T>(path: string, options?: RequestInit) => Promise<T> }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [notes, setNotes] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function load() {
    setMessage('');
    try { setSummary(await api<Summary>('/api/night-audit/preview')); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load daily closing preview'); }
  }
  useEffect(() => { void load(); }, []);

  async function closeDay() {
    if (!summary || !window.confirm(`Close business day ${summary.business_date}? This creates the official daily closing record.`)) return;
    setBusy(true); setMessage('');
    try {
      await api('/api/night-audit/close', { method: 'POST', body: JSON.stringify({ notes: notes || null }) });
      setMessage(`Daily closing completed for ${summary.business_date}.`);
      await load();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close business day'); }
    finally { setBusy(false); }
  }

  const money = (value: number) => Number(value || 0).toFixed(2);
  if (!summary) return <section className="page"><div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div></div>{message && <p className="notice">{message}</p>}<p className="muted">Loading closing pack…</p></section>;

  return <section className="page">
    <div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div><div className="desk-actions"><span className="room-count">Business date {summary.business_date}</span><button className="secondary-button" onClick={() => void load()} disabled={busy}>Refresh</button></div></div>
    {message && <p className="notice">{message}</p>}
    <section className="stats">
      <article className="stat"><span>Occupancy</span><strong>{summary.occupancy.total_rooms ? ((summary.occupancy.occupied_rooms / summary.occupancy.total_rooms) * 100).toFixed(1) : '0.0'}%</strong></article>
      <article className="stat"><span>Arrivals</span><strong>{summary.movement.arrivals}</strong></article>
      <article className="stat"><span>Departures</span><strong>{summary.movement.departures}</strong></article>
      <article className="stat"><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></article>
      <article className="stat"><span>Payments</span><strong>{money(summary.payments.total)}</strong></article>
      <article className="stat"><span>Outstanding</span><strong>{money(summary.outstanding)}</strong></article>
    </section>
    <div className="report-grid" style={{ marginTop: 16 }}>
      <section className="panel"><div className="panel-head"><h2>Occupancy & movement</h2></div><div className="report-list"><div><span>Total rooms</span><strong>{summary.occupancy.total_rooms}</strong></div><div><span>Occupied rooms</span><strong>{summary.occupancy.occupied_rooms}</strong></div><div><span>Reserved rooms</span><strong>{summary.occupancy.reserved_rooms}</strong></div><div><span>Available rooms</span><strong>{summary.occupancy.available_rooms}</strong></div><div><span>Dirty / out of order</span><strong>{summary.occupancy.dirty_rooms} / {summary.occupancy.out_of_order_rooms}</strong></div><div><span>In-house reservations</span><strong>{summary.occupancy.in_house_reservations}</strong></div><div><span>No-shows</span><strong>{summary.movement.no_shows}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue</h2></div><div className="report-list"><div><span>Room revenue</span><strong>{money(summary.revenue.room)}</strong></div><div><span>Food revenue</span><strong>{money(summary.revenue.food)}</strong></div><div><span>Food service charge (10%)</span><strong>{money(summary.revenue.food_service_charge)}</strong></div><div><span>Other revenue</span><strong>{money(summary.revenue.other)}</strong></div><div><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></div><div><span>Expenses</span><strong>{money(summary.expenses)}</strong></div><div><span>Net operating</span><strong>{money(summary.net_operating)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Cashier collection</h2></div><div className="report-list"><div><span>Cash</span><strong>{money(summary.payments.cash || 0)}</strong></div><div><span>Card</span><strong>{money(summary.payments.card || 0)}</strong></div><div><span>Bank transfer</span><strong>{money(summary.payments.bank_transfer || 0)}</strong></div><div><span>Other</span><strong>{money(summary.payments.other || 0)}</strong></div><div><span>Total collected</span><strong>{money(summary.payments.total)}</strong></div></div></section>
      <section className="panel form-panel"><div className="panel-head"><h2>Close business day</h2><span>Admin</span></div><p className="muted">Record any handover notes for Head Office before locking the day’s operational closing record.</p><label>Closing notes<textarea rows={5} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Cashier variance, pending follow-up, maintenance notes, Head Office comments…" /></label><button className="primary-button" disabled={busy} onClick={() => void closeDay()}>{busy ? 'Closing…' : 'Complete daily closing'}</button></section>
    </div>
  </section>;
}
