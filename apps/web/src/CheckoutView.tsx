import React, { useEffect, useState } from 'react';

type Payment = { id: number; amount: number; method: string; reference?: string | null };
type Item = { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number; line_total: number };
type Folio = { id: number; reservation_id: number; status: string; items: Item[]; payments: Payment[]; subtotal: number; discounts: number; food_service_charge: number; total: number; paid: number; balance: number };
type Props = { folioId: number; guestName: string; reservationId: number; onComplete: () => Promise<void>; onClose: () => void; api: <T>(path: string, options?: RequestInit) => Promise<T> };

const money = (value: number) => Number(value || 0).toFixed(2);

export default function CheckoutView({ folioId, guestName, reservationId, onComplete, onClose, api }: Props) {
  const [folio, setFolio] = useState<Folio | null>(null);
  const [method, setMethod] = useState('cash');
  const [amount, setAmount] = useState('');
  const [reference, setReference] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  async function load() {
    await api(`/api/folios/${folioId}/room-charges`, { method: 'POST' });
    setFolio(await api<Folio>(`/api/folios/${folioId}`));
  }

  useEffect(() => { void load().catch(e => setMessage(e instanceof Error ? e.message : 'Unable to load folio')); }, [folioId]);

  async function settle(e: React.FormEvent) {
    e.preventDefault();
    const value = Number(amount);
    if (!folio || !value || value <= 0) return;
    setBusy(true); setMessage('');
    try {
      await api(`/api/folios/${folio.id}/payments`, { method: 'POST', body: JSON.stringify({ amount: value, method, reference: reference || null }) });
      setAmount(''); setReference(''); await load(); setMessage('Payment recorded.');
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record payment'); }
    finally { setBusy(false); }
  }

  async function completeCheckout() {
    if (!folio || Number(folio.balance) !== 0) { setMessage('The folio must be fully settled before checkout.'); return; }
    if (!window.confirm(`Complete checkout for ${guestName}?`)) return;
    setBusy(true); setMessage('');
    try {
      // Atomic checkout owns the final state transition: validates zero balance,
      // closes the folio, completes stays, dirties occupied rooms, audits, commits.
      await api(`/api/reservations/${reservationId}/checkout`, { method: 'POST' });
      setMessage('Checkout completed.');
      await printFolio();
      await onComplete();
      onClose();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to complete checkout'); }
    finally { setBusy(false); }
  }

  async function printFolio() {
    const receipt = await api<Record<string, unknown>>(`/api/folios/${folioId}/receipt`);
    const popup = window.open('', '_blank', 'width=900,height=750');
    if (!popup) { setMessage('Pop-up blocked. Allow pop-ups to print the final folio.'); return; }
    const items = (receipt.items as Array<Record<string, unknown>>).map(item => `<tr><td>${String(item.description)}</td><td>${String(item.category)}</td><td>${Number(item.quantity).toFixed(2)}</td><td>${money(Number(item.line_total))}</td></tr>`).join('');
    const payments = (receipt.payments as Array<Record<string, unknown>>).map(p => `<tr><td>${String(p.method)}</td><td>${String(p.reference || '')}</td><td>${money(Number(p.amount))}</td></tr>`).join('');
    popup.document.write(`<!doctype html><html><head><title>Folio #${folioId}</title><style>body{font-family:Arial,sans-serif;padding:28px;color:#17211b}h1,h2{margin:0 0 8px}p{margin:5px 0}table{width:100%;border-collapse:collapse;margin-top:18px}th,td{border-bottom:1px solid #ddd;padding:8px;text-align:left}.total{margin-top:18px;width:320px;margin-left:auto}.total div{display:flex;justify-content:space-between;padding:6px}.grand{font-size:18px;font-weight:700}.meta{margin-bottom:18px}@media print{button{display:none}}</style></head><body><h1>LA SERENE HOTEL</h1><h2>Final Guest Folio #${folioId}</h2><div class="meta"><p><b>Guest:</b> ${guestName}</p><p><b>Reservation:</b> #${reservationId}</p><p><b>Status:</b> Settled / Checked out</p></div><table><thead><tr><th>Description</th><th>Category</th><th>Qty</th><th>Amount</th></tr></thead><tbody>${items}</tbody></table><h3>Payments</h3><table><thead><tr><th>Method</th><th>Reference</th><th>Amount</th></tr></thead><tbody>${payments}</tbody></table><div class="total"><div><span>Subtotal</span><b>${money(Number(receipt.subtotal))}</b></div><div><span>Discounts</span><b>- ${money(Number(receipt.discounts))}</b></div><div><span>Food service charge</span><b>${money(Number(receipt.food_service_charge))}</b></div><div class="grand"><span>Total</span><b>${money(Number(receipt.total))}</b></div><div><span>Paid</span><b>${money(Number(receipt.paid))}</b></div><div class="grand"><span>Balance</span><b>${money(Number(receipt.balance))}</b></div></div><script>window.onload=()=>window.print()</script></body></html>`);
    popup.document.close();
  }

  if (!folio) return <section className="panel"><div className="panel-head"><h2>Checkout · {guestName}</h2><button className="link-button" onClick={onClose}>Close</button></div><p className="muted">Loading folio and current room charges…</p></section>;
  return <section className="panel checkout-panel">
    <div className="panel-head"><div><p className="muted">Final settlement</p><h2>Checkout · {guestName}</h2></div><button className="link-button" onClick={onClose}>Back</button></div>
    {message && <p className="notice">{message}</p>}
    <div className="billing-totals"><div><span>Subtotal</span><b>{money(folio.subtotal)}</b></div><div><span>Discounts</span><b>- {money(folio.discounts)}</b></div><div><span>Food service charge</span><b>{money(folio.food_service_charge)}</b></div><div className="grand"><span>Grand total</span><b>{money(folio.total)}</b></div><div><span>Paid</span><b>{money(folio.paid)}</b></div><div className="balance"><span>Outstanding balance</span><b>{money(folio.balance)}</b></div></div>
    <section><h3>Folio charges</h3><div className="folio-items">{folio.items.map(item => <article key={item.id}><div><strong>{item.description}</strong><span>{item.category} · {item.quantity} × {money(item.unit_price)}</span></div><b>{money(item.line_total)}</b></article>)}</div></section>
    {folio.payments.length > 0 && <section style={{ marginTop: 16 }}><h3>Payments</h3><div className="folio-items">{folio.payments.map(payment => <article key={payment.id}><div><strong>{payment.method}</strong><span>{payment.reference || 'No reference'}</span></div><b>{money(payment.amount)}</b></article>)}</div></section>}
    {Number(folio.balance) > 0 && <form className="form-panel" style={{ marginTop: 18 }} onSubmit={settle}><h3>Collect remaining balance</h3><div className="two-col"><label>Payment method<select value={method} onChange={e => setMethod(e.target.value)}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label><label>Amount<input type="number" min="0.01" step="0.01" max={folio.balance} value={amount} onChange={e => setAmount(e.target.value)} /></label></div><label>Reference<input value={reference} onChange={e => setReference(e.target.value)} placeholder="Optional receipt / terminal reference" /></label><button className="primary-button" disabled={busy || !amount}>{busy ? 'Saving…' : `Record payment · ${money(Number(amount || 0))}`}</button></form>}
    <div className="billing-actions"><button className="secondary-button" onClick={() => void printFolio()}>Print folio</button>{Number(folio.balance) === 0 && <button className="primary-button" disabled={busy} onClick={() => void completeCheckout()}>Complete checkout & print</button>}</div>
  </section>;
}
