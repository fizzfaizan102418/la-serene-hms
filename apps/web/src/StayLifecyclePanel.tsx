import React, { useEffect, useMemo, useState } from 'react';

type Room = { id: number; number: string; room_type_id: number; status: string };
type Api = <T>(path: string, options?: RequestInit) => Promise<T>;

type Guest = { id: number; name: string };
type Occupant = { id: number; guest_id: number; guest_name: string; role: string; is_primary: boolean; check_in: string; check_out: string; notes?: string | null };
type RateSegment = { id: number; from_date: string; to_date: string; rate: string | number; discount_percent: string | number; discount_amount: string | number; net_rate: string | number; rate_plan?: string | null; source?: string | null };
type Stay = {
 id: number; room_id: number; room_number: string | null; room_status: string | null; status: string; check_in: string; check_out: string;
 guest: Guest; agreed_rate: string | number; discount_percent: string | number; discount_amount: string | number;
 deposit_required: string | number; deposit_received: string | number; occupants: Occupant[]; rate_segments: RateSegment[];
};
type Overview = { reservation: { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; folio_id: number | null }; stays: Stay[] };

type Props = { reservationId: number; rooms: Room[]; api: Api; onRefresh: () => Promise<void> };

const money = (value: string | number | undefined) => Number(value ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const nights = (a: string, b: string) => Math.max(0, Math.round((new Date(`${b}T00:00:00`).getTime() - new Date(`${a}T00:00:00`).getTime()) / 86400000));

export default function StayLifecyclePanel({ reservationId, rooms, api, onRefresh }: Props) {
 const [overview, setOverview] = useState<Overview | null>(null);
 const [busy, setBusy] = useState(false);
 const [error, setError] = useState('');
 const [message, setMessage] = useState('');
 const [selectedSplit, setSelectedSplit] = useState<number[]>([]);
 const [moveTarget, setMoveTarget] = useState<Record<number, string>>({});
 const [moveRate, setMoveRate] = useState<Record<number, string>>({});
 const [newCheckout, setNewCheckout] = useState('');
 const [extensionRates, setExtensionRates] = useState<Record<number, string>>({});
 const [addGuestId, setAddGuestId] = useState<Record<number, string>>({});
 const [windowNames, setWindowNames] = useState<Record<number, string>>({});

 async function load() {
  setError('');
  try { setOverview(await api<Overview>(`/api/reservations/${reservationId}/stay-overview`)); }
  catch (e) { setError(e instanceof Error ? e.message : 'Unable to load stay lifecycle'); }
 }
 useEffect(() => { void load(); }, [reservationId]);

 const availableRooms = useMemo(() => rooms.filter(room => room.status === 'available'), [rooms]);
 const canSplit = overview && overview.stays.length > 1 && overview.stays.some(stay => stay.status === 'reserved');

 async function run(action: () => Promise<void>, success: string) {
  setBusy(true); setError(''); setMessage('');
  try { await action(); setMessage(success); await load(); await onRefresh(); }
  catch (e) { setError(e instanceof Error ? e.message : 'Operation failed'); }
  finally { setBusy(false); }
 }

 async function moveRoom(stay: Stay) {
  const target = Number(moveTarget[stay.id]);
  if (!target) return;
  const rawRate = moveRate[stay.id]?.trim();
  await run(async () => {
   await api(`/api/stays/${stay.id}/move-rate-aware`, { method: 'POST', body: JSON.stringify({ to_room_id: target, reason: 'Front desk room move', ...(rawRate ? { rate: Number(rawRate) } : {}) }) });
  }, `Stay #${stay.id} moved successfully.`);
  setMoveTarget(prev => ({ ...prev, [stay.id]: '' })); setMoveRate(prev => ({ ...prev, [stay.id]: '' }));
 }

 async function extendStay() {
  if (!overview || !newCheckout) return;
  const rate_overrides = overview.stays.map(stay => ({ stay_id: stay.id, rate: Number(extensionRates[stay.id] || stay.rate_segments.at(-1)?.net_rate || stay.agreed_rate) }));
  await run(async () => {
   await api(`/api/reservations/${reservationId}/extend-rate-aware`, { method: 'POST', body: JSON.stringify({ new_check_out: newCheckout, rate_overrides }) });
  }, `Reservation #${reservationId} extended through ${newCheckout}.`);
  setNewCheckout(''); setExtensionRates({});
 }

 async function replaceRoom(stay: Stay, target: string) {
  if (!target) return;
  await run(async () => {
   await api(`/api/stays/${stay.id}/replace-room`, { method: 'POST', body: JSON.stringify({ to_room_id: Number(target), reason: 'Front desk room replacement' }) });
  }, `Room ${stay.room_number ?? stay.room_id} replaced.`);
 }

 async function cancelStay(stay: Stay) {
  if (!window.confirm(`Cancel the reserved room stay in room ${stay.room_number ?? stay.room_id}?`)) return;
  await run(async () => {
   await api(`/api/stays/${stay.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason: 'Cancelled from Front Desk' }) });
  }, `Stay #${stay.id} cancelled.`);
 }

 async function promote(stay: Stay, occupant: Occupant) {
  await run(async () => {
   await api(`/api/stays/${stay.id}/occupants/${occupant.id}`, { method: 'PATCH', body: JSON.stringify({ is_primary: true, role: 'primary' }) });
  }, `${occupant.guest_name} is now primary for stay #${stay.id}.`);
 }

 async function removeOccupant(stay: Stay, occupant: Occupant) {
  if (!window.confirm(`Remove ${occupant.guest_name} from this stay?`)) return;
  await run(async () => { await api(`/api/stays/${stay.id}/occupants/${occupant.id}`, { method: 'DELETE' }); }, `${occupant.guest_name} removed from stay #${stay.id}.`);
 }

 async function addOccupant(stay: Stay) {
  const guestId = Number(addGuestId[stay.id]);
  if (!guestId) return;
  await run(async () => {
   await api(`/api/stays/${stay.id}/occupants`, { method: 'POST', body: JSON.stringify({ guest_id: guestId, role: 'occupant' }) });
  }, `Guest #${guestId} added to stay #${stay.id}.`);
  setAddGuestId(prev => ({ ...prev, [stay.id]: '' }));
 }

 async function createWindow(stay: Stay) {
  const name = windowNames[stay.id]?.trim();
  if (!name) return;
  await run(async () => {
   await api(`/api/stays/${stay.id}/folio-windows`, { method: 'POST', body: JSON.stringify({ name, payer_type: 'guest', guest_id: stay.guest.id }) });
  }, `Folio window “${name}” created.`);
  setWindowNames(prev => ({ ...prev, [stay.id]: '' }));
 }

 async function splitReservation() {
  if (!overview || selectedSplit.length === 0 || selectedSplit.length === overview.stays.length) return;
  await run(async () => {
   await api(`/api/reservations/${reservationId}/split`, { method: 'POST', body: JSON.stringify({ room_ids: selectedSplit, reason: 'Split from Front Desk' }) });
  }, `Reservation #${reservationId} split into a new reservation.`);
  setSelectedSplit([]);
 }

 if (!overview) return <section className="panel"><div className="panel-head"><h2>Stay & room lifecycle</h2></div><p className="muted">{error || 'Loading room stays…'}</p></section>;

 return <section className="panel" style={{ marginTop: 16 }}>
  <div className="panel-head">
   <div><p className="muted">Reservation → Stay → Occupants → Folio windows</p><h2>#{overview.reservation.id} · {overview.reservation.guest_name}</h2><small className="muted">{overview.reservation.check_in} → {overview.reservation.check_out} · {overview.reservation.status}</small></div>
   <span className="room-count">{overview.stays.length} room stay(s)</span>
  </div>
  {error && <p className="notice" style={{ borderColor: '#d28b8b' }}>{error}</p>}
  {message && <p className="notice">{message}</p>}

  <div style={{ display: 'grid', gap: 12 }}>
   {overview.stays.map(stay => <article key={stay.id} style={{ border: '1px solid #dedbd2', borderRadius: 12, padding: 14, background: '#fbfaf7' }}>
    <div style={{ display: 'flex', gap: 10, justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap' }}>
     <div><strong>Stay #{stay.id} · Room {stay.room_number ?? stay.room_id}</strong><div className="muted">{stay.check_in} → {stay.check_out} · {nights(stay.check_in, stay.check_out)} night(s) · {stay.status}</div></div>
     <div style={{ textAlign: 'right' }}><div><strong>PKR {money(stay.rate_segments.at(-1)?.net_rate ?? stay.agreed_rate)}</strong> / night</div><small className="muted">Deposit {money(stay.deposit_received)} / {money(stay.deposit_required)}</small></div>
    </div>

    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 12, marginTop: 12 }}>
     <div><h3 style={{ margin: 0 }}>Occupants</h3>{stay.occupants.map(o => <div key={o.id} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 7 }}><span style={{ flex: 1 }}><strong>{o.guest_name}</strong> · {o.role}{o.is_primary ? ' · Primary' : ''}</span>{!o.is_primary && <button className="secondary-button small-button" disabled={busy} onClick={() => void promote(stay, o)}>Make primary</button>}<button className="secondary-button small-button" disabled={busy || stay.occupants.length === 1} onClick={() => void removeOccupant(stay, o)}>Remove</button></div>)}<div style={{ display: 'flex', gap: 7, marginTop: 8 }}><input value={addGuestId[stay.id] || ''} onChange={e => setAddGuestId(prev => ({ ...prev, [stay.id]: e.target.value }))} placeholder="Guest ID" inputMode="numeric"/><button className="secondary-button small-button" disabled={busy || !addGuestId[stay.id]} onClick={() => void addOccupant(stay)}>Add occupant</button></div></div>

     <div><h3 style={{ margin: 0 }}>Rate history</h3>{stay.rate_segments.map(segment => <div key={segment.id} style={{ marginTop: 7, padding: 8, borderRadius: 8, background: '#f3f0e9' }}><strong>{segment.from_date} → {segment.to_date}</strong><br/><span>PKR {money(segment.net_rate)} net</span>{Number(segment.discount_percent) > 0 && <small className="muted"> · {segment.discount_percent}% off</small>}<br/><small className="muted">{segment.source || 'manual'}</small></div>)}</div>
    </div>

    <div style={{ marginTop: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
     {stay.status === 'reserved' ? <><select value="" onChange={e => void replaceRoom(stay, e.target.value)} disabled={busy}><option value="">Replace room…</option>{availableRooms.filter(room => room.id !== stay.room_id).map(room => <option key={room.id} value={room.id}>{room.number}</option>)}</select><button className="secondary-button small-button" disabled={busy} onClick={() => void cancelStay(stay)}>Cancel room stay</button></> : <><select value={moveTarget[stay.id] || ''} onChange={e => setMoveTarget(prev => ({ ...prev, [stay.id]: e.target.value }))} disabled={busy}><option value="">Move room…</option>{availableRooms.filter(room => room.id !== stay.room_id).map(room => <option key={room.id} value={room.id}>{room.number}</option>)}</select><input value={moveRate[stay.id] || ''} onChange={e => setMoveRate(prev => ({ ...prev, [stay.id]: e.target.value }))} placeholder="New rate (optional)" inputMode="decimal" style={{ maxWidth: 150 }}/><button className="secondary-button small-button" disabled={busy || !moveTarget[stay.id]} onClick={() => void moveRoom(stay)}>Confirm move</button></>}
    </div>

    <div style={{ marginTop: 10, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
     <input value={windowNames[stay.id] || ''} onChange={e => setWindowNames(prev => ({ ...prev, [stay.id]: e.target.value }))} placeholder="New folio window name"/><button className="secondary-button small-button" disabled={busy || !windowNames[stay.id]} onClick={() => void createWindow(stay)}>Add folio window</button>
    </div>
   </article>)}
  </div>

  {overview.reservation.status === 'checked_in' && <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #dedbd2' }}><h3 style={{ marginTop: 0 }}>Extend stay with rate control</h3><div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}><input type="date" min={overview.reservation.check_out} value={newCheckout} onChange={e => setNewCheckout(e.target.value)}/>{overview.stays.map(stay => <input key={stay.id} value={extensionRates[stay.id] || ''} onChange={e => setExtensionRates(prev => ({ ...prev, [stay.id]: e.target.value }))} placeholder={`Stay #${stay.id} rate`} inputMode="decimal" style={{ maxWidth: 150 }}/>) }<button className="primary-button small-button" disabled={busy || !newCheckout} onClick={() => void extendStay()}>Extend</button></div><small className="muted">Past rate segments remain unchanged; extension nights receive the rates shown above.</small></div>}

  {canSplit && <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid #dedbd2' }}><h3 style={{ marginTop: 0 }}>Split reservation by room</h3><div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>{overview.stays.filter(stay => stay.status === 'reserved').map(stay => <label key={stay.id} style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={selectedSplit.includes(stay.room_id)} onChange={e => setSelectedSplit(prev => e.target.checked ? [...prev, stay.room_id] : prev.filter(id => id !== stay.room_id))}/>{stay.room_number ?? stay.room_id}</label>)}<button className="secondary-button small-button" disabled={busy || selectedSplit.length === 0 || selectedSplit.length >= overview.stays.filter(stay => stay.status === 'reserved').length} onClick={() => void splitReservation()}>Split selected rooms</button></div></div>}
  <div style={{ marginTop: 12 }}><small className="muted">Folio #{overview.reservation.folio_id ?? '—'} · Room-level operations stay attached to their individual Stay records.</small></div>
 </section>;
}
