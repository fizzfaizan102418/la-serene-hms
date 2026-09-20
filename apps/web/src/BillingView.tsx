import React, { useEffect, useMemo, useState } from 'react';

type Summary = { folio_id: number; reservation_id: number; guest_name: string; status: string; total: number; paid: number; balance: number };
type Item = { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number; line_total: number };
type Payment = { id: number; amount: number; method: string; reference?: string | null };
type Folio = { id: number; reservation_id: number; status: string; active_stay_id?: number | null; deposit_balance: number; deposit_required: number; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; food_service_charge: number; total: number; paid: number; balance: number };
type FinancialTransaction = { id: number; transaction_type: string; status: string; reference_type?: string | null; reference_id?: string | null; folio_id?: number | null; reversal_of_id?: number | null };
type ReservationLookup = { id: number; room_ids: number[] };
type RoomLookup = { id: number; number: string };
type Props = { userRole: string; summaries: Summary[]; onRefresh: () => Promise<void>; api: <T>(path: string, options?: RequestInit) => Promise<T> };

const money = (value: number) => Number(value || 0).toFixed(2);
const ITEM_TRANSACTION_REFERENCES = new Set(['folio_item', 'folio_item_discount', 'folio_item_service_charge']);
const PAGE_SIZE = 25;

function paymentStatus(summary: Summary): 'paid' | 'partial' | 'due' {
  if (Number(summary.balance) <= 0.005) return 'paid';
  if (Number(summary.paid) > 0) return 'partial';
  return 'due';
}

function paymentLabel(status: ReturnType<typeof paymentStatus>) {
  return status === 'paid' ? 'Paid' : status === 'partial' ? 'Partial' : 'Due';
}

function normalizeStatus(status: string) {
  return status.toLowerCase().replace(/_/g, ' ');
}

function methodLabel(method: string) {
  return method.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}

function badgeStyle(kind: 'paid' | 'partial' | 'due' | 'open' | 'closed') {
  const styles = {
    paid: { background: '#dcfce7', color: '#166534' },
    partial: { background: '#fef3c7', color: '#92400e' },
    due: { background: '#fee2e2', color: '#991b1b' },
    open: { background: '#dbeafe', color: '#1d4ed8' },
    closed: { background: '#e5e7eb', color: '#374151' },
  } as const;
  return { ...styles[kind], borderRadius: 999, display: 'inline-flex', alignItems: 'center', padding: '4px 9px', fontSize: 12, fontWeight: 700, whiteSpace: 'nowrap' as const };
}

export default function BillingView({ userRole, summaries, onRefresh, api }: Props) {
  const canOperate = userRole === 'admin' || userRole === 'reception';
  const isAdmin = userRole === 'admin';
  const [selected, setSelected] = useState<number | null>(summaries[0]?.folio_id ?? null);
  const [folio, setFolio] = useState<Folio | null>(null);
  const [transactions, setTransactions] = useState<FinancialTransaction[]>([]);
  const [roomNumbersByReservation, setRoomNumbersByReservation] = useState<Record<number, string[]>>({});
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('service');
  const [quantity, setQuantity] = useState('1');
  const [unitPrice, setUnitPrice] = useState('');
  const [discount, setDiscount] = useState('0');
  const [amount, setAmount] = useState('');
  const [depositAmount, setDepositAmount] = useState('');
  const [method, setMethod] = useState('cash');
  const [depositMethod, setDepositMethod] = useState('cash');
  const [depositReference, setDepositReference] = useState('');
  const [reference, setReference] = useState('');
  const [message, setMessage] = useState('');
  const [editingItem, setEditingItem] = useState<Item | null>(null);
  const [editDescription, setEditDescription] = useState('');
  const [editCategory, setEditCategory] = useState('service');
  const [editQuantity, setEditQuantity] = useState('1');
  const [editUnitPrice, setEditUnitPrice] = useState('');
  const [editDiscount, setEditDiscount] = useState('0');
  const [editReason, setEditReason] = useState('Billing correction');
  const [busyItemId, setBusyItemId] = useState<number | null>(null);

  const [query, setQuery] = useState('');
  const [folioStatusFilter, setFolioStatusFilter] = useState('all');
  const [paymentFilter, setPaymentFilter] = useState('all');
  const [sortBy, setSortBy] = useState('newest');
  const [page, setPage] = useState(1);

  useEffect(() => {
    if (selected !== null && summaries.some(summary => summary.folio_id === selected)) return;
    setSelected(summaries[0]?.folio_id ?? null);
  }, [summaries, selected]);

  useEffect(() => {
    setPage(1);
  }, [query, folioStatusFilter, paymentFilter, sortBy]);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<ReservationLookup[]>('/api/reservations'),
      api<RoomLookup[]>('/api/rooms'),
    ]).then(([reservations, rooms]) => {
      if (!active) return;
      const roomNumberById = new Map(rooms.map(room => [room.id, room.number]));
      const mapping: Record<number, string[]> = {};
      reservations.forEach(reservation => {
        mapping[reservation.id] = (reservation.room_ids || [])
          .map(roomId => roomNumberById.get(roomId))
          .filter((number): number is string => Boolean(number));
      });
      setRoomNumbersByReservation(mapping);
    }).catch(() => {
      if (active) setRoomNumbersByReservation({});
    });
    return () => { active = false; };
  }, [api]);

  const filteredSummaries = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const filtered = summaries.filter(summary => {
      const roomNumbers = roomNumbersByReservation[summary.reservation_id] || [];
      const searchable = `${summary.folio_id} ${summary.reservation_id} ${summary.guest_name} ${roomNumbers.join(' ')}`.toLowerCase();
      const statusMatches = folioStatusFilter === 'all' || summary.status.toLowerCase() === folioStatusFilter;
      const paymentMatches = paymentFilter === 'all' || paymentStatus(summary) === paymentFilter;
      return (!needle || searchable.includes(needle)) && statusMatches && paymentMatches;
    });

    return [...filtered].sort((a, b) => {
      if (sortBy === 'oldest') return a.folio_id - b.folio_id;
      if (sortBy === 'guest') return a.guest_name.localeCompare(b.guest_name);
      if (sortBy === 'balance') return Number(b.balance) - Number(a.balance);
      if (sortBy === 'total') return Number(b.total) - Number(a.total);
      return b.folio_id - a.folio_id;
    });
  }, [summaries, roomNumbersByReservation, query, folioStatusFilter, paymentFilter, sortBy]);

  const pageCount = Math.max(1, Math.ceil(filteredSummaries.length / PAGE_SIZE));
  const visibleSummaries = filteredSummaries.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  const registerStats = useMemo(() => {
    let open = 0; let closed = 0; let paid = 0; let due = 0;
    summaries.forEach(summary => {
      if (summary.status.toLowerCase() === 'closed') closed += 1; else open += 1;
      if (paymentStatus(summary) === 'paid') paid += 1; else due += 1;
    });
    return { total: summaries.length, open, closed, paid, due };
  }, [summaries]);

  async function loadFolio(id: number) {
    const [nextFolio, nextTransactions] = await Promise.all([
      api<Folio>(`/api/folios/${id}`),
      api<FinancialTransaction[]>('/api/ledger/transactions?limit=500'),
    ]);
    setFolio(nextFolio);
    setTransactions(nextTransactions);
  }

  async function openFolio(id: number) {
    setSelected(id); setMessage(''); setEditingItem(null);
    try { await loadFolio(id); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load folio'); }
  }

  async function ensureSelected() {
    const id = selected ?? summaries[0]?.folio_id;
    if (!id) throw new Error('Select a folio first');
    if (!folio || folio.id !== id) await loadFolio(id);
    return id;
  }

  function itemTransactions(item: Item) {
    return transactions.filter(tx => tx.reference_id === String(item.id) && ITEM_TRANSACTION_REFERENCES.has(tx.reference_type || ''));
  }

  function itemState(item: Item): 'posted' | 'reversed' | 'unknown' {
    const related = itemTransactions(item);
    if (!related.length) return 'unknown';
    if (related.some(tx => tx.status === 'posted')) return 'posted';
    if (related.every(tx => tx.status === 'reversed')) return 'reversed';
    return 'unknown';
  }

  function startEdit(item: Item) {
    if (!isAdmin || itemState(item) !== 'posted') return;
    setEditingItem(item);
    setEditDescription(item.description);
    setEditCategory(item.category);
    setEditQuantity(String(item.quantity));
    setEditUnitPrice(String(item.unit_price));
    setEditDiscount(String(item.discount || 0));
    setEditReason('Billing correction');
    setMessage('');
  }

  async function addRoomCharges() {
    try { const id = await ensureSelected(); setFolio(await api<Folio>(`/api/folios/${id}/room-charges`, { method: 'POST' })); setMessage('Room charges posted.'); await onRefresh(); await loadFolio(id); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add room charges'); }
  }

  async function addCharge(event: React.FormEvent) {
    event.preventDefault();
    try {
      const id = await ensureSelected();
      await api<Item>(`/api/folios/${id}/items`, { method: 'POST', body: JSON.stringify({ description, category, quantity: Number(quantity), unit_price: Number(unitPrice), discount: Number(discount || 0) }) });
      setDescription(''); setQuantity('1'); setUnitPrice(''); setDiscount('0');
      await loadFolio(id); setMessage('Charge posted.'); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add charge'); }
  }

  async function removeItem(item: Item) {
    if (!isAdmin || !folio || folio.status !== 'open') return;
    const state = itemState(item);
    if (state !== 'posted') return;
    if (!window.confirm(`Remove charge “${item.description}” for PKR ${money(item.line_total)}? The charge will be reversed with a full audit trail; the original record will remain visible.`)) return;
    setBusyItemId(item.id); setMessage('');
    try {
      const id = await ensureSelected();
      await api(`/api/folios/${id}/items/${item.id}/reverse?reason=${encodeURIComponent('Removed from folio')}`, { method: 'POST' });
      await loadFolio(id); setMessage(`Charge #${item.id} removed by audited reversal.`); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to remove charge'); }
    finally { setBusyItemId(null); }
  }

  async function saveEdit(event: React.FormEvent) {
    event.preventDefault();
    if (!editingItem || !isAdmin) return;
    setBusyItemId(editingItem.id); setMessage('');
    try {
      const id = await ensureSelected();
      const gross = Number(editQuantity) * Number(editUnitPrice);
      const fixedDiscount = Number(editDiscount || 0);
      if (fixedDiscount > gross) throw new Error('Discount cannot exceed the line amount');
      await api(`/api/folios/${id}/items/${editingItem.id}/correct`, {
        method: 'POST',
        body: JSON.stringify({ description: editDescription, category: editCategory, quantity: Number(editQuantity), unit_price: Number(editUnitPrice), discount: fixedDiscount, reason: editReason || 'Billing correction' }),
      });
      setEditingItem(null); await loadFolio(id); setMessage(`Charge #${editingItem.id} corrected. The original remains in the audit trail.`); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to correct charge'); }
    finally { setBusyItemId(null); }
  }

  async function addDeposit(event: React.FormEvent) {
    event.preventDefault();
    try {
      const id = await ensureSelected();
      if (!folio?.active_stay_id) throw new Error('No active in-house stay is available for this folio');
      await api(`/api/folios/${id}/deposits`, { method: 'POST', body: JSON.stringify({ amount: Number(depositAmount), method: depositMethod, reference: depositReference || null }) });
      await loadFolio(id); setDepositAmount(''); setDepositReference(''); setMessage('Guest deposit recorded. It will be applied automatically to eligible room charges during Night Audit.'); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record guest deposit'); }
  }

  async function addPayment(event: React.FormEvent) {
    event.preventDefault();
    try { const id = await ensureSelected(); await api<Payment>(`/api/folios/${id}/payments`, { method: 'POST', body: JSON.stringify({ amount: Number(amount), method, reference: reference || null }) }); await loadFolio(id); setAmount(''); setReference(''); setMessage('Payment recorded.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record payment'); }
  }

  async function closeFolio() {
    if (folio && folio.balance > 0.005) {
      if (!window.confirm(`This folio still has PKR ${money(folio.balance)} outstanding. Close it anyway?`)) return;
    } else if (!window.confirm('Close this folio? This will complete the current billing record.')) return;
    try { const id = await ensureSelected(); setFolio(await api<Folio>(`/api/folios/${id}/close`, { method: 'POST' })); setMessage('Folio closed successfully.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close folio'); }
  }

  function escapePrintHtml(value: unknown) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  async function printReceipt() {
    try {
      const id = await ensureSelected();
      const receipt = await api<any>(`/api/folios/${id}/receipt`);
      const rows = receipt.items.map((item: Item) => `<tr><td>${escapePrintHtml(item.description)}</td><td>${escapePrintHtml(item.category)}</td><td class="num">${item.quantity}</td><td class="num">PKR ${money(item.unit_price)}</td><td class="num">PKR ${money(item.discount)}</td><td class="num strong">PKR ${money(item.line_total)}</td></tr>`).join('');
      const paymentRows = (receipt.payments || []).map((payment: Payment) => `<tr><td>${escapePrintHtml(methodLabel(payment.method))}</td><td>${escapePrintHtml(payment.reference || '—')}</td><td class="num strong">PKR ${money(payment.amount)}</td></tr>`).join('');
      const status = Number(receipt.balance || 0) <= 0.005 ? 'PAID' : Number(receipt.paid || 0) > 0 ? 'PARTIALLY PAID' : 'BALANCE DUE';
      const balanceClass = status === 'PAID' ? 'paid' : 'due';
      const html = `<!doctype html><html><head><meta charset="utf-8"><title>La Serene Hotel · Folio #${receipt.folio_id}</title>
<style>
@page{size:A4;margin:14mm 13mm 16mm}
:root{color-scheme:light}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:#1E293B;font:12px/1.45 Arial,Helvetica,sans-serif}
.sheet{max-width:184mm;margin:0 auto}
.header{display:flex;justify-content:space-between;gap:24px;padding-bottom:14px;border-bottom:2px solid #D4AF37}
.brand{display:grid;gap:3px}.brand h1{margin:0;font-size:22px;letter-spacing:.04em}.brand p{margin:0;color:#64748B;font-size:10px}
.meta{text-align:right}.meta strong{display:block;font-size:15px}.meta span{display:block;color:#64748B;font-size:10px;margin-top:2px}
.status{display:inline-flex;margin-top:10px;padding:5px 9px;border-radius:999px;font-size:9px;font-weight:800;letter-spacing:.06em;background:#FEF3C7;color:#92400E}
.status.paid{background:#DCFCE7;color:#166534}.status.due{background:#FEE2E2;color:#991B1B}
.section{margin-top:18px}.section-title{margin:0 0 8px;font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#64748B}
.info-grid{display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:8px}.info{padding:9px 10px;border:1px solid #E2E8F0;border-radius:8px;background:#F8FAFC}.info span{display:block;color:#64748B;font-size:9px;text-transform:uppercase;letter-spacing:.05em}.info strong{display:block;margin-top:2px;font-size:11px}
table{width:100%;border-collapse:collapse}th{padding:8px 7px;background:#F8FAFC;color:#475569;font-size:9px;text-transform:uppercase;letter-spacing:.05em;text-align:left;border-top:1px solid #E2E8F0;border-bottom:1px solid #CBD5E1}td{padding:8px 7px;border-bottom:1px solid #E2E8F0;vertical-align:top}th.num,td.num{text-align:right}.strong{font-weight:750}
.totals{width:330px;max-width:100%;margin:14px 0 0 auto}.totals div{display:flex;justify-content:space-between;gap:16px;padding:5px 0;color:#475569}.totals .grand{margin-top:4px;padding-top:9px;border-top:2px solid #1E293B;color:#1E293B;font-size:13px;font-weight:800}.totals .balance{margin-top:7px;padding:10px;border-radius:8px;background:#FEF3C7;color:#92400E;font-weight:800}.totals .balance.paid{background:#DCFCE7;color:#166534}
.footer{margin-top:28px;padding-top:10px;border-top:1px solid #CBD5E1;color:#64748B;font-size:9px;display:flex;justify-content:space-between;gap:16px}
@media print{body{print-color-adjust:exact;-webkit-print-color-adjust:exact}.no-print{display:none!important}thead{display:table-header-group}tr{break-inside:avoid}.section{break-inside:auto}}
@media(max-width:700px){.header,.info-grid{grid-template-columns:1fr;display:grid}.meta{text-align:left}.totals{width:100%}}
</style></head><body><main class="sheet">
<header class="header"><div class="brand"><h1>LA SERENE HOTEL</h1><p>Hotel Management System · Guest Folio</p></div><div class="meta"><strong>Folio #${escapePrintHtml(receipt.folio_id)}</strong><span>Reservation #${escapePrintHtml(receipt.reservation_id)}</span><span>${escapePrintHtml(receipt.generated_at || new Date().toLocaleString())}</span><span class="status ${balanceClass}">${status}</span></div></header>
<section class="section"><h2 class="section-title">Guest & stay</h2><div class="info-grid"><div class="info"><span>Guest</span><strong>${escapePrintHtml(receipt.guest?.full_name || '—')}</strong></div><div class="info"><span>Check-in</span><strong>${escapePrintHtml(receipt.stay?.check_in || '—')}</strong></div><div class="info"><span>Check-out</span><strong>${escapePrintHtml(receipt.stay?.check_out || '—')}</strong></div><div class="info"><span>Nights</span><strong>${escapePrintHtml(receipt.stay?.nights ?? '—')}</strong></div><div class="info"><span>Room</span><strong>${escapePrintHtml((receipt.stay?.room_numbers || []).join(', ') || receipt.stay?.room || '—')}</strong></div><div class="info"><span>Folio status</span><strong>${escapePrintHtml(receipt.status || '—')}</strong></div></div></section>
<section class="section"><h2 class="section-title">Charges</h2><table><thead><tr><th>Description</th><th>Category</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Discount</th><th class="num">Total</th></tr></thead><tbody>${rows || '<tr><td colspan="6">No charges</td></tr>'}</tbody></table></section>
<section class="section"><h2 class="section-title">Payments received</h2><table><thead><tr><th>Method</th><th>Reference</th><th class="num">Amount</th></tr></thead><tbody>${paymentRows || '<tr><td colspan="3">No payments recorded</td></tr>'}</tbody></table></section>
<div class="totals"><div><span>Subtotal</span><b>PKR ${money(receipt.subtotal)}</b></div><div><span>Discounts</span><b>PKR ${money(receipt.discounts)}</b></div><div><span>Food service charge</span><b>PKR ${money(receipt.food_service_charge)}</b></div><div class="grand"><span>Total</span><b>PKR ${money(receipt.total)}</b></div><div><span>Paid</span><b>PKR ${money(receipt.paid)}</b></div><div class="balance ${balanceClass}"><span>Balance due</span><b>PKR ${money(receipt.balance)}</b></div></div>
<footer class="footer"><span>Thank you for staying with La Serene Hotel.</span><span>Official guest folio · Keep for your records</span></footer>
</main></body></html>`;
      const popup = window.open('', '_blank', 'width=900,height=1000');
      if (!popup) throw new Error('Please allow pop-ups to print the folio');
      popup.document.write(html); popup.document.close(); popup.focus();
      window.setTimeout(() => popup.print(), 250);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to print folio'); }
  }

  const selectedPaymentStatus = folio ? (folio.balance <= 0.005 ? 'paid' : folio.paid > 0 ? 'partial' : 'due') : 'due';
  const selectedFolioStatus = folio?.status.toLowerCase() === 'closed' ? 'closed' : 'open';

  return <section className="page">
    <div className="page-heading"><div><p className="muted">Folios, charges, payments and balances</p><h2>Billing</h2></div><span className="room-count">{registerStats.total} folios</span></div>
    {message && <p className="notice">{message}</p>}

    <section className="stats billing-kpis">
      {[['Total Folios', registerStats.total], ['Open', registerStats.open], ['Closed', registerStats.closed], ['Paid', registerStats.paid], ['Outstanding', registerStats.due]].map(([label, value]) => <article className={(label === 'Outstanding' ? 'stat billing-kpi billing-kpi-alert' : 'stat billing-kpi')} key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}
    </section>

    <div className="billing-layout billing-workspace">
      <div className="panel billing-register-panel">
        <div className="panel-head"><div><p className="muted">Search, filter and open a folio</p><h2>Folio register</h2></div><span>{filteredSummaries.length} matching folios</span></div>
        <div className="billing-filters">
          <input aria-label="Search folios" placeholder="Search guest, room, folio # or reservation #" value={query} onChange={e => setQuery(e.target.value)} />
          <select aria-label="Folio status" value={folioStatusFilter} onChange={e => setFolioStatusFilter(e.target.value)}><option value="all">All folio statuses</option><option value="open">Open</option><option value="closed">Closed</option></select>
          <select aria-label="Payment status" value={paymentFilter} onChange={e => setPaymentFilter(e.target.value)}><option value="all">All payment statuses</option><option value="paid">Paid</option><option value="partial">Partial</option><option value="due">Due</option></select>
          <select aria-label="Sort folios" value={sortBy} onChange={e => setSortBy(e.target.value)}><option value="newest">Newest folio</option><option value="oldest">Oldest folio</option><option value="guest">Guest name</option><option value="balance">Highest balance</option><option value="total">Highest total</option></select>
        </div>

        <div className="billing-list">
          {visibleSummaries.length ? visibleSummaries.map(summary => {
            const payStatus = paymentStatus(summary);
            const folioStatus = summary.status.toLowerCase() === 'closed' ? 'closed' : 'open';
            const roomNumbers = roomNumbersByReservation[summary.reservation_id] || [];
            return <button type="button" key={summary.folio_id} className={`billing-row ${selected === summary.folio_id ? 'selected' : ''}`} onClick={() => void openFolio(summary.folio_id)}>
              <div className="billing-row-main"><strong>Folio #{summary.folio_id} · {summary.guest_name}</strong>{roomNumbers.length > 0 && <span>Rooms {roomNumbers.join(', ')}</span>}<span>Reservation #{summary.reservation_id}</span><span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 5 }}><span style={badgeStyle(folioStatus)}>{normalizeStatus(summary.status)}</span><span style={badgeStyle(payStatus)}>{paymentLabel(payStatus)}</span></span></div>
              <div className="billing-row-amount"><b>PKR {money(summary.total)}</b><small className={summary.balance > 0 ? 'billing-balance-due' : 'billing-balance-paid'}>{summary.balance > 0 ? `PKR ${money(summary.balance)} due` : 'PKR 0.00 due'}</small></div>
            </button>;
          }) : <p className="muted">No folios match the current search and filters.</p>}
        </div>

        {filteredSummaries.length > PAGE_SIZE && <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
          <span className="muted">Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, filteredSummaries.length)} of {filteredSummaries.length}</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <button className="secondary-button small-button" disabled={page === 1} onClick={() => setPage(current => Math.max(1, current - 1))}>Previous</button>
            <span className="muted">Page {page} of {pageCount}</span>
            <button className="secondary-button small-button" disabled={page === pageCount} onClick={() => setPage(current => Math.min(pageCount, current + 1))}>Next</button>
          </div>
        </div>}
      </div>

      <div className="side-stack">
        {folio && <div className="panel billing-detail-panel">
          <div className="panel-head"><div><p className="muted">Folio #{folio.id} · Reservation #{folio.reservation_id}</p><h2>Folio details</h2></div><div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}><span style={badgeStyle(selectedFolioStatus)}>{normalizeStatus(folio.status)}</span><span style={badgeStyle(selectedPaymentStatus)}>{paymentLabel(selectedPaymentStatus)}</span></div></div>
          <div className="billing-balance-banner"><div><span>Balance due</span><strong>PKR {money(folio.balance)}</strong></div><small>{folio.balance > 0.005 ? 'Payment is still outstanding. Review the folio before closing.' : 'Folio is fully settled.'}</small></div><div className="billing-totals"><div><span>Subtotal</span><b>PKR {money(folio.subtotal)}</b></div><div><span>Discounts</span><b>PKR {money(folio.discounts)}</b></div><div><span>Staff service charge (10%)</span><b>PKR {money(folio.food_service_charge)}</b></div><div className="grand"><span>Total</span><b>PKR {money(folio.total)}</b></div><div><span>Paid</span><b>PKR {money(folio.paid)}</b></div><div><span>Guest deposit held</span><b>PKR {money(folio.deposit_balance)}</b></div><div className="balance"><span>Balance</span><b>PKR {money(folio.balance)}</b></div></div>

          <div className="panel-head billing-section-head" style={{ marginTop: 18 }}><h3>Charges</h3><span>{folio.items.length} line items</span></div>
          <div className="folio-items">{folio.items.length ? folio.items.map(item => { const state = itemState(item); return <article key={item.id}><div><strong>{item.description}</strong><span>{item.category} · {Number(item.quantity)} × PKR {money(item.unit_price)}{Number(item.discount) ? ` · discount PKR ${money(item.discount)}` : ''}</span></div><div className="desk-actions"><b>PKR {money(item.line_total)}</b><span className="muted">{state === 'reversed' ? 'Reversed · audit retained' : state === 'posted' ? 'Posted · immutable' : 'Financial status unavailable'}</span>{isAdmin && folio.status === 'open' && state === 'posted' && <><button className="secondary-button small-button" disabled={busyItemId === item.id} onClick={() => startEdit(item)}>Edit</button><button className="secondary-button small-button" disabled={busyItemId === item.id} onClick={() => void removeItem(item)}>{busyItemId === item.id ? 'Working…' : 'Remove'}</button></>}</div></article>; }) : <p className="muted">No charges yet.</p>}</div>

          <div className="panel-head billing-section-head" style={{ marginTop: 18 }}><h3>Payments</h3><span>{folio.payments.length} payments</span></div>
          <div className="folio-items">{folio.payments.length ? folio.payments.map(payment => <article key={payment.id}><div><strong>PKR {money(payment.amount)}</strong><span>{methodLabel(payment.method)}{payment.reference ? ` · ${payment.reference}` : ''}</span></div><span className="muted">Payment #{payment.id}</span></article>) : <p className="muted">No payments recorded.</p>}</div>

          {canOperate && <div className="billing-actions">{folio.status === 'open' && <button className="secondary-button" onClick={() => void addRoomCharges()}>Add room charges</button>}<button className="secondary-button" onClick={() => void printReceipt()}>Print receipt</button>{folio.status === 'open' && folio.balance === 0 && <button className="primary-button" onClick={() => void closeFolio()}>Close folio</button>}</div>}
        </div>}

        {isAdmin && editingItem && folio?.status === 'open' && <form className="panel form-panel" onSubmit={saveEdit}><div className="panel-head"><div><h2>Edit charge</h2><span>Creates an audited reversal and replacement</span></div><button type="button" className="secondary-button small-button" onClick={() => setEditingItem(null)}>Cancel</button></div><label>Description<input value={editDescription} onChange={e => setEditDescription(e.target.value)} required /></label><label>Category<select value={editCategory} onChange={e => setEditCategory(e.target.value)}><option value="service">Service</option><option value="food">Food & beverage</option><option value="room">Room</option><option value="adjustment">Adjustment</option><option value="other">Other</option></select></label><div className="two-col"><label>Quantity<input type="number" min="0.01" step="0.01" value={editQuantity} onChange={e => setEditQuantity(e.target.value)} required /></label><label>Unit price<input type="number" min="0" step="0.01" value={editUnitPrice} onChange={e => setEditUnitPrice(e.target.value)} required /></label></div><label>Discount<input type="number" min="0" step="0.01" value={editDiscount} onChange={e => setEditDiscount(e.target.value)} /></label><label>Reason<input value={editReason} onChange={e => setEditReason(e.target.value)} required /></label><p className="muted">The original posted charge is never overwritten. Its financial transactions are reversed atomically, then the corrected charge is posted.</p><button className="primary-button" disabled={busyItemId === editingItem.id || !editUnitPrice}>{busyItemId === editingItem.id ? 'Saving…' : 'Save correction'}</button></form>}
        {canOperate && folio?.status === 'open' && !editingItem && <form className="panel form-panel" onSubmit={addCharge}><div className="panel-head"><h2>Add charge</h2><span>Charges post immediately</span></div><label>Description<input value={description} onChange={e => setDescription(e.target.value)} required /></label><label>Category<select value={category} onChange={e => setCategory(e.target.value)}><option value="service">Service</option><option value="food">Food & beverage</option><option value="room">Room</option><option value="adjustment">Adjustment</option><option value="other">Other</option></select></label><div className="two-col"><label>Quantity<input type="number" min="0.01" step="0.01" value={quantity} onChange={e => setQuantity(e.target.value)} required /></label><label>Unit price<input type="number" min="0" step="0.01" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} required /></label></div><label>Discount<input type="number" min="0" step="0.01" value={discount} onChange={e => setDiscount(e.target.value)} /></label><button className="primary-button" disabled={!selected || !unitPrice}>Post charge</button></form>}
        {canOperate && folio?.status === 'open' && <form className="panel form-panel" onSubmit={addDeposit}>
          <div className="panel-head"><h2>Record guest deposit</h2><span>Advance payment for the current/future stay</span></div>
          <p className="muted">Held as a guest deposit, not a folio payment. Night Audit applies available deposits to eligible room charges.</p>
          <label>Amount<input type="number" min="0.01" step="0.01" value={depositAmount} onChange={e => setDepositAmount(e.target.value)} required /></label>
          <label>Method<select value={depositMethod} onChange={e => setDepositMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label>
          <label>Reference<input value={depositReference} onChange={e => setDepositReference(e.target.value)} /></label>
          <p className="muted">Current deposit: PKR {money(folio?.deposit_balance || 0)} / required: PKR {money(folio?.deposit_required || 0)}</p>
          <button className="primary-button" disabled={!selected || !depositAmount || !folio?.active_stay_id}>Record guest deposit</button>
        </form>}

        {canOperate && folio?.status === 'open' && <form className="panel form-panel" onSubmit={addPayment}>
          <div className="panel-head"><h2>Record payment</h2></div>
          <label>Amount<input type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} required /></label>
          <label>Method<select value={method} onChange={e => setMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label>
          <label>Reference<input value={reference} onChange={e => setReference(e.target.value)} /></label>
          <button className="primary-button" disabled={!selected || !amount}>Record payment</button>
        </form>}
      </div>
    </div>
  </section>;
}
