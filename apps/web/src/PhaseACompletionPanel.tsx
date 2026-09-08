import React, { useEffect, useState } from 'react';

type Api = <T>(path: string, options?: RequestInit) => Promise<T>;
type Guest = { id: number; name: string };
type Occupant = { id: number; guest_id: number; guest_name: string; role: string; is_primary: boolean; check_in: string; check_out: string };
type Segment = { id: number; from_date: string; to_date: string; rate: string | number; discount_percent: string | number; discount_amount: string | number; net_rate: string | number };
type Window = { id: number; name: string; payer_type: string; guest_id?: number | null; group_id?: number | null; status: string };
type Stay = { id: number; room_id: number; room_number: string | null; status: string; check_in: string; check_out: string; guest: Guest; deposit_required: string | number; deposit_received: string | number; occupants: Occupant[]; rate_segments: Segment[]; folio_windows: Window[] };
type Overview = { reservation: { id: number; folio_id: number | null; guest_name: string; status: string }; stays: Stay[] };
type Folio = { items: { id: number; description: string; category: string; quantity: number; unit_price: number; discount: number }[] };

const money = (v: string | number) => Number(v ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function PhaseACompletionPanel({ reservationId, api, onRefresh }: { reservationId: number; api: Api; onRefresh: () => Promise<void> }) {
 const [overview, setOverview] = useState<Overview | null>(null);
 const [folio, setFolio] = useState<Folio | null>(null);
 const [busy, setBusy] = useState(false);
 const [error, setError] = useState('');
 const [message, setMessage] = useState('');
 const [changeGuest, setChangeGuest] = useState<Record<number, string>>({});
 const [shareGuest, setShareGuest] = useState<Record<number, string>>({});
 const [rateForm, setRateForm] = useState<Record<number, { from: string; to: string; rate: string; discount: string }>>({});
 const [depositForm, setDepositForm] = useState<Record<number, { target: string; amount: string }>>({});
 const [refundForm, setRefundForm] = useState<Record<number, string>>({});
 const [applyForm, setApplyForm] = useState<Record<number, string>>({});
 const [routeSelection, setRouteSelection] = useState<Record<number, string>>({});

 async function load() {
  setError('');
  try {
   const data = await api<Overview>(`/api/reservations/${reservationId}/stay-overview`); setOverview(data);
   if (data.reservation.folio_id) setFolio(await api<Folio>(`/api/folios/${data.reservation.folio_id}`));
  } catch (e) { setError(e instanceof Error ? e.message : 'Unable to load Phase A operations'); }
 }
 useEffect(() => { void load(); }, [reservationId]);

 async function run(action: () => Promise<void>, success: string) {
  setBusy(true); setError(''); setMessage('');
  try { await action(); setMessage(success); await load(); await onRefresh(); }
  catch (e) { setError(e instanceof Error ? e.message : 'Operation failed'); }
  finally { setBusy(false); }
 }

 if (!overview) return <section className="panel" style={{ marginTop: 16 }}><h2>Phase A operations</h2><p className="muted">{error || 'Loading…'}</p></section>;
 const stayOptions = overview.stays.map(s => `${s.id}: Room ${s.room_number ?? s.room_id}`).join(' · ');
 return <section className="panel" style={{ marginTop: 16 }}>
  <div className="panel-head"><div><p className="muted">Production room/stay controls</p><h2>Phase A completion</h2></div><span className="room-count">{overview.stays.length} stay(s)</span></div>
  {error && <p className="notice" style={{ borderColor: '#d28b8b' }}>{error}</p>}
  {message && <p className="notice">{message}</p>}
  <small className="muted">Reservation #{reservationId} · {stayOptions}</small>

  {overview.stays.map(stay => {
   const rate = rateForm[stay.id] ?? { from: stay.check_in, to: stay.check_out, rate: '', discount: '' };
   const dep = depositForm[stay.id] ?? { target: '', amount: '' };
   return <article key={stay.id} style={{ marginTop: 12, padding: 14, border: '1px solid #dedbd2', borderRadius: 12 }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}><strong>Stay #{stay.id} · Room {stay.room_number ?? stay.room_id} · {stay.status}</strong><span>Deposit PKR {money(stay.deposit_received)} / {money(stay.deposit_required)}</span></div>

    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 12, marginTop: 12 }}>
     <div><h3 style={{ margin: 0 }}>Occupant control</h3>{stay.occupants.map(o => <div key={o.id} style={{ marginTop: 7, padding: 8, background: '#f7f5f0', borderRadius: 8 }}><strong>{o.guest_name}</strong>{o.is_primary ? ' · Primary' : ' · Sharing'}<div style={{ display: 'flex', gap: 6, marginTop: 6 }}><input value={changeGuest[stay.id] ?? ''} onChange={e => setChangeGuest(p => ({ ...p, [stay.id]: e.target.value }))} placeholder={`New guest ID for occupant #${o.id}`} inputMode="numeric"/><button className="secondary-button small-button" disabled={busy || !changeGuest[stay.id]} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/occupants/${o.id}/guest`, { method: 'PATCH', body: JSON.stringify({ guest_id: Number(changeGuest[stay.id]), reason: 'Guest reassignment from Front Desk' }) }); }, `Occupant changed for room ${stay.room_number ?? stay.room_id}.`)}>Change guest</button></div></div>)}<div style={{ display: 'flex', gap: 6, marginTop: 8 }}><input value={shareGuest[stay.id] ?? ''} onChange={e => setShareGuest(p => ({ ...p, [stay.id]: e.target.value }))} placeholder="Share room with guest ID" inputMode="numeric"/><button className="secondary-button small-button" disabled={busy || !shareGuest[stay.id]} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/occupants/share`, { method: 'POST', body: JSON.stringify({ guest_id: Number(shareGuest[stay.id]), reason: 'Room sharing from Front Desk' }) }); }, `Guest added to room ${stay.room_number ?? stay.room_id}.`)}>Add sharing guest</button></div></div>

     <div><h3 style={{ margin: 0 }}>Rate segment</h3><div style={{ display: 'grid', gap: 6, marginTop: 7 }}><div style={{ display: 'flex', gap: 6 }}><input type="date" value={rate.from} min={stay.check_in} max={stay.check_out} onChange={e => setRateForm(p => ({ ...p, [stay.id]: { ...rate, from: e.target.value } }))}/><input type="date" value={rate.to} min={stay.check_in} max={stay.check_out} onChange={e => setRateForm(p => ({ ...p, [stay.id]: { ...rate, to: e.target.value } }))}/></div><input value={rate.rate} onChange={e => setRateForm(p => ({ ...p, [stay.id]: { ...rate, rate: e.target.value } }))} placeholder="Gross nightly rate" inputMode="decimal"/><input value={rate.discount} onChange={e => setRateForm(p => ({ ...p, [stay.id]: { ...rate, discount: e.target.value } }))} placeholder="Discount %" inputMode="decimal"/><button className="secondary-button small-button" disabled={busy || !rate.rate} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/rate-segments`, { method: 'POST', body: JSON.stringify({ from_date: rate.from, to_date: rate.to, rate: Number(rate.rate), discount_percent: Number(rate.discount || 0), discount_amount: 0, source: 'front_desk' }) }); }, `Rate segment added to stay #${stay.id}.`)}>Add rate segment</button></div><small className="muted">Existing segments are never overwritten; the interval is split automatically.</small></div>

     <div><h3 style={{ margin: 0 }}>Deposit lifecycle</h3><small className="muted">Target stay IDs: {overview.stays.map(s => s.id).join(', ')}</small><div style={{ display: 'grid', gap: 6, marginTop: 7 }}><div style={{ display: 'flex', gap: 6 }}><input value={dep.target} onChange={e => setDepositForm(p => ({ ...p, [stay.id]: { ...dep, target: e.target.value } }))} placeholder="Transfer target stay ID" inputMode="numeric"/><input value={dep.amount} onChange={e => setDepositForm(p => ({ ...p, [stay.id]: { ...dep, amount: e.target.value } }))} placeholder="Amount" inputMode="decimal"/><button className="secondary-button small-button" disabled={busy || !dep.target || !dep.amount} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/deposits/transfer`, { method: 'POST', body: JSON.stringify({ target_stay_id: Number(dep.target), amount: Number(dep.amount), reason: 'Front Desk deposit transfer' }) }); }, `Deposit transferred from stay #${stay.id}.`)}>Transfer</button></div><div style={{ display: 'flex', gap: 6 }}><input value={refundForm[stay.id] ?? ''} onChange={e => setRefundForm(p => ({ ...p, [stay.id]: e.target.value }))} placeholder="Refund amount" inputMode="decimal"/><button className="secondary-button small-button" disabled={busy || !refundForm[stay.id]} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/deposits/refund`, { method: 'POST', body: JSON.stringify({ amount: Number(refundForm[stay.id]), payment_method: 'cash', reason: 'Front Desk deposit refund' }) }); }, `Deposit refunded from stay #${stay.id}.`)}>Refund</button>{overview.reservation.folio_id && <><input value={applyForm[stay.id] ?? ''} onChange={e => setApplyForm(p => ({ ...p, [stay.id]: e.target.value }))} placeholder="Apply to folio" inputMode="decimal"/><button className="secondary-button small-button" disabled={busy || !applyForm[stay.id]} onClick={() => void run(async () => { await api(`/api/stays/${stay.id}/deposits/apply`, { method: 'POST', body: JSON.stringify({ folio_id: overview.reservation.folio_id, amount: Number(applyForm[stay.id]), reason: 'Front Desk deposit application' }) }); }, `Deposit applied to folio #${overview.reservation.folio_id}.`)}>Apply</button></>}</div></div></div>
    </div>

    <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid #dedbd2' }}><h3 style={{ margin: 0 }}>Folio windows & routing</h3><div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 7 }}>{stay.folio_windows.map(w => <span key={w.id} style={{ padding: '5px 8px', borderRadius: 999, background: w.status === 'open' ? '#eef5ed' : '#eeeae3' }}>{w.name} · {w.payer_type} · {w.status}</span>)}</div>{folio?.items.length ? <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>{folio.items.map(item => <div key={item.id} style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}><span style={{ flex: 1 }}>#{item.id} · {item.description}</span><select value={routeSelection[item.id] ?? ''} onChange={e => setRouteSelection(p => ({ ...p, [item.id]: e.target.value }))} disabled={busy}><option value="">Route to window…</option>{stay.folio_windows.filter(w => w.status === 'open').map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select><button className="secondary-button small-button" disabled={busy || !routeSelection[item.id]} onClick={() => void run(async () => { await api(`/api/folio-items/${item.id}/route`, { method: 'POST', body: JSON.stringify({ window_id: Number(routeSelection[item.id]), notes: 'Front Desk folio routing' }) }); }, `Folio item #${item.id} routed.`)}>Route</button></div>)}</div> : <small className="muted">No folio items available for routing.</small>}</div>
   </article>;
  })}
 </section>;
}
