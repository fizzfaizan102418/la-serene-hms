import React, { useMemo, useState } from 'react';
import CheckoutView from './CheckoutView';
import PhaseACompletionPanel from './PhaseACompletionPanel';
import StayLifecyclePanel from './StayLifecyclePanel';

type User = { id: number; username: string; role: string };
type Room = { id: number; number: string; room_type_id: number; status: string };
type Guest = { id: number; full_name: string; phone?: string | null; email?: string | null };
type Reservation = { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; room_ids: number[]; folio_id: number };
type FrontDeskData = { arrivals: Reservation[]; departures: Reservation[]; in_house: Reservation[] };
type SearchResult = { type: 'guest' | 'reservation' | 'room' | 'folio'; id: number; label: string; secondary: string; guest_id?: number; room_id?: number; folio_id?: number; reservation_id?: number };
type WalkInRoom = { roomId: number; occupantGuestId: string; rate: string; discountPercent: string; fixedDiscount: string };

type Props = {
  user: User;
  data: FrontDeskData;
  rooms: Room[];
  reservations: Reservation[];
  guests: Guest[];
  roomTypes: { id: number; name: string; base_rate: number }[];
  onRefresh: () => Promise<void>;
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
};

const addDays = (value: string, amount: number) => { const date = new Date(`${value}T00:00:00`); date.setDate(date.getDate() + amount); return date.toISOString().slice(0, 10); };
const nights = (a: string, b: string) => Math.max(0, Math.round((new Date(`${b}T00:00:00`).getTime() - new Date(`${a}T00:00:00`).getTime()) / 86400000));
const active = (r: Reservation) => r.status === 'reserved' || r.status === 'checked_in';
const money = (value: number | string) => Number(value || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export default function FrontDeskPMSView({ user, data, rooms, reservations, guests, roomTypes, onRefresh, api }: Props) {
  const canOperate = user.role === 'admin' || user.role === 'reception';
  const today = new Date().toISOString().slice(0, 10);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [rackStart, setRackStart] = useState(today);
  const [checkoutReservation, setCheckoutReservation] = useState<Reservation | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [walkInGuest, setWalkInGuest] = useState('');
  const [walkInCheckout, setWalkInCheckout] = useState(addDays(today, 1));
  const [walkInPolicy, setWalkInPolicy] = useState('at_checkout');
  const [walkInDeposit, setWalkInDeposit] = useState('0');
  const [walkInNotes, setWalkInNotes] = useState('');
  const [walkInRooms, setWalkInRooms] = useState<WalkInRoom[]>([]);
  const [showWalkIn, setShowWalkIn] = useState(true);

  const roomById = useMemo(() => new Map(rooms.map(room => [room.id, room])), [rooms]);
  const typeById = useMemo(() => new Map(roomTypes.map(type => [type.id, type])), [roomTypes]);
  const selected = selectedId ? reservations.find(r => r.id === selectedId) ?? null : null;
  const rackDays = useMemo(() => Array.from({ length: 14 }, (_, index) => addDays(rackStart, index)), [rackStart]);
  const availableRooms = useMemo(() => rooms.filter(room => room.status === 'available'), [rooms]);

  function selectReservation(id: number) { setSelectedId(id); setError(''); setMessage(''); }

  async function checkIn(id: number) {
    if (!window.confirm(`Check in reservation #${id}?`)) return;
    setBusy(true); setError(''); setMessage('');
    try { await api(`/api/reservations/${id}/check-in`, { method: 'POST' }); setMessage(`Reservation #${id} checked in.`); await onRefresh(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Unable to check in'); }
    finally { setBusy(false); }
  }

  async function universalSearch(event?: React.FormEvent) {
    event?.preventDefault();
    const query = searchQuery.trim();
    if (!query) { setSearchResults([]); return; }
    setBusy(true); setError('');
    try { const result = await api<{ results: SearchResult[] }>(`/api/front-desk/search?q=${encodeURIComponent(query)}`); setSearchResults(result.results); }
    catch (e) { setError(e instanceof Error ? e.message : 'Search failed'); }
    finally { setBusy(false); }
  }

  function addWalkInRoom(room: Room) {
    if (walkInRooms.some(item => item.roomId === room.id)) return;
    const type = typeById.get(room.room_type_id);
    setWalkInRooms(current => [...current, { roomId: room.id, occupantGuestId: walkInGuest, rate: String(type?.base_rate ?? 0), discountPercent: '0', fixedDiscount: '0' }]);
  }

  function updateWalkInRoom(roomId: number, patch: Partial<WalkInRoom>) { setWalkInRooms(current => current.map(item => item.roomId === roomId ? { ...item, ...patch } : item)); }
  function removeWalkInRoom(roomId: number) { setWalkInRooms(current => current.filter(item => item.roomId !== roomId)); }

  async function createWalkIn(event: React.FormEvent) {
    event.preventDefault();
    if (!walkInGuest) { setError('Select the booking guest before creating a walk-in.'); return; }
    if (!walkInRooms.length) { setError('Assign at least one available room.'); return; }
    if (walkInCheckout <= today) { setError('Walk-in check-out must be after the current business date.'); return; }
    if (walkInRooms.some(room => !room.rate || Number(room.rate) < 0)) { setError('Every walk-in room must have a valid rate.'); return; }
    if (walkInRooms.some(room => room.discountPercent && room.fixedDiscount && Number(room.discountPercent) > 0 && Number(room.fixedDiscount) > 0)) { setError('Use either percentage or fixed discount for each room.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await api<{ reservation_id: number; estimated_total: number }>('/api/front-desk/walk-ins', {
        method: 'POST',
        body: JSON.stringify({
          guest_id: Number(walkInGuest),
          check_out: walkInCheckout,
          payment_policy: walkInPolicy,
          deposit_received: Number(walkInDeposit || 0),
          notes: walkInNotes || null,
          rooms: walkInRooms.map(room => ({
            room_id: room.roomId,
            occupant_guest_id: room.occupantGuestId ? Number(room.occupantGuestId) : Number(walkInGuest),
            agreed_rate: Number(room.rate),
            discount_percent: Number(room.discountPercent || 0),
            fixed_discount: Number(room.fixedDiscount || 0),
          })),
        }),
      });
      setMessage(`Walk-in reservation #${result.reservation_id} checked in. Estimated stay value: PKR ${money(result.estimated_total)}.`);
      setWalkInRooms([]); setWalkInGuest(''); setWalkInDeposit('0'); setWalkInNotes(''); setWalkInCheckout(addDays(today, 1));
      await onRefresh();
      selectReservation(result.reservation_id);
    } catch (e) { setError(e instanceof Error ? e.message : 'Unable to create walk-in'); }
    finally { setBusy(false); }
  }

  function focusSearchGuest(guestId: number) {
    const guest = guests.find(item => item.id === guestId);
    if (!guest) return;
    const matching = reservations.filter(reservation => reservation.guest_id === guestId && active(reservation)).sort((a, b) => Number(b.id) - Number(a.id));
    setMessage(`${guest.full_name} · ${guest.phone || guest.email || 'No contact'} · ${matching.length} active reservation(s).`);
    if (matching[0]) selectReservation(matching[0].id);
  }

  function activateSearchResult(result: SearchResult) {
    if (result.type === 'reservation' && result.id) selectReservation(result.id);
    else if (result.type === 'folio' && result.reservation_id) selectReservation(result.reservation_id);
    else if (result.type === 'room' && result.room_id) {
      const booking = reservations.find(reservation => active(reservation) && reservation.room_ids.includes(result.room_id as number));
      if (booking) selectReservation(booking.id);
      else { setMessage(`Room ${roomById.get(result.room_id)?.number ?? result.room_id} is not attached to an active reservation.`); setWalkInRooms(current => current.some(item => item.roomId === result.room_id) ? current : [...current, { roomId: result.room_id as number, occupantGuestId: walkInGuest, rate: String(typeById.get(roomById.get(result.room_id as number)?.room_type_id || 0)?.base_rate ?? 0), discountPercent: '0', fixedDiscount: '0' }]); setShowWalkIn(true); }
    } else if (result.type === 'guest' && result.guest_id) focusSearchGuest(result.guest_id);
    setSearchResults([]);
  }

  const card = (reservation: Reservation, kind: 'arrival' | 'departure' | 'in-house') => (
    <article className="desk-card" key={reservation.id} onClick={() => selectReservation(reservation.id)} style={{ cursor: 'pointer' }}>
      <div><strong>#{reservation.id} · {reservation.guest_name}</strong><span>{reservation.check_in} → {reservation.check_out} · {nights(reservation.check_in, reservation.check_out)} night(s)</span><small>Room(s): {reservation.room_ids.map(id => roomById.get(id)?.number ?? id).join(', ') || 'Unassigned'}</small></div>
      <div className="desk-actions">
        {kind === 'arrival' && canOperate && <button className="primary-button small-button" disabled={busy} onClick={e => { e.stopPropagation(); void checkIn(reservation.id); }}>Check in</button>}
        {(kind === 'departure' || kind === 'in-house') && canOperate && <button className="primary-button small-button" disabled={busy} onClick={e => { e.stopPropagation(); setCheckoutReservation(reservation); }}>Checkout / folio</button>}
      </div>
    </article>
  );

  if (checkoutReservation) return <CheckoutView folioId={checkoutReservation.folio_id} guestName={checkoutReservation.guest_name} reservationId={checkoutReservation.id} onComplete={async () => { setCheckoutReservation(null); await onRefresh(); }} onClose={() => setCheckoutReservation(null)} api={api} />;

  return <section className="page">
    <div className="page-heading"><div><p className="muted">Reception workstation</p><h2>Front Desk 2.0</h2></div><span className="room-count">{data.in_house.length} in house</span></div>
    {error && <p className="notice" style={{ borderColor: '#d28b8b' }}>{error}</p>}
    {message && <p className="notice">{message}</p>}

    <section className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head"><div><p className="muted">Guest · reservation · room · folio</p><h2>Universal search</h2></div><span>{searchResults.length} result(s)</span></div>
      <form className="search-bar" onSubmit={universalSearch}>
        <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)} placeholder="Search guest name, phone, reservation #, room or folio" aria-label="Universal front desk search" />
        <button className="secondary-button" disabled={busy || !searchQuery.trim()}>Search</button>
        {searchResults.length > 0 && <button type="button" className="link-button" onClick={() => setSearchResults([])}>Clear</button>}
      </form>
      {searchResults.length > 0 && <div className="reservation-list" style={{ marginTop: 10 }}>{searchResults.map(result => <article key={`${result.type}-${result.id}`}>
        <div><strong>{result.label}</strong><span>{result.secondary}</span></div>
        <button type="button" className="secondary-button small-button" onClick={() => activateSearchResult(result)}>{result.type === 'guest' ? 'Open guest' : 'Open'}</button>
      </article>)}</div>}
    </section>

    {canOperate && <section className="panel" style={{ marginBottom: 16 }}>
      <div className="panel-head"><div><p className="muted">Guest → Room → Rate → Occupant → Payment policy</p><h2>Walk-in check-in</h2></div><button className="secondary-button small-button" type="button" onClick={() => setShowWalkIn(value => !value)}>{showWalkIn ? 'Collapse' : 'Open'}</button></div>
      {showWalkIn && <form onSubmit={createWalkIn}>
        <div className="two-col">
          <label>Guest<select value={walkInGuest} onChange={e => { setWalkInGuest(e.target.value); setWalkInRooms(current => current.map(room => ({ ...room, occupantGuestId: room.occupantGuestId || e.target.value }))); }} required><option value="">Select guest</option>{guests.map(guest => <option key={guest.id} value={guest.id}>{guest.full_name}{guest.phone ? ` · ${guest.phone}` : ''}</option>)}</select></label>
          <label>Check-out<input type="date" min={addDays(today, 1)} value={walkInCheckout} onChange={e => setWalkInCheckout(e.target.value)} required /></label>
        </div>
        <div className="two-col">
          <label>Payment policy<select value={walkInPolicy} onChange={e => setWalkInPolicy(e.target.value)}><option value="at_checkin">Full payment at check-in</option><option value="partial">Partial / deposit</option><option value="at_checkout">Settle at checkout</option></select></label>
          <label>Deposit received<input type="number" min="0" step="0.01" value={walkInDeposit} onChange={e => setWalkInDeposit(e.target.value)} /></label>
        </div>
        <label>Notes<textarea rows={2} value={walkInNotes} onChange={e => setWalkInNotes(e.target.value)} placeholder="Optional reception notes" /></label>
        <div style={{ marginTop: 10 }}><strong>Room assignment</strong><div className="desk-actions" style={{ marginTop: 8, flexWrap: 'wrap' }}>{availableRooms.map(room => <button key={room.id} type="button" className={walkInRooms.some(item => item.roomId === room.id) ? 'primary-button small-button' : 'secondary-button small-button'} onClick={() => walkInRooms.some(item => item.roomId === room.id) ? removeWalkInRoom(room.id) : addWalkInRoom(room)}>{room.number} · {typeById.get(room.room_type_id)?.name ?? 'Room'}{walkInRooms.some(item => item.roomId === room.id) ? ' · Selected' : ''}</button>)}{!availableRooms.length && <span className="muted">No rooms currently marked available.</span>}</div></div>
        {walkInRooms.length > 0 && <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>{walkInRooms.map(item => <article key={item.roomId} style={{ border: '1px solid #dedbd2', padding: 12, borderRadius: 10 }}>
          <div className="panel-head"><div><strong>Room {roomById.get(item.roomId)?.number ?? item.roomId}</strong><small className="muted">{typeById.get(roomById.get(item.roomId)?.room_type_id || 0)?.name ?? 'Room type'}</small></div><button type="button" className="link-button" onClick={() => removeWalkInRoom(item.roomId)}>Remove</button></div>
          <div className="two-col"><label>Occupant<select value={item.occupantGuestId} onChange={e => updateWalkInRoom(item.roomId, { occupantGuestId: e.target.value })}><option value="">Same as booking guest</option>{guests.map(guest => <option key={guest.id} value={guest.id}>{guest.full_name}</option>)}</select></label><label>Nightly rate<input type="number" min="0" step="0.01" value={item.rate} onChange={e => updateWalkInRoom(item.roomId, { rate: e.target.value })} /></label></div>
          <div className="two-col"><label>Discount %<input type="number" min="0" max="100" step="0.01" value={item.discountPercent} onChange={e => updateWalkInRoom(item.roomId, { discountPercent: e.target.value, fixedDiscount: e.target.value ? '0' : item.fixedDiscount })} /></label><label>Fixed discount<input type="number" min="0" step="0.01" value={item.fixedDiscount} onChange={e => updateWalkInRoom(item.roomId, { fixedDiscount: e.target.value, discountPercent: e.target.value ? '0' : item.discountPercent })} /></label></div>
        </article>)}</div>}
        <div className="billing-actions" style={{ marginTop: 14 }}><span className="muted">The walk-in is checked in immediately after the transaction commits.</span><button className="primary-button" disabled={busy || !walkInGuest || !walkInRooms.length}>{busy ? 'Checking in…' : 'Create walk-in & check in'}</button></div>
      </form>}
    </section>}

    <section className="stats">{[["Arrivals today", data.arrivals.length], ["Departures today", data.departures.length], ["In house", data.in_house.length], ["Reserved", reservations.filter(r => r.status === 'reserved').length], ["Occupied rooms", rooms.filter(r => r.status === 'occupied').length], ["Dirty rooms", rooms.filter(r => r.status === 'dirty').length]].map(([label, value]) => <article className="stat" key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}</section>

    <div className="desk-grid">
      <div className="panel"><div className="panel-head"><h2>Arrivals</h2><span>{data.arrivals.length}</span></div>{data.arrivals.length ? data.arrivals.map(r => card(r, 'arrival')) : <p className="muted">No arrivals today.</p>}</div>
      <div className="panel"><div className="panel-head"><div><h2>Departures</h2><small className="muted">Due today</small></div><span>{data.departures.length}</span></div>{data.departures.length ? data.departures.map(r => card(r, 'departure')) : <p className="muted">No departures due today.</p>}</div>
      <div className="panel"><div className="panel-head"><div><h2>In house</h2><small className="muted">Checked-in guests</small></div><span>{data.in_house.length}</span></div>{data.in_house.length ? data.in_house.map(r => card(r, 'in-house')) : <p className="muted">No checked-in guests.</p>}</div>
    </div>

    {selected && canOperate && <><StayLifecyclePanel reservationId={selected.id} rooms={rooms} api={api} onRefresh={onRefresh} /><PhaseACompletionPanel reservationId={selected.id} api={api} onRefresh={onRefresh} /></>}

    <section className="panel" style={{ marginTop: 16, overflowX: 'auto' }}>
      <div className="panel-head"><div><p className="muted">Click a booked cell to open its reservation/stay actions</p><h2>Room Rack / 14-Day Calendar</h2></div><div className="desk-actions"><button className="secondary-button small-button" onClick={() => setRackStart(addDays(rackStart, -7))}>← 7 days</button><input type="date" value={rackStart} onChange={e => setRackStart(e.target.value)} style={{ padding: '9px 10px', border: '1px solid #d8d4cb', borderRadius: 10 }} /><button className="secondary-button small-button" onClick={() => setRackStart(addDays(rackStart, 7))}>7 days →</button></div></div>
      <div style={{ display: 'grid', gridTemplateColumns: '120px repeat(14,minmax(74px,1fr))', minWidth: 1180, gap: 4 }}>
        <div style={{ padding: 9, fontWeight: 700 }}>Room</div>
        {rackDays.map(day => <div key={day} style={{ padding: 8, background: '#f4f1ea', borderRadius: 8, textAlign: 'center', fontSize: 11 }}><strong>{day.slice(5)}</strong><br />{new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short' })}</div>)}
        {rooms.map(room => <React.Fragment key={room.id}><div style={{ padding: 9, background: '#f7f5f0', borderRadius: 8 }}><strong>{room.number}</strong><br /><small>{room.status.replace(/_/g, ' ')}</small></div>{rackDays.map(day => { const booking = reservations.find(r => active(r) && r.room_ids.includes(room.id) && day >= r.check_in && day < r.check_out); const current = day === today ? room.status : ''; return <button key={`${room.id}-${day}`} type="button" onClick={() => booking ? selectReservation(booking.id) : day === today && room.status === 'available' ? (setWalkInRooms(current => current.some(item => item.roomId === room.id) ? current : [...current, { roomId: room.id, occupantGuestId: walkInGuest, rate: String(typeById.get(room.room_type_id)?.base_rate ?? 0), discountPercent: '0', fixedDiscount: '0' }]), setShowWalkIn(true)) : undefined} style={{ minHeight: 56, border: '1px solid #dedbd2', borderRadius: 8, background: booking ? '#eef5ed' : '#fbfaf7', padding: 6, textAlign: 'left', cursor: booking || (day === today && room.status === 'available') ? 'pointer' : 'default' }}>{booking ? <><strong style={{ fontSize: 11 }}>#{booking.id}</strong><br /><span style={{ fontSize: 10 }}>{booking.guest_name.slice(0, 14)}</span></> : current ? <span style={{ fontSize: 10, textTransform: 'capitalize' }}>{current.replace(/_/g, ' ')}</span> : <span style={{ color: '#9aa09b', fontSize: 11 }}>{day === today && room.status === 'available' ? 'Walk-in' : 'Free'}</span>}</button>; })}</React.Fragment>)}
      </div>
    </section>
  </section>;
}
