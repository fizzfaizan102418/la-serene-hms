import React, { useState } from 'react';

type Summary = { folio_id: number; reservation_id: number; guest_name: string; status: string; total: number; paid: number; balance: number };
type Item = { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number; line_total: number };
type Payment = { id: number; amount: number; method: string; reference?: string | null };
type Folio = { id: number; reservation_id: number; status: string; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; food_service_charge: number; total: number; paid: number; balance: number };
type Props = { userRole: string; summaries: Summary[]; onRefresh: () => Promise<void>; api: <T>(path: string, options?: RequestInit) => Promise<T> };
const money = (value: number) => Number(value || 0).toFixed(2);

export default function BillingView({ userRole, summaries, onRefresh, api }: Props) {
  const canOperate = userRole === 'admin' || userRole === 'reception';
  const isAdmin = userRole === 'admin';
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
    try { const id = await ensureSelected(); setFolio(await api<Folio>(`/api/folios/${id}/room-charges`, { method: 'POST' })); setMessage('Room charges posted.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add room charges'); }
  }
  async function addCharge(event: React.FormEvent) {
    event.preventDefault();
    try {
      const id = await ensureSelected();
      await api<Item>(`/api/folios/${id}/items`, { method: 'POST', body: JSON.stringify({ description, category, quantity: Number(quantity), unit_price: Number(unitPrice), discount: Number(discount || 0) }) });
      setDescription(''); setQuantity('1'); setUnitPrice(''); setDiscount('0');
      setFolio(await api<Folio>(`/api/folios/${id}`)); setMessage('Charge posted.'); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add charge'); }
  }
  async function reverseItem(item: Item) {
    if (!isAdmin) return;
    if (!window.confirm(`Reverse posted charge “${item.description}” for PKR ${money(item.line_total)}? This keeps the original transaction and creates an audited financial reversal.`)) return;
    try {
      const id = await ensureSelected();
      const gross = Number(item.quantity) * Number(item.unit_price);
      const discountAmount = Number(item.discount || 0);
      const net = Math.max(0, gross - discountAmount);
      const lines: Array<{ account: string; direction: 'debit' | 'credit'; amount: number; folio_id: number }> = [
        { account: 'Guest Receivables', direction: 'credit', amount: gross, folio_id: id },
        { account: `Revenue - ${item.category.slice(0, 45) || 'Other'}`, direction: 'debit', amount: gross, folio_id: id },
      ];
      if (discountAmount > 0) {
        lines.push({ account: `Revenue - ${item.category.slice(0, 45) || 'Other'}`, direction: 'credit', amount: discountAmount, folio_id: id });
        lines.push({ account: 'Guest Receivables', direction: 'debit', amount: discountAmount, folio_id: id });
      }
      if (['food', 'restaurant', 'room_service', 'beverage', 'drink', 'snack'].includes(item.category.trim().toLowerCase())) {
        const serviceCharge = Number((net * 0.10).toFixed(2));
        if (serviceCharge > 0) {
          lines.push({ account: 'Revenue - service_charge', direction: 'debit', amount: serviceCharge, folio_id: id });
          lines.push({ account: 'Guest Receivables', direction: 'credit', amount: serviceCharge, folio_id: id });
        }
      }
      await api(`/api/ledger/adjustments`, { method: 'POST', body: JSON.stringify({ description: `Reversal of folio item #${item.id}: ${item.description}`, folio_id: id, lines }) });
      setFolio(await api<Folio>(`/api/folios/${id}`)); setMessage(`Charge #${item.id} reversed. Original transaction remains in the audit trail.`); await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to reverse charge'); }
  }
  async function addPayment(event: React.FormEvent) {
    event.preventDefault();
    try { const id = await ensureSelected(); const result = await api<Payment>(`/api/folios/${id}/payments`, { method: 'POST', body: JSON.stringify({ amount: Number(amount), method, reference: reference || null }) }); setFolio(current => current ? { ...current, payments: [...current.payments, result], paid: current.paid + Number(result.amount), balance: Math.max(0, current.balance - Number(result.amount)) } : current); setAmount(''); setReference(''); setMessage('Payment recorded.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record payment'); }
  }
  async function closeFolio() {
    try { const id = await ensureSelected(); setFolio(await api<Folio>(`/api/folios/${id}/close`, { method: 'POST' })); setMessage('Folio closed successfully.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to close folio'); }
  }
  async function printReceipt() {
    try {
      const id = await ensureSelected();
      const receipt = await api<any>(`/api/folios/${id}/receipt`);
      const rows = receipt.items.map((item: Item) => `<tr><td>${item.description}</td><td>${item.category}</td><td>${item.quantity}</td><td>${money(item.unit_price)}</td><td>${money(item.discount)}</td><td>${money(item.line_total)}</td></tr>`).join('');
      const html = `<!doctype html><html><head><meta charset="utf-8"><title>La Serene Hotel · Folio #${receipt.folio_id}</title><style>body{font-family:Arial;margin:32px;color:#222}table{width:100%;border-collapse:collapse}th,td{padding:8px;border-bottom:1px solid #ddd;text-align:left}.totals{max-width:360px;margin-left:auto}.totals div{display:flex;justify-content:space-between;padding:6px 0}.grand{font-weight:700;border-top:2px solid #222;margin-top:6px}</style></head><body><h1>LA SERENE HOTEL</h1><p>Folio #${receipt.folio_id} · ${receipt.guest.full_name}</p><p>Stay: ${receipt.stay.check_in} → ${receipt.stay.check_out} · ${receipt.stay.nights} night(s)</p><table><thead><tr><th>Description</th><th>Category</th><th>Qty</th><th>Unit</th><th>Discount</th><th>Total</th></tr></thead><tbody>${rows || '<tr><td colspan="6">No charges</td></tr>'}</tbody></table><div class="totals"><div><span>Subtotal</span><b>${money(receipt.subtotal)}</b></div><div><span>Discounts</span><b>${money(receipt.discounts)}</b></div><div><span>Food service charge</span><b>${money(receipt.food_service_charge)}</b></div><div class="grand"><span>Total</span><b>${money(receipt.total)}</b></div><div><span>Paid</span><b>${money(receipt.paid)}</b></div><div><span>Balance</span><b>${money(receipt.balance)}</b></div></div></body></html>`;
      const popup = window.open('', '_blank', 'width=820,height=900'); if (!popup) throw new Error('Please allow pop-ups to print the receipt'); popup.document.write(html); popup.document.close(); popup.focus(); popup.print();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to print receipt'); }
  }

  return <section className="page"><div className="page-heading"><div><p className="muted">Folios, charges, payments and balances</p><h2>Billing</h2></div><span className="room-count">{summaries.length} folios</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="billing-layout"><div className="panel"><div className="panel-head"><h2>Folio register</h2></div><div className="billing-list">{summaries.length ? summaries.map(s => <button key={s.folio_id} className={`billing-row ${selected === s.folio_id ? 'selected' : ''}`} onClick={() => void openFolio(s.folio_id)}><div><strong>Folio #{s.folio_id} · {s.guest_name}</strong><span>Reservation #{s.reservation_id} · {s.status}</span></div><div><b>{money(s.total)}</b><small>{money(s.balance)} due</small></div></button>) : <p className="muted">No folios available yet.</p>}</div></div>
      <div className="side-stack">{folio && <div className="panel"><div className="panel-head"><div><p className="muted">Folio #{folio.id} · reservation #{folio.reservation_id}</p><h2>Guest account</h2></div><span>{folio.status}</span></div><div className="billing-totals"><div><span>Subtotal</span><b>{money(folio.subtotal)}</b></div><div><span>Discounts</span><b>{money(folio.discounts)}</b></div><div><span>Food service charge (10%)</span><b>{money(folio.food_service_charge)}</b></div><div className="grand"><span>Total</span><b>{money(folio.total)}</b></div><div><span>Paid</span><b>{money(folio.paid)}</b></div><div className="balance"><span>Balance</span><b>{money(folio.balance)}</b></div></div><div className="folio-items">{folio.items.length ? folio.items.map(item => <article key={item.id}><div><strong>{item.description}</strong><span>{item.category} · {Number(item.quantity)} × {money(item.unit_price)}{Number(item.discount) ? ` · discount ${money(item.discount)}` : ''}</span></div><div className="desk-actions"><b>{money(item.line_total)}</b><span className="muted">Posted · immutable</span>{isAdmin && folio.status === 'open' && <button className="secondary-button small-button" onClick={() => void reverseItem(item)}>Reverse</button>}</div></article>) : <p className="muted">No charges yet.</p>}</div>{canOperate && <div className="billing-actions">{folio.status === 'open' && <button className="secondary-button" onClick={() => void addRoomCharges()}>Add room charges</button>}<button className="secondary-button" onClick={() => void printReceipt()}>Print receipt</button>{folio.status === 'open' && folio.balance === 0 && <button className="primary-button" onClick={() => void closeFolio()}>Close folio</button>}</div>}</div>}
        {canOperate && folio?.status === 'open' && <form className="panel form-panel" onSubmit={addCharge}><div className="panel-head"><h2>Add charge</h2><span>Charges post immediately</span></div><label>Description<input value={description} onChange={e => setDescription(e.target.value)} required /></label><label>Category<select value={category} onChange={e => setCategory(e.target.value)}><option value="service">Service</option><option value="food">Food & beverage</option><option value="room">Room</option><option value="adjustment">Adjustment</option><option value="other">Other</option></select></label><div className="two-col"><label>Quantity<input type="number" min="0.01" step="0.01" value={quantity} onChange={e => setQuantity(e.target.value)} required /></label><label>Unit price<input type="number" min="0" step="0.01" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} required /></label></div><label>Discount<input type="number" min="0" step="0.01" value={discount} onChange={e => setDiscount(e.target.value)} /></label><button className="primary-button" disabled={!selected || !unitPrice}>Post charge</button></form>}
        {canOperate && folio?.status === 'open' && <form className="panel form-panel" onSubmit={addPayment}><div className="panel-head"><h2>Record payment</h2></div><label>Amount<input type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} required /></label><label>Method<select value={method} onChange={e => setMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label><label>Reference<input value={reference} onChange={e => setReference(e.target.value)} /></label><button className="primary-button" disabled={!selected || !amount}>Record payment</button></form>}
      </div></div></section>;
}
