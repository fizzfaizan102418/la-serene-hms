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

  function printReport() {
    if (!summary) return;
    const money = (value: number) => Number(value || 0).toFixed(2);
    const generated = new Date(summary.generated_at).toLocaleString();
    const notesHtml = notes.trim() ? `<section><h2>Closing Notes</h2><p class="notes">${escapeHtml(notes).replace(/\n/g, '<br/>')}</p></section>` : '';
    const html = `<!doctype html><html><head><meta charset="utf-8"><title>La Serene Hotel - Daily Closing - ${summary.business_date}</title><style>
      @page{size:A4;margin:16mm}*{box-sizing:border-box}body{font-family:Arial,Helvetica,sans-serif;color:#222;margin:0;font-size:12px}h1,h2,p{margin:0}header{border-bottom:2px solid #222;padding-bottom:12px;margin-bottom:16px}h1{font-size:22px;letter-spacing:.4px}header p{margin-top:4px;color:#555}.meta{display:grid;grid-template-columns:1fr 1fr;margin-top:10px;gap:8px}.meta div{padding:8px;border:1px solid #ddd;border-radius:5px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}section{border:1px solid #ddd;border-radius:6px;padding:12px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.6px;margin-bottom:9px}.row{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid #eee}.row:last-child{border-bottom:0;font-weight:700}.total{font-size:13px}.notes{line-height:1.5;min-height:50px}.footer{margin-top:18px;padding-top:12px;border-top:1px solid #bbb;display:grid;grid-template-columns:1fr 1fr;gap:24px}.signature{padding-top:30px;border-bottom:1px solid #777}.small{font-size:10px;color:#666;margin-top:5px}@media print{button{display:none}}
    </style></head><body><header><h1>LA SERENE HOTEL</h1><p>Daily Closing / Night Audit Report</p><div class="meta"><div><strong>Business Date</strong><br/>${summary.business_date}</div><div><strong>Generated</strong><br/>${escapeHtml(generated)}</div></div></header>
      <div class="grid"><section><h2>Occupancy & Movement</h2><div class="row"><span>Total rooms</span><strong>${summary.occupancy.total_rooms}</strong></div><div class="row"><span>Occupied rooms</span><strong>${summary.occupancy.occupied_rooms}</strong></div><div class="row"><span>Reserved rooms</span><strong>${summary.occupancy.reserved_rooms}</strong></div><div class="row"><span>Available rooms</span><strong>${summary.occupancy.available_rooms}</strong></div><div class="row"><span>Dirty / out of order</span><strong>${summary.occupancy.dirty_rooms} / ${summary.occupancy.out_of_order_rooms}</strong></div><div class="row"><span>In-house reservations</span><strong>${summary.occupancy.in_house_reservations}</strong></div><div class="row"><span>Arrivals / departures</span><strong>${summary.movement.arrivals} / ${summary.movement.departures}</strong></div><div class="row"><span>No-shows</span><strong>${summary.movement.no_shows}</strong></div></section>
      <section><h2>Revenue</h2><div class="row"><span>Room revenue</span><strong>${money(summary.revenue.room)}</strong></div><div class="row"><span>Food revenue</span><strong>${money(summary.revenue.food)}</strong></div><div class="row"><span>Food service charge (10%)</span><strong>${money(summary.revenue.food_service_charge)}</strong></div><div class="row"><span>Other revenue</span><strong>${money(summary.revenue.other)}</strong></div><div class="row total"><span>Gross revenue</span><strong>${money(summary.revenue.gross)}</strong></div></section>
      <section><h2>Cashier Collection</h2><div class="row"><span>Cash</span><strong>${money(summary.payments.cash || 0)}</strong></div><div class="row"><span>Card</span><strong>${money(summary.payments.card || 0)}</strong></div><div class="row"><span>Bank transfer</span><strong>${money(summary.payments.bank_transfer || 0)}</strong></div><div class="row"><span>Other</span><strong>${money(summary.payments.other || 0)}</strong></div><div class="row total"><span>Total collected</span><strong>${money(summary.payments.total)}</strong></div></section>
      <section><h2>Control Totals</h2><div class="row"><span>Outstanding</span><strong>${money(summary.outstanding)}</strong></div><div class="row"><span>Expenses</span><strong>${money(summary.expenses)}</strong></div><div class="row total"><span>Net operating</span><strong>${money(summary.net_operating)}</strong></div></section></div>
      ${notesHtml}<div class="footer"><div><div class="signature"></div><div class="small">Prepared / Printed by</div></div><div><div class="signature"></div><div class="small">Head Office received / verified</div></div></div><p class="small" style="margin-top:14px">This report is generated from the hotel's offline operational records for the stated business date.</p>
      <script>window.onload=()=>{window.focus();window.print();}</script></body></html>`;
    const popup = window.open('', '_blank', 'width=900,height=900');
    if (!popup) { setMessage('Pop-up blocked. Allow pop-ups for this hotel application to print the daily closing report.'); return; }
    popup.document.open(); popup.document.write(html); popup.document.close();
  }

  function escapeHtml(value: string) {
    return value.replace(/[&<>\"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[char] || char));
  }

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
  if (!summary) return <section className="page"><div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div></div>{message && <p className="notice">{message}</p>}<p className="muted">Loading closing pack...</p></section>;

  return <section className="page">
    <div className="page-heading"><div><p className="muted">End-of-day controls</p><h2>Night Audit & Daily Closing</h2></div><div className="desk-actions"><span className="room-count">Business date {summary.business_date}</span><button className="secondary-button" onClick={() => void load()} disabled={busy}>Refresh</button><button className="secondary-button" onClick={printReport}>Print Daily Closing</button></div></div>
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
      <section className="panel form-panel"><div className="panel-head"><h2>Close business day</h2><span>Admin</span></div><p className="muted">Record any handover notes for Head Office before locking the day's operational closing record.</p><label>Closing notes<textarea rows={5} value={notes} onChange={e => setNotes(e.target.value)} placeholder="Cashier variance, pending follow-up, maintenance notes, Head Office comments..." /></label><button className="primary-button" disabled={busy} onClick={() => void closeDay()}>{busy ? 'Closing...' : 'Complete daily closing'}</button></section>
    </div>
  </section>;
}
