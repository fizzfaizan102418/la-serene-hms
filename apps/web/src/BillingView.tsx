import React, { useState } from 'react';

type Summary = { folio_id: number; reservation_id: number; guest_name: string; status: string; total: number; paid: number; balance: number };
type Item = { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number; line_total: number };
type Payment = { id: number; amount: number; method: string; reference?: string | null };
type Folio = { id: number; reservation_id: number; status: string; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; total: number; paid: number; balance: number };

type Props = { userRole: string; summaries: Summary[]; onRefresh: () => Promise<void>; api: <T>(path: string, options?: RequestInit) => Promise<T> };

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

  const currentSummary = summaries.find(s => s.folio_id === selected);

  return <section className="page"><div className="page-heading"><div><p className="muted">Folios, charges, payments and balances</p><h2>Billing</h2></div><span className="room-count">{summaries.length} folios</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="billing-layout">
      <div className="panel"><div className="panel-head"><h2>Folio register</h2></div><div className="billing-list">{summaries.length ? summaries.map(s => <button key={s.folio_id} className={`billing-row ${selected === s.folio_id ? 'selected' : ''}`} onClick={() => openFolio(s.folio_id)}><div><strong>Folio #{s.folio_id} · {s.guest_name}</strong><span>Reservation #{s.reservation_id} · {s.status}</span></div><div><b>{Number(s.total).toFixed(2)}</b><small>{Number(s.balance).toFixed(2)} due</small></div></button>) : <p className="muted">No folios available yet.</p>}</div></div>
      <div className="side-stack">
        {folio && <div className="panel"><div className="panel-head"><div><p className="muted">Folio #{folio.id} · reservation #{folio.reservation_id}</p><h2>Guest account</h2></div><span>{folio.status}</span></div><div className="billing-totals"><div><span>Subtotal</span><b>{Number(folio.subtotal).toFixed(2)}</b></div><div><span>Discounts</span><b>{Number(folio.discounts).toFixed(2)}</b></div><div className="grand"><span>Total</span><b>{Number(folio.total).toFixed(2)}</b></div><div><span>Paid</span><b>{Number(folio.paid).toFixed(2)}</b></div><div className="balance"><span>Balance</span><b>{Number(folio.balance).toFixed(2)}</b></div></div><div className="folio-items">{folio.items.length ? folio.items.map(item => <article key={item.id}><div><strong>{item.description}</strong><span>{item.category} · {Number(item.quantity)} × {Number(item.unit_price).toFixed(2)}{Number(item.discount) ? ` · discount ${Number(item.discount).toFixed(2)}` : ''}</span></div><b>{Number(item.line_total).toFixed(2)}</b></article>) : <p className="muted">No charges yet.</p>}</div>{canOperate && folio.status === 'open' && <div className="billing-actions"><button className="secondary-button" onClick={addRoomCharges}>Add room charges</button>{folio.balance === 0 && <button className="primary-button" onClick={closeFolio}>Close folio</button>}</div>}</div>}
        {canOperate && <form className="panel form-panel" onSubmit={addItem}><div className="panel-head"><h2>Add charge</h2></div><label>Description<input value={description} onChange={e => setDescription(e.target.value)} required /></label><label>Category<select value={category} onChange={e => setCategory(e.target.value)}><option value="service">Service</option><option value="food">Food & beverage</option><option value="room">Room</option><option value="adjustment">Adjustment</option><option value="other">Other</option></select></label><div className="two-col"><label>Quantity<input type="number" min="0.01" step="0.01" value={quantity} onChange={e => setQuantity(e.target.value)} required /></label><label>Unit price<input type="number" min="0" step="0.01" value={unitPrice} onChange={e => setUnitPrice(e.target.value)} required /></label></div><label>Discount<input type="number" min="0" step="0.01" value={discount} onChange={e => setDiscount(e.target.value)} /></label><button className="primary-button" disabled={!selected || !unitPrice}>Add charge</button></form>}
        {canOperate && <form className="panel form-panel" onSubmit={addPayment}><div className="panel-head"><h2>Record payment</h2></div><label>Amount<input type="number" min="0.01" step="0.01" value={amount} onChange={e => setAmount(e.target.value)} required /></label><label>Method<select value={method} onChange={e => setMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label><label>Reference<input value={reference} onChange={e => setReference(e.target.value)} /></label><button className="primary-button" disabled={!selected || !amount}>Record payment</button></form>}
      </div>
    </div>
  </section>;
}
