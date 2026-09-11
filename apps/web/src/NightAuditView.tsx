import React, { useEffect, useState } from 'react';

type TrialBalanceAccount = { account: string; debit: number; credit: number; net: number };
type PaymentRow = { method: string; received: number; refunded: number; net: number };
type Finance = { status: string; ledger_balanced: boolean; total_debits: number; total_credits: number; revenue_difference: number; cash_difference: number; ledger_transactions: number; trial_balance: { balanced: boolean; total_debit: number; total_credit: number; accounts: TrialBalanceAccount[] }; payment_reconciliation: { received_total: number; refunded_total: number; net_total: number; methods: PaymentRow[] }; revenue_reconciliation: { ledger_total: number; operational_total: number; difference: number; accounts: { account: string; amount: number }[]; total: number } };
type Summary = { business_date: string; generated_at: string; posting_open: boolean; occupancy: { total_rooms: number; occupied_rooms: number; reserved_rooms: number; available_rooms: number; dirty_rooms: number; out_of_order_rooms: number; in_house_reservations: number }; movement: { arrivals: number; departures: number; no_shows: number }; revenue: { room: number; food: number; food_service_charge: number; other: number; gross: number }; payments: { cash?: number; card?: number; bank_transfer?: number; other?: number; total: number }; outstanding: number; expenses: number; net_operating: number; finance: Finance };
type Pack = { report: Summary; closing?: { notes?: string | null; closed_by: string; closed_at: string } };
type Props = { api: <T>(path: string, options?: RequestInit) => Promise<T> };
const money = (value: number) => Number(value || 0).toFixed(2);
const today = () => new Date().toISOString().slice(0, 10);

export default function NightAuditView({ api }: Props) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [reportDate, setReportDate] = useState(today());
  const [currentDate, setCurrentDate] = useState(today());
  const [notes, setNotes] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [archive, setArchive] = useState<Pack | null>(null);

  async function loadCurrent() {
    setMessage('');
    try {
      const result = await api<Summary>('/api/night-audit/preview');
      setSummary(result); setCurrentDate(result.business_date); setReportDate(result.business_date); setArchive(null);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load daily closing preview'); }
  }
  async function loadDate(value: string) {
    setReportDate(value); setMessage(''); setArchive(null);
    if (value === currentDate) return loadCurrent();
    setBusy(true);
    try {
      const pack = await api<Pack>(`/api/night-audit/pack/${value}/daily-closing.json`);
      setArchive(pack); setSummary({ ...pack.report, posting_open: false }); setNotes(pack.closing?.notes || '');
    } catch (err) { setSummary(null); setMessage(err instanceof Error ? err.message : `No archived daily closing found for ${value}`); }
    finally { setBusy(false); }
  }
  useEffect(() => { void loadCurrent(); }, []);

  async function closeDay() {
    if (!summary || !summary.posting_open || archive) return;
    if (summary.finance.status !== 'balanced') { setMessage('Financial controls are not balanced. Resolve the reconciliation before closing the business date.'); return; }
    if (!window.confirm(`Close business day ${summary.business_date}? This locks the period, creates the closing pack and advances the business date.`)) return;
    setBusy(true); setMessage('');
    try {
      const result = await api<any>('/api/night-audit/close', { method: 'POST', body: JSON.stringify({ notes: notes || null }) });
      setSummary({ ...result.summary, posting_open: false }); setArchive({ report: result.summary, closing: { notes: notes || null, closed_by: result.closed_by, closed_at: result.closed_at } }); setReportDate(result.business_date); setCurrentDate(result.next_business_date); setMessage(`Daily closing completed for ${result.business_date}. Next business date is ${result.next_business_date}.`);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close business day'); await loadCurrent(); }
    finally { setBusy(false); }
  }
  const download = (date: string, filename: string) => window.open(`/api/night-audit/pack/${date}/${filename}`, '_blank', 'noopener,noreferrer');
  if (!summary) return <section className="page"><div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div></div>{message && <p className="notice">{message}</p>}<p className="muted">No archived closing pack is available for the selected date.</p></section>;
  const f = summary.finance;
  return <section className="page">
    <div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div><div className="desk-actions"><label className="muted">Report date<input type="date" value={reportDate} onChange={e => void loadDate(e.target.value)} disabled={busy} /></label><button className="secondary-button" onClick={() => void loadCurrent()} disabled={busy}>Refresh current</button></div></div>
    {message && <p className="notice">{message}</p>}
    <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><p className="muted">{archive ? 'Archived Head Office pack' : 'Current business date'}</p><h2>Daily Closing {summary.business_date}</h2></div><span>{archive ? `Closed by ${archive.closing?.closed_by || 'admin'}` : summary.posting_open ? 'Open' : 'Closed'}</span></div><div className="desk-actions"><button className="primary-button" onClick={() => download(summary.business_date, 'daily-closing.pdf')}>Download / Print PDF</button><button className="secondary-button" onClick={() => download(summary.business_date, 'daily-closing.xlsx')}>Download Excel</button><button className="secondary-button" onClick={() => download(summary.business_date, 'daily-closing.json')}>Download JSON</button></div><p className="muted" style={{ marginTop: 10 }}>Closing packs are stored locally by business date, so refreshing or moving to the next business date does not lose the previous day's report.</p></section>
    <section className="stats"><article className="stat"><span>Occupancy</span><strong>{summary.occupancy.total_rooms ? ((summary.occupancy.occupied_rooms / summary.occupancy.total_rooms) * 100).toFixed(1) : '0.0'}%</strong></article><article className="stat"><span>Arrivals</span><strong>{summary.movement.arrivals}</strong></article><article className="stat"><span>Departures</span><strong>{summary.movement.departures}</strong></article><article className="stat"><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></article><article className="stat"><span>Payments</span><strong>{money(summary.payments.total)}</strong></article><article className="stat"><span>Outstanding</span><strong>{money(summary.outstanding)}</strong></article></section>
    <div className="report-grid" style={{ marginTop: 16 }}>
      <section className="panel"><div className="panel-head"><h2>Occupancy & movement</h2></div><div className="report-list"><div><span>Total rooms</span><strong>{summary.occupancy.total_rooms}</strong></div><div><span>Occupied rooms</span><strong>{summary.occupancy.occupied_rooms}</strong></div><div><span>Reserved rooms</span><strong>{summary.occupancy.reserved_rooms}</strong></div><div><span>Available rooms</span><strong>{summary.occupancy.available_rooms}</strong></div><div><span>Dirty / out of order</span><strong>{summary.occupancy.dirty_rooms} / {summary.occupancy.out_of_order_rooms}</strong></div><div><span>In-house reservations</span><strong>{summary.occupancy.in_house_reservations}</strong></div><div><span>No-shows</span><strong>{summary.movement.no_shows}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue</h2></div><div className="report-list"><div><span>Room revenue</span><strong>{money(summary.revenue.room)}</strong></div><div><span>Food revenue</span><strong>{money(summary.revenue.food)}</strong></div><div><span>Food service charge (10%)</span><strong>{money(summary.revenue.food_service_charge)}</strong></div><div><span>Other revenue</span><strong>{money(summary.revenue.other)}</strong></div><div><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></div><div><span>Expenses</span><strong>{money(summary.expenses)}</strong></div><div><span>Net operating</span><strong>{money(summary.net_operating)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Cashier collection</h2></div><div className="report-list"><div><span>Cash</span><strong>{money(summary.payments.cash || 0)}</strong></div><div><span>Card</span><strong>{money(summary.payments.card || 0)}</strong></div><div><span>Bank transfer</span><strong>{money(summary.payments.bank_transfer || 0)}</strong></div><div><span>Other</span><strong>{money(summary.payments.other || 0)}</strong></div><div><span>Total collected</span><strong>{money(summary.payments.total)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Financial controls</h2><span>{f.status === 'balanced' ? 'Balanced' : 'Review required'}</span></div><div className="report-list"><div><span>Ledger transactions</span><strong>{f.ledger_transactions}</strong></div><div><span>Debits / credits</span><strong>{money(f.total_debits)} / {money(f.total_credits)}</strong></div><div><span>Trial balance</span><strong>{f.trial_balance.balanced ? 'Balanced' : 'Review'}</strong></div><div><span>Revenue difference</span><strong>{money(f.revenue_difference)}</strong></div><div><span>Cash difference</span><strong>{money(f.cash_difference)}</strong></div><div><span>Posting period</span><strong>{summary.posting_open ? 'Open' : 'Closed'}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Trial balance</h2><span>{f.trial_balance.balanced ? 'Balanced' : 'Review required'}</span></div><div className="report-list">{f.trial_balance.accounts.length ? f.trial_balance.accounts.map(row => <div key={row.account}><span>{row.account}</span><strong>D {money(row.debit)} · C {money(row.credit)}</strong></div>) : <div><span>No ledger activity</span><strong>0.00</strong></div>}<div><span>Total debit</span><strong>{money(f.trial_balance.total_debit)}</strong></div><div><span>Total credit</span><strong>{money(f.trial_balance.total_credit)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Payment reconciliation</h2><span>Net {money(f.payment_reconciliation.net_total)}</span></div><div className="report-list">{f.payment_reconciliation.methods.length ? f.payment_reconciliation.methods.map(row => <div key={row.method}><span>{row.method}</span><strong>+{money(row.received)} / -{money(row.refunded)}</strong></div>) : <div><span>No payment activity</span><strong>0.00</strong></div>}<div><span>Received</span><strong>{money(f.payment_reconciliation.received_total)}</strong></div><div><span>Refunded</span><strong>{money(f.payment_reconciliation.refunded_total)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue reconciliation</h2><span>{money(f.revenue_reconciliation.difference)} difference</span></div><div className="report-list"><div><span>Ledger revenue</span><strong>{money(f.revenue_reconciliation.ledger_total)}</strong></div><div><span>Operational folio charges</span><strong>{money(f.revenue_reconciliation.operational_total)}</strong></div><div><span>Revenue report total</span><strong>{money(f.revenue_reconciliation.total)}</strong></div></div></section>
      {!archive && <section className="panel form-panel"><div className="panel-head"><h2>Close business day</h2><span>Admin only</span></div><p className="muted">Close only after all ledger, trial balance, revenue and payment controls are balanced.</p><label>Closing notes<textarea rows={5} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Cashier variance, pending follow-up, Head Office comments..." /></label><button className="primary-button" onClick={() => void closeDay()} disabled={busy || !summary.posting_open || f.status !== 'balanced'}>{busy ? 'Closing...' : f.status !== 'balanced' ? 'Resolve reconciliation first' : 'Complete daily closing'}</button></section>}
    </div>
  </section>;
}
