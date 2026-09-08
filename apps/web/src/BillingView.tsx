import React, { useState } from 'react';

type Summary = { folio_id: number; reservation_id: number; guest_name: string; status: string; total: number; paid: number; balance: number };
type Item = { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number; line_total: number };
type Payment = { id: number; amount: number; method: string; reference?: string | null };
type Folio = { id: number; reservation_id: number; status: string; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; total: number; paid: number; balance: number };
type Receipt = { folio_id: number; reservation_id: number; status: string; guest: { full_name: string; phone?: string | null; email?: string | null; address?: string | null }; stay: { check_in: string; check_out: string; nights: number }; rooms: { id: number; number: string; room_type_id: number }[]; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; total: number; paid: number; balance: number };

type Props = { userRole: string; summaries: Summary[]; onRefresh: () => Promise<void>; api: <T>(path: string, options?: RequestInit) => Promise<T> };

const money = (value: number) => Number(value || 0).toFixed(2);
const escapeHtml = (value: string) => value.replace(/[&<>'"]/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char] ?? char);

export default function BillingView({ userRole, summaries, onRefresh, api }: Props) {
  const canOperate = userRole === 'admin' || userRole === 'reception';
  const [selected, setSelected] = useState<number | null>(summaries[0]?.folio_id ?? null);
  const [folio, setFolio] = useState<Folio | null>(null);
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('service');
  const [quantity, setQuantity] = useState('1');
  const [unitPrice, setUnitPrice] = useState('');
  const [discount, setDiscount] = useState('0');
  const [amount, setAmount] = useState('');
  const [method, setMethod] = useState('cash');
  const [reference, setReference] = useState('');
  const [message, setMessage] = useState('');

  async function openFolio(id: number) {
    setSelected(id); setMessage('');
    try { setFolio(await api<Folio>(`/api/folios/${id}`)); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load folio'); }
  }

  async function ensureSelected() {
    const id = selected ?? summaries[0]?.folio_id;
    if (!id) throw new Error('Select a folio first');
    if (!folio || folio.id !== id) setFolio(await api<Folio>(`/api/folios/${id}`));
    return id;
  }

  async function addRoomCharges() {
    setMessage('');
    try { const id = await ensureSelected(); const result = await api<Folio>(`/api/folios/${id}/room-charges`, { method: 'POST' }); setFolio(result); setMessage('Room charges added.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add room charges'); }
  }

  async function addItem(event: React.FormEvent) {
    event.preventDefault(); setMessage('');
    try {
      const id = await ensureSelected();
      const result = await api<Item>(`/api/folios/${id}/items`, { method: 'POST', body: JSON.stringify({ description, category, quantity: Number(quantity), unit_price: Number(unitPrice), discount: Number(discount || 0) }) });
      setFolio(current => current ? { ...current, items: [...current.items, result] } : current);
      setDescription(''); setUnitPrice(''); setDiscount('0'); setMessage('Charge added.'); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add charge'); }
  }

  async function addPayment(event: React.FormEvent) {
    event.preventDefault(); setMessage('');
    try {
      const id = await ensureSelected();
      const result = await api<Payment>(`/api/folios/${id}/payments`, { method: 'POST', body: JSON.stringify({ amount: Number(amount), method, reference: reference || null }) });
      setFolio(current => current ? { ...current, payments: [...current.payments, result], paid: current.paid + Number(result.amount), balance: Math.max(0, current.balance - Number(result.amount)) } : current);
      setAmount(''); setReference(''); setMessage('Payment recorded.'); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record payment'); }
  }

  async function closeFolio() {
    setMessage('');
    try { const id = await ensureSelected(); setFolio(await api<Folio>(`/api/folios/${id}/close`, { method: 'POST' })); setMessage('Folio closed successfully.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close folio'); }
  }

  async function printReceipt() {
    setMessage('');
    try {
      const id = await ensureSelected();
      const receipt = await api<Receipt>(`/api/folios/${id}/receipt`);
      const itemRows = receipt.items.map(item => `<tr><td>${escapeHtml(item.description)}</td><td>${escapeHtml(item.category)}</td><td>${Number(item.quantity)}</td><td>${money(item.unit_price)}</td><td>${money(item.discount)}</td><td>${money(item.line_total)}</td></tr>`).join('');
      const paymentRows = receipt.payments.map(payment => `<tr><td>${escapeHtml(payment.method.replace('_', ' '))}</td><td>${escapeHtml(payment.reference || '—')}</td><td>${money(payment.amount)}</td></tr>`).join('');
      const roomNumbers = receipt.rooms.map(room => escapeHtml(room.number)).join(', ') || '—';
      const guestName = escapeHtml(receipt.guest.full_name);
      const phone = escapeHtml(receipt.guest.phone || '');
      const email = receipt.guest.email ? ` · ${escapeHtml(receipt.guest.email)}` : '';
      const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>La Serene Hotel · Folio #${receipt.folio_id}</title><style>body{font-family:Arial,sans-serif;margin:32px;color:#222;line-height:1.45}h1{margin:0 0 4px}.muted{color:#666}section{margin:24px 0}table{width:100%;border-collapse:collapse;margin-top:10px}th,td{border-bottom:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}th{background:#f5f5f5}.totals{max-width:360px;margin-left:auto}.totals div{display:flex;justify-content:space-between;padding:6px 0}.grand{font-size:18px;font-weight:700;border-top:2px solid #222;margin-top:6px;padding-top:10px}.balance{font-weight:700}.footer{margin-top:36px;font-size:12px;color:#666}@media print{body{margin:12mm}button{display:none}}@media(max-width:700px){body{margin:16px;font-size:13px}th,td{padding:6px}}</style></head><body><h1>LA SERENE HOTEL</h1><div class="muted">Guest folio / receipt · #${receipt.folio_id}</div><section><strong>${guestName}</strong><br>${phone}${email}<br>Stay: ${escapeHtml(receipt.stay.check_in)} → ${escapeHtml(receipt.stay.check_out)} · ${receipt.stay.nights} night(s)<br>Room(s): ${roomNumbers}</section><section><h3>Charges</h3><table><thead><tr><th>Description</th><th>Category</th><th>Qty</th><th>Unit</th><th>Discount</th><th>Total</th></tr></thead><tbody>${itemRows || '<tr><td colspan="6">No charges</td></tr>'}</tbody></table></section><section><h3>Payments</h3><table><thead><tr><th>Method</th><th>Reference</th><th>Amount</th></tr></thead><tbody>${paymentRows || '<tr><td colspan="3">No payments</td></tr>'}</tbody></table></section><section class="totals"><div><span>Subtotal</span><strong>${money(receipt.subtotal)}</strong></div><div><span>Discounts</span><strong>${money(receipt.discounts)}</strong></div><div class="grand"><span>Total</span><strong>${money(receipt.total)}</strong></div><div><span>Paid</span><strong>${money(receipt.paid)}</strong></div><div class="balance"><span>Balance</span><strong>${money(receipt.balance)}</strong></div></section><div class="footer">Thank you for staying with La Serene Hotel.</div></body></html>`;
      const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const popup = window.open(url, '_blank', 'width=820,height=900');
      if (!popup) { URL.revokeObjectURL(url); throw new Error('Please allow pop-ups to print the receipt'); }
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
      setMessage('Receipt opened. Use the browser print command to print or save it as PDF.');
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to print receipt'); }
  }

  return <section className="page"><div className="page-heading"><div><p className="muted">Folios, charges, payments and balances</p><h2>Billing</h2></div><span className="room-count">{summaries.length} folios</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="billing-layout">
      <div className="panel"><div className="panel-head"><h2>Folio register</h2></div><div className="billing-list">{summaries.length ? summaries.map(s => <button key={s.folio_id} className={`billing-row ${selected === s.folio_id ? 'selected' : ''}`} onClick={() => openFolio(s.folio_id)}><div><strong>Folio #{s.folio_id} · {s.guest_name}</strong><span>Reservation #{s.reservation_id} · {s.status}</span></div><div><b>{money(s.total)}</b><small>{money(s.balance)} due</small></div></button>) : <p className="muted">No folios available yet.</p>}</div></div>
      <div className="side-stack">
        {folio && <div className="panel"><div className="panel-head"><div><p className="muted">Folio #{folio.id} · reservation #{folio.reservation_id}</p><h2>Guest account</h2></div><span>{folio.status}</span></div><div className="billing-totals"><div><span>Subtotal</span><b>{money(folio.subtotal)}</b></div><div><span>Discounts</span><b>{money(folio.discounts)}</b></div><div className="grand"><span>Total</span><b>{money(folio.total)}</b></div><div><span>Paid</span><b>{money(folio.paid)}</b></div><div className="balance"><span>Balance</span><b>{money(folio.balance)}</b></div></div><div className="folio-items">{folio.items.length ? folio.items.map(item => <article key={item.id}><div><strong>{item.description}</strong><span>{item.category} · {Number(item.quantity)} × {money(item.unit_price)}{Number(item.discount) ? ` · discount ${money(item.discount)}` : ''}</span></div><b>{money(item.line_total)}</b></article>) : <p className="muted">No charges yet.</p>}</div>{canOperate && <div className="billing-actions">{folio.status === 'open' && <button className="secondary-button" onClick={addRoomCharges}>Add room charges</button>}<button className="secondary-button" onClick={printReceipt}>Print receipt</button>{folio.status === 'open' && folio.balance === 0 && <button className="primary-button" onClick={closeFolio}>Close folio</button>}</div>}</div>}
        {canOperate && <form className="panel form-panel" onSubmit={addItem}><div className="panel-head"><h2>Add charge</h2></div><label>Description<input value={description} onChange={e => setDescription(e.target.value)} required /></label><label>Category<select value={category} onChange={e => setCategory(e.target.value)}><option value="service">Service</option><option value="food">Food & beverage</option><option value="room">Room</option><option value="adjustment">Adjustment</option><option value="other">Other</option></select></label><div className="two-col"><label>Quantity<input type="number" min="0.01" step="0.01" value={quantity} onChange={e => setQuantity(e.target.value)} required /></label><label>Unit price<input type="number" min="0" step="0.01" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} required /></label></div><label>Discount<input type="number" min="0" step="0.01" value={discount} onChange={e => setDiscount(e.target.value)} /></label><button className="primary-button" disabled={!selected || !unitPrice}>Add charge</button></form>}
        {canOperate && <form className="panel form-panel" onSubmit={addPayment}><div className="panel-head"><h2>Record payment</h2></div><label>Amount<input type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} required /></label><label>Method<select value={method} onChange={e => setMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label><label>Reference<input value={reference} onChange={e => setReference(e.target.value)} /></label><button className="primary-button" disabled={!selected || !amount}>Record payment</button></form>}
      </div>
    </div>
  </section>;
}
