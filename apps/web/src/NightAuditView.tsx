import React, { useEffect, useState } from 'react';

type TrialBalanceAccount = { account: string; debit: number; credit: number; net: number };
type PaymentRow = { method: string; received: number; refunded: number; net: number };
type Finance = { status: string; ledger_balanced: boolean; total_debits: number; total_credits: number; revenue_difference: number; cash_difference: number; ledger_transactions: number; trial_balance: { balanced: boolean; total_debit: number; total_credit: number; accounts: TrialBalanceAccount[] }; payment_reconciliation: { received_total: number; refunded_total: number; net_total: number; methods: PaymentRow[] }; revenue_reconciliation: { ledger_total: number; operational_total: number; difference: number; accounts: { account: string; amount: number }[]; total: number } };
type PreCloseItem = { stay_id: number; reservation_id: number; folio_id: number; room_id: number; room: string; gross_amount: number; discount_amount: number; amount: number; description: string };
type PreClose = { business_date: string; opening: { cash: number; guest_receivables: number; outstanding: number }; activity: { room_revenue: number; payments_received: number; cash_received: number; expenses: number; ledger_transactions: number; guest_receivables_delta: number }; pending_night_audit: { room_charges_count: number; room_charges_total: number; items: PreCloseItem[] }; projected_close: { room_revenue: number; cash: number; guest_receivables: number; outstanding: number; gross_revenue: number }; controls: { trial_balance: string; revenue_difference: number; cash_difference: number } };
type Summary = { business_date: string; generated_at: string; posting_open: boolean; occupancy: { total_rooms: number; occupied_rooms: number; reserved_rooms: number; available_rooms: number; dirty_rooms: number; out_of_order_rooms: number; in_house_reservations: number }; movement: { arrivals: number; departures: number; no_shows: number }; revenue: { room: number; food: number; food_service_charge: number; other: number; gross: number }; payments: { cash?: number; card?: number; bank_transfer?: number; other?: number; total: number }; outstanding: number; expenses: number; net_operating: number; finance: Finance; pre_close?: PreClose };
type Pack = { report: Summary; closing?: { notes?: string | null; closed_by: string; closed_at: string } };
type Props = { api: <T>(path: string, options?: RequestInit) => Promise<T> };
const TOKEN_KEY = 'la_serene_access_token';
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
  const [confirmingClose, setConfirmingClose] = useState(false);

  async function loadCurrent() {
    setMessage('');
    try { const result = await api<Summary>('/api/night-audit/preview'); setSummary(result); setCurrentDate(result.business_date); setReportDate(result.business_date); setArchive(null); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load daily closing preview'); }
  }
  async function loadDate(value: string) {
    setReportDate(value); setMessage(''); setArchive(null);
    if (value === currentDate) return loadCurrent();
    setBusy(true);
    try { const pack = await api<Pack>(`/api/night-audit/pack/${value}/daily-closing.json`); setArchive(pack); setSummary({ ...pack.report, posting_open: false }); setNotes(pack.closing?.notes || ''); }
    catch (err) { setSummary(null); setMessage(err instanceof Error ? err.message : `No archived daily closing found for ${value}`); }
    finally { setBusy(false); }
  }
  useEffect(() => { void loadCurrent(); }, []);

  function escapePrintHtml(value: unknown) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function printDailyClosing() {
    if (!summary) return;
    const status = summary.finance.status === 'balanced' ? 'BALANCED' : 'REVIEW REQUIRED';
    const statusClass = summary.finance.status === 'balanced' ? 'balanced' : 'review';
    const rows = [
      ['Total rooms', summary.occupancy.total_rooms], ['Occupied rooms', summary.occupancy.occupied_rooms], ['Reserved rooms', summary.occupancy.reserved_rooms], ['Available rooms', summary.occupancy.available_rooms],
      ['Dirty rooms', summary.occupancy.dirty_rooms], ['Out of order rooms', summary.occupancy.out_of_order_rooms], ['In-house reservations', summary.occupancy.in_house_reservations], ['No-shows', summary.movement.no_shows],
    ].map(([label,value]) => `<tr><td>${escapePrintHtml(label)}</td><td class="num">${escapePrintHtml(value)}</td></tr>`).join('');
    const revenue = [
      ['Room revenue', summary.revenue.room], ['Food revenue', summary.revenue.food], ['Food service charge', summary.revenue.food_service_charge], ['Other revenue', summary.revenue.other], ['Gross revenue', summary.revenue.gross], ['Expenses', summary.expenses], ['Net operating', summary.net_operating],
    ].map(([label,value]) => `<tr><td>${escapePrintHtml(label)}</td><td class="num">PKR ${money(Number(value))}</td></tr>`).join('');
    const payments = [
      ['Cash', summary.payments.cash || 0], ['Card', summary.payments.card || 0], ['Bank transfer', summary.payments.bank_transfer || 0], ['Other', summary.payments.other || 0], ['Total received', summary.payments.total],
    ].map(([label,value]) => `<tr><td>${escapePrintHtml(label)}</td><td class="num">PKR ${money(Number(value))}</td></tr>`).join('');
    const controls = [
      ['Ledger transactions', f.ledger_transactions], ['Total debits', f.trial_balance.total_debit], ['Total credits', f.trial_balance.total_credit], ['Revenue difference', f.revenue_difference], ['Cash difference', f.cash_difference],
    ].map(([label,value]) => `<tr><td>${escapePrintHtml(label)}</td><td class="num">PKR ${money(Number(value))}</td></tr>`).join('');
    const outstanding = Number(summary.outstanding || 0);
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>La Serene Hotel · Daily Closing · ${escapePrintHtml(summary.business_date)}</title>
<style>
@page{size:A4;margin:13mm 13mm 15mm}*{box-sizing:border-box}body{margin:0;color:#1E293B;background:#fff;font:11px/1.45 Arial,Helvetica,sans-serif}.sheet{max-width:184mm;margin:0 auto}
.header{display:flex;justify-content:space-between;gap:20px;padding-bottom:13px;border-bottom:2px solid #D4AF37}.brand h1{margin:0;font-size:21px;letter-spacing:.04em}.brand p{margin:3px 0 0;color:#64748B;font-size:10px}.meta{text-align:right}.meta strong{display:block;font-size:14px}.meta span{display:block;color:#64748B;font-size:10px;margin-top:2px}.status{display:inline-flex;margin-top:7px;padding:4px 8px;border-radius:999px;font-size:9px;font-weight:800;letter-spacing:.06em;background:#DCFCE7;color:#166534}.status.review{background:#FEE2E2;color:#991B1B}
.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:14px}.kpi{padding:8px 9px;border:1px solid #E2E8F0;border-radius:8px;background:#F8FAFC}.kpi span{display:block;color:#64748B;font-size:8px;text-transform:uppercase;letter-spacing:.05em}.kpi strong{display:block;margin-top:2px;font-size:12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.section{border:1px solid #E2E8F0;border-radius:9px;padding:10px}.section h2{margin:0 0 7px;font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#475569}.section table{width:100%;border-collapse:collapse}.section td{padding:5px 2px;border-bottom:1px solid #EDF2F7}.section tr:last-child td{border-bottom:0}.num{text-align:right;font-weight:700}.full{grid-column:1/-1}
.alert{margin-top:12px;padding:9px 10px;border-left:3px solid #D97706;background:#FFFBEB;border-radius:6px}.alert strong{color:#92400E}.outstanding{margin-top:12px;padding:10px;border-radius:8px;background:#FEF3C7;color:#92400E;display:flex;justify-content:space-between;gap:10px;font-weight:800}.outstanding.clear{background:#DCFCE7;color:#166534}
.footer{margin-top:18px;padding-top:9px;border-top:1px solid #CBD5E1;color:#64748B;font-size:9px;display:flex;justify-content:space-between;gap:15px}
@media print{body{print-color-adjust:exact;-webkit-print-color-adjust:exact}tr{break-inside:avoid}.section{break-inside:avoid}.grid{break-inside:auto}}
@media(max-width:700px){.header{display:grid}.meta{text-align:left}.summary,.grid{grid-template-columns:1fr}.full{grid-column:auto}}
</style></head><body><main class="sheet">
<header class="header"><div class="brand"><h1>LA SERENE HOTEL</h1><p>Hotel Management System · Night Audit & Daily Closing</p></div><div class="meta"><strong>Business Date · ${escapePrintHtml(summary.business_date)}</strong><span>Generated ${escapePrintHtml(summary.generated_at)}</span><span class="status ${statusClass}">${status}</span></div></header>
<section class="summary"><div class="kpi"><span>Occupancy</span><strong>${summary.occupancy.total_rooms ? ((summary.occupancy.occupied_rooms / summary.occupancy.total_rooms) * 100).toFixed(1) : '0.0'}%</strong></div><div class="kpi"><span>Gross revenue</span><strong>PKR ${money(summary.revenue.gross)}</strong></div><div class="kpi"><span>Payments received</span><strong>PKR ${money(summary.payments.total)}</strong></div><div class="kpi"><span>Outstanding</span><strong>PKR ${money(outstanding)}</strong></div></section>
<div class="grid"><section class="section"><h2>Occupancy & movement</h2><table>${rows}</table></section><section class="section"><h2>Revenue</h2><table>${revenue}</table></section><section class="section"><h2>Payments</h2><table>${payments}</table></section><section class="section"><h2>Financial controls</h2><table>${controls}</table></section>
<section class="section full"><h2>Trial balance accounts</h2><table>${f.trial_balance.accounts.map(row => `<tr><td>${escapePrintHtml(row.account)}</td><td class="num">Debit PKR ${money(row.debit)} · Credit PKR ${money(row.credit)}</td></tr>`).join('') || '<tr><td>No ledger accounts recorded.</td><td class="num">—</td></tr>'}</table></section></div>
<div class="outstanding ${outstanding > 0.005 ? '' : 'clear'}"><span>Open folio balance</span><span>PKR ${money(outstanding)}</span></div>
${outstanding > 0.005 ? '<div class="alert"><strong>Attention:</strong> outstanding guest receivables remain open for this business date. Review the related folios before final hand-off.</div>' : ''}
<footer class="footer"><span>La Serene Hotel · Official daily closing record</span><span>${summary.posting_open ? 'Posting period open' : 'Posting period closed'}</span></footer>
</main></body></html>`;
    const popup = window.open('', '_blank', 'width=900,height=1000');
    if (!popup) { setMessage('Please allow pop-ups to print the daily closing report'); return; }
    popup.document.write(html); popup.document.close(); popup.focus();
    window.setTimeout(() => popup.print(), 250);
  }

  async function closeDay() {
    if (!summary || !summary.posting_open || archive || busy) return;
    if (summary.finance.status !== 'balanced') { setMessage('Financial controls are not balanced. Resolve the reconciliation before closing the business date.'); return; }
    setConfirmingClose(false);
    setBusy(true); setMessage('Closing business day…');
    try { const result = await api<any>('/api/night-audit/close', { method: 'POST', body: JSON.stringify({ notes: notes || null }) }); setSummary({ ...result.summary, posting_open: false }); setArchive({ report: result.summary, closing: { notes: notes || null, closed_by: result.closed_by, closed_at: result.closed_at } }); setReportDate(result.business_date); setCurrentDate(result.next_business_date); setMessage(`Daily closing completed for ${result.business_date}. Next business date is ${result.next_business_date}.`); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close business day'); await loadCurrent(); }
    finally { setBusy(false); }
  }
  async function download(date: string, filename: string) {
    try {
      const token = localStorage.getItem(TOKEN_KEY);
      const response = await fetch(`/api/night-audit/pack/${date}/${filename}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!response.ok) { let detail = `Download failed (${response.status})`; try { detail = (await response.json()).detail || detail; } catch {} throw new Error(detail); }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; document.body.appendChild(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
      setMessage(`${filename} downloaded for business date ${date}.`);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to download closing pack'); }
  }
  if (!summary) return <section className="page night-audit-page"><div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div></div>{message && <p className="notice">{message}</p>}<p className="muted">No archived closing pack is available for the selected date.</p></section>;
  const f = summary.finance;
  const p = summary.pre_close;
  return <section className="page">
    <div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div><div className="desk-actions"><label className="muted">Report date<input type="date" value={reportDate} onChange={e => void loadDate(e.target.value)} disabled={busy} /></label><button className="secondary-button" onClick={() => void loadCurrent()} disabled={busy}>Refresh current</button></div></div>
    {message && <p className="notice">{message}</p>}
    <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><p className="muted">{archive ? 'Archived Head Office pack' : 'Current hotel business date'}</p><h2>{archive ? `Closed business date \u00b7 ${summary.business_date}` : `Closing business date \u00b7 ${summary.business_date}`}</h2></div><span>{archive ? `Closed by ${archive.closing?.closed_by || 'admin'}` : summary.posting_open ? 'Open for closing' : 'Closed'}</span></div><div className="desk-actions"><button className="primary-button" onClick={printDailyClosing}>Print daily closing</button><button className="secondary-button" onClick={() => void download(summary.business_date, 'daily-closing.pdf')}>Download PDF</button><button className="secondary-button" onClick={() => void download(summary.business_date, 'daily-closing.xlsx')}>Download Excel</button><button className="secondary-button" onClick={() => void download(summary.business_date, 'daily-closing.json')}>Download JSON</button></div><p className="muted" style={{ marginTop: 10 }}>{archive ? `This closing pack belongs to hotel business date ${summary.business_date}. The next hotel business date is ${currentDate}.` : `The hotel is currently operating on business date ${summary.business_date}. Completing daily closing will close ${summary.business_date} and advance the hotel to the next business date.`}</p></section>
    {!archive && p && <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><p className="muted">Real pre-close reconciliation</p><h2>What will happen when you close</h2></div><span>{p.pending_night_audit.room_charges_count} room night(s) pending</span></div><p className="muted">This is a preview only. Nothing below is posted until you complete Daily Closing.</p><div className="report-grid"><section><div className="panel-head"><h3>Opening / carry-forward</h3><span>Before {summary.business_date}</span></div><div className="report-list"><div><span>Cash</span><strong>{money(p.opening.cash)}</strong></div><div><span>Guest receivables</span><strong>{money(p.opening.guest_receivables)}</strong></div><div><span>Opening outstanding</span><strong>{money(p.opening.outstanding)}</strong></div></div></section><section><div className="panel-head"><h3>Today's activity</h3><span>{p.activity.ledger_transactions} ledger transaction(s)</span></div><div className="report-list"><div><span>Room revenue already posted</span><strong>{money(p.activity.room_revenue)}</strong></div><div><span>Payments received</span><strong>{money(p.activity.payments_received)}</strong></div><div><span>Cash received / net cash movement</span><strong>{money(p.activity.cash_received)}</strong></div><div><span>Expenses</span><strong>{money(p.activity.expenses)}</strong></div><div><span>Receivables movement</span><strong>{money(p.activity.guest_receivables_delta)}</strong></div></div></section><section><div className="panel-head"><h3>Tonight's room charge</h3><span>Posted automatically at closing</span></div><div className="report-list">{p.pending_night_audit.items.length ? p.pending_night_audit.items.map(item => <div key={item.stay_id}><span>Room {item.room}</span><strong>+PKR {money(item.amount)}</strong></div>) : <div><span>No automatic room-night charges pending</span><strong>0.00</strong></div>}<div><span>Total room charge to post</span><strong>PKR {money(p.pending_night_audit.room_charges_total)}</strong></div></div></section><section><div className="panel-head"><h3>Projected closing position</h3><span>After pending postings</span></div><div className="report-list"><div><span>Room revenue</span><strong>{money(p.projected_close.room_revenue)}</strong></div><div><span>Gross revenue</span><strong>{money(p.projected_close.gross_revenue)}</strong></div><div><span>Cash</span><strong>{money(p.projected_close.cash)}</strong></div><div><span>Guest receivables</span><strong>{money(p.projected_close.guest_receivables)}</strong></div><div><span>Outstanding</span><strong>{money(p.projected_close.outstanding)}</strong></div></div></section></div><div className="report-list" style={{ marginTop: 12 }}><div><span>Pre-close financial controls</span><strong>{p.controls.trial_balance === 'balanced' && p.controls.revenue_difference === 0 && p.controls.cash_difference === 0 ? 'Balanced' : 'Review required'}</strong></div><div><span>Revenue difference</span><strong>{money(p.controls.revenue_difference)}</strong></div><div><span>Cash difference</span><strong>{money(p.controls.cash_difference)}</strong></div></div></section>}
    {!archive && <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><p className="muted">Current business-date activity</p><h2>What is already posted</h2></div><span>{summary.posting_open ? 'Live' : 'Closed'}</span></div><p className="muted">Room charges for the night are posted automatically during Daily Closing. Staff do not enter these charges manually.</p><div className="report-list"><div><span>Room revenue already posted today</span><strong>{money(summary.revenue.room)}</strong></div><div><span>Payments recorded today</span><strong>{money(summary.payments.total)}</strong></div><div><span>Open folio balance across active folios</span><strong>{money(summary.outstanding)}</strong></div><div><span>Active in-house reservations</span><strong>{summary.occupancy.in_house_reservations}</strong></div><div><span>Current ledger transactions</span><strong>{f.ledger_transactions}</strong></div><div><span>Current ledger status</span><strong>{f.status === 'balanced' ? 'Balanced' : 'Review required'}</strong></div></div></section>}
    <section className="stats"><article className="stat"><span>Occupancy</span><strong>{summary.occupancy.total_rooms ? ((summary.occupancy.occupied_rooms / summary.occupancy.total_rooms) * 100).toFixed(1) : '0.0'}%</strong></article><article className="stat"><span>Arrivals</span><strong>{summary.movement.arrivals}</strong></article><article className="stat"><span>Departures</span><strong>{summary.movement.departures}</strong></article><article className="stat"><span>Today's gross revenue</span><strong>{money(summary.revenue.gross)}</strong></article><article className="stat"><span>Today's payments</span><strong>{money(summary.payments.total)}</strong></article><article className="stat"><span>Open folio balance</span><strong>{money(summary.outstanding)}</strong></article></section>
    <div className="report-grid" style={{ marginTop: 16 }}>
      <section className="panel"><div className="panel-head"><h2>Occupancy & movement</h2><span>Business date</span></div><div className="report-list"><div><span>Total rooms</span><strong>{summary.occupancy.total_rooms}</strong></div><div><span>Occupied rooms</span><strong>{summary.occupancy.occupied_rooms}</strong></div><div><span>Reserved rooms</span><strong>{summary.occupancy.reserved_rooms}</strong></div><div><span>Available rooms</span><strong>{summary.occupancy.available_rooms}</strong></div><div><span>Dirty / out of order</span><strong>{summary.occupancy.dirty_rooms} / {summary.occupancy.out_of_order_rooms}</strong></div><div><span>In-house reservations</span><strong>{summary.occupancy.in_house_reservations}</strong></div><div><span>No-shows</span><strong>{summary.movement.no_shows}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue — current date</h2><span>Recorded activity</span></div><div className="report-list"><div><span>Room revenue already posted</span><strong>{money(summary.revenue.room)}</strong></div><div><span>Food revenue</span><strong>{money(summary.revenue.food)}</strong></div><div><span>Food service charge (10%)</span><strong>{money(summary.revenue.food_service_charge)}</strong></div><div><span>Other revenue</span><strong>{money(summary.revenue.other)}</strong></div><div><span>Gross revenue</span><strong>{money(summary.revenue.gross)}</strong></div><div><span>Expenses</span><strong>{money(summary.expenses)}</strong></div><div><span>Net operating</span><strong>{money(summary.net_operating)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Payments — current date</h2><span>Recorded activity</span></div><div className="report-list"><div><span>Cash received today</span><strong>{money(summary.payments.cash || 0)}</strong></div><div><span>Card received today</span><strong>{money(summary.payments.card || 0)}</strong></div><div><span>Bank transfer received today</span><strong>{money(summary.payments.bank_transfer || 0)}</strong></div><div><span>Other received today</span><strong>{money(summary.payments.other || 0)}</strong></div><div><span>Total received today</span><strong>{money(summary.payments.total)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Financial controls — current date</h2><span>{f.status === 'balanced' ? 'Balanced' : 'Review required'}</span></div><div className="report-list"><div><span>Ledger transactions today</span><strong>{f.ledger_transactions}</strong></div><div><span>Debits / credits today</span><strong>{money(f.trial_balance.total_debit)} / {money(f.trial_balance.total_credit)}</strong></div><div><span>Trial balance today</span><strong>{f.trial_balance.balanced ? 'Balanced' : 'Review'}</strong></div><div><span>Revenue difference today</span><strong>{money(f.revenue_difference)}</strong></div><div><span>Cash difference today</span><strong>{money(f.cash_difference)}</strong></div><div><span>Posting period</span><strong>{summary.posting_open ? 'Open' : 'Closed'}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Trial balance — current date</h2><span>{f.trial_balance.balanced ? 'Balanced' : 'Review required'}</span></div><div className="report-list">{f.trial_balance.accounts.length ? f.trial_balance.accounts.map(row => <div key={row.account}><span>{row.account}</span><strong>D {money(row.debit)} · C {money(row.credit)}</strong></div>) : <div><span>No current-date ledger activity</span><strong>0.00</strong></div>}<div><span>Total debit today</span><strong>{money(f.trial_balance.total_debit)}</strong></div><div><span>Total credit today</span><strong>{money(f.trial_balance.total_credit)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Payment reconciliation — current date</h2><span>Net {money(f.payment_reconciliation.net_total)}</span></div><div className="report-list">{f.payment_reconciliation.methods.length ? f.payment_reconciliation.methods.map(row => <div key={row.method}><span>{row.method}</span><strong>+{money(row.received)} / -{money(row.refunded)}</strong></div>) : <div><span>No current-date payment activity</span><strong>0.00</strong></div>}<div><span>Received today</span><strong>{money(f.payment_reconciliation.received_total)}</strong></div><div><span>Refunded today</span><strong>{money(f.payment_reconciliation.refunded_total)}</strong></div></div></section>
      <section className="panel"><div className="panel-head"><h2>Revenue reconciliation — current date</h2><span>{money(f.revenue_reconciliation.difference)} difference</span></div><div className="report-list"><div><span>Ledger revenue today</span><strong>{money(f.revenue_reconciliation.ledger_total)}</strong></div><div><span>Operational folio charges today</span><strong>{money(f.revenue_reconciliation.operational_total)}</strong></div><div><span>Revenue report total today</span><strong>{money(f.revenue_reconciliation.total)}</strong></div></div></section>
      {!archive && summary.posting_open && <section className="panel form-panel"><div className="panel-head"><h2>Close business day</h2><span>Admin only</span></div><p className="muted">Review the pre-close projection above. Night Audit will post only the listed pending room nights, re-run the financial controls, create the immutable closing pack, and then advance the business date.</p><label>Closing notes<textarea rows={5} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Cashier variance, pending follow-up, Head Office comments..." /></label><div className="desk-actions" style={{ alignItems: 'center', marginTop: 8 }}>
        {!confirmingClose ? <button type="button" className="primary-button" onClick={() => { setMessage(''); setConfirmingClose(true); }} disabled={busy || f.status !== 'balanced'}>{f.status !== 'balanced' ? 'Resolve reconciliation first' : 'Complete daily closing'}</button> : <>
          <span className="muted">Confirm closing {summary.business_date}? This will post the listed pending room nights, create the closing pack, and advance the business date.</span>
          <button type="button" className="secondary-button" onClick={() => setConfirmingClose(false)} disabled={busy}>Cancel</button>
          <button type="button" className="primary-button" onClick={() => void closeDay()} disabled={busy}>{busy ? 'Closing…' : 'Confirm daily closing'}</button>
        </>}
      </div></section>}
      {!archive && !summary.posting_open && <section className="panel form-panel"><div className="panel-head"><h2>Daily closing completed</h2><span>Closed</span></div><p className="muted">{`Business date ${summary.business_date} has already been closed. No further daily closing action is required for this business date.`}</p><p className="muted">The hotel is ready for activity on the next business date.</p></section>}
    </div>
  </section>;
}
