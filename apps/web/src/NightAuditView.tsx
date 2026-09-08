import React, { useEffect, useState } from 'react';

type Finance = {
  status: string;
  ledger_balanced: boolean;
  total_debits: number;
  total_credits: number;
  revenue_difference: number;
  cash_difference: number;
  ledger_transactions: number;
};
type Summary = {
  business_date: string;
  generated_at: string;
  posting_open: boolean;
  occupancy: { total_rooms: number; occupied_rooms: number; reserved_rooms: number; available_rooms: number; dirty_rooms: number; out_of_order_rooms: number; in_house_reservations: number };
  movement: { arrivals: number; departures: number; no_shows: number };
  revenue: { room: number; food: number; food_service_charge: number; other: number; gross: number };
  payments: { cash?: number; card?: number; bank_transfer?: number; other?: number; total: number };
  outstanding: number;
  expenses: number;
  net_operating: number;
  finance: Finance;
};
type CloseResponse = { status: string; business_date: string; next_business_date: string; summary: Summary; pack: { pdf: string; xlsx: string; json: string }; download_urls: { pdf: string; xlsx: string; json: string }; closed_by: string; closed_at: string };

export default function NightAuditView({ api }: { api: <T>(path: string, options?: RequestInit) => Promise<T> }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [notes, setNotes] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [pack, setPack] = useState<CloseResponse | null>(null);

  async function load() {
    setMessage('');
    try {
      setSummary(await api<Summary>('/api/night-audit/preview'));
      setPack(null);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to load daily closing preview');
    }
  }

  useEffect(() => { void load(); }, []);

  async function closeDay() {
    if (!summary || !summary.posting_open) return;
    if (!summary.finance || summary.finance.status !== 'balanced') {
      setMessage('Financial controls are not balanced. Resolve the reconciliation before closing the business date.');
      return;
    }
    if (!window.confirm(`Close business day ${summary.business_date}? This locks the official financial period, creates the closing pack, and advances the business date.`)) return;
    setBusy(true);
    setMessage('');
    try {
      const result = await api<CloseResponse>('/api/night-audit/close', { method: 'POST', body: JSON.stringify({ notes: notes || null }) });
      setPack(result);
      setSummary(result.summary);
      setMessage(`Daily closing completed for ${result.business_date}. Next business date is ${result.next_business_date}.`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to close business day');
      await load();
    } finally {
      setBusy(false);
    }
  }

  const money = (value: number) => Number(value || 0).toFixed(2);
  const financeBalanced = summary.finance.status === 'balanced';

  if (!summary) return <section className="page"><div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div></div>{message && <p className="notice">{message}</p>}<p className="muted">Loading closing pack...</p></section>;

  return <section className="page">
    <div className="page-heading">
      <div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div>
      <div className="desk-actions"><span className="room-count">Business date {summary.business_date}</span><button className="secondary-button" onClick={() => void load()} disabled={busy}>Refresh</button></div>
    </div>
    {message && <p className="notice">{message}</p>}
    {pack && <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><p className="muted">Archived Head Office pack</p><h2>Daily Closing {pack.business_date}</h2></div><span>Closed by {pack.closed_by}</span></div><div className="desk-actions"><button className="primary-button" onClick={() => window.open(pack.download_urls.pdf, '_blank', 'noopener,noreferrer')}>Download / Print PDF</button><button className="secondary-button" onClick={() => window.open(pack.download_urls.xlsx, '_blank', 'noopener,noreferrer')}>Download Excel</button><button className="secondary-button" onClick={() => window.open(pack.download_urls.json, '_blank', 'noopener,noreferrer')}>Download JSON</button></div><p className="muted" style={{ marginTop: 10 }}>Archived locally under <code>data/daily_closing/{pack.business_date}</code> for offline transfer to Head Office.</p></section>}
    <section className="stats"><article className="stat"><span>Occupancy</span><strong>{summary.occupancy.total_rooms ? ((summary.occupancy.occupied_rooms / summary.occupancy.total_rooms) * 100).toFixed(1) : '0.0'}%</strong></article><article className="stat"><span>Arrivals</span><strong>{summary.movement.arrivals}</strong></article><article className="stat"><span>Departures</span><strong>{summary.movement.departures}</strong></article><article className="stat"><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></article><article className="stat"><span>Payments</span><strong>{money(summary.payments.total)}</strong></article><article className="stat"><span>Outstanding</span><strong>{money(summary.outstanding)}</strong></article></section>
    <div className="report-grid" style={{ marginTop: 16 }}>
      <section className="panel"><div className="panel-head"><h2>Occupancy & movement</h2></div><div className="report-list"><div><span>Total rooms</span><strong>{summary.occupancy.total_rooms}</strong></div><div><span>Occupied rooms</span><strong>{summary.occupancy.occupied_rooms}</strong></div><div><span>Reserved rooms</span><strong>{summary.occupancy.reserved_rooms}</strong></div><div><span>Available rooms</span><strong>{summary.occupancy.available_rooms}</strong></div><div><span>Dirty / out of order</span><strong>{summary.occupancy.dirty_rooms} / {summary.occupancy.out_of_order_rooms}</strong></div><div><span>In-house reservations</span><strong>{summary.occupancy.in_house_reservations}</strong></div><div><span>No-shows</span><strong>{summary.movement.no_shows}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue</h2></div><div className="report-list"><div><span>Room revenue</span><strong>{money(summary.revenue.room)}</strong></div><div><span>Food revenue</span><strong>{money(summary.revenue.food)}</strong></div><div><span>Food service charge (10%)</span><strong>{money(summary.revenue.food_service_charge)}</strong></div><div><span>Other revenue</span><strong>{money(summary.revenue.other)}</strong></div><div><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></div><div><span>Expenses</span><strong>{money(summary.expenses)}</strong></div><div><span>Net operating</span><strong>{money(summary.net_operating)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Cashier collection</h2></div><div className="report-list"><div><span>Cash</span><strong>{money(summary.payments.cash || 0)}</strong></div><div><span>Card</span><strong>{money(summary.payments.card || 0)}</strong></div><div><span>Bank transfer</span><strong>{money(summary.payments.bank_transfer || 0)}</strong></div><div><span>Other</span><strong>{money(summary.payments.other || 0)}</strong></div><div><span>Total collected</span><strong>{money(summary.payments.total)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Financial controls</h2><span>{financeBalanced ? 'Balanced' : 'Review required'}</span></div><div className="report-list"><div><span>Ledger transactions</span><strong>{summary.finance.ledger_transactions}</strong></div><div><span>Debits</span><strong>{money(summary.finance.total_debits)}</strong></div><div><span>Credits</span><strong>{money(summary.finance.total_credits)}</strong></div><div><span>Revenue difference</span><strong>{money(summary.finance.revenue_difference)}</strong></div><div><span>Cash difference</span><strong>{money(summary.finance.cash_difference)}</strong></div><div><span>Posting period</span><strong>{summary.posting_open ? 'Open' : 'Closed'}</strong></div></div></section>
      <section className="panel form-panel"><div className="panel-head"><h2>Close business day</h2><span>Admin</span></div><p className="muted">Night Audit closes the controlled business date only after the ledger and operational reconciliation is balanced.</p><label>Closing notes<textarea rows={5} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Cashier variance, pending follow-up, maintenance notes, Head Office comments..." /></label><button className="primary-button" onClick={() => void closeDay()} disabled={busy || !summary.posting_open || !financeBalanced}>{busy ? 'Closing...' : !summary.posting_open ? 'Business day closed' : !financeBalanced ? 'Resolve reconciliation first' : 'Complete daily closing'}</button></section>
    </div>
  </section>;
}
