import React, { useMemo, useState } from 'react';

type User = { id: number; username: string; role: string };
type Guest = { id: number; full_name: string; phone?: string | null; email?: string | null };
type RoomType = { id: number; name: string; base_rate: number };
type Room = { id: number; number: string; room_type_id: number; status: string };
type Reservation = { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; room_ids: number[]; folio_id: number };
type Group = { id: number; code: string; name: string; status: string };
type Stay = { id: number; room_id: number; room_number: string; guest_id: number | null; guest_name: string | null; status: string; check_in: string; check_out: string; agreed_rate: number; discount_percent: number; discount_amount: number; payment_due_policy: string; deposit_required: number; deposit_received: number };

type Props = {
  user: User;
  guests: Guest[];
  rooms: Room[];
  roomTypes: RoomType[];
  reservations: Reservation[];
  onRefresh: () => Promise<void>;
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
};

type Allocation = { roomId: number; occupantId: string; rate: string; discountPercent: string; fixedDiscount: string };

const money = (value: number) => Number(value || 0).toFixed(2);

export default function ReservationsPMSView({ user, guests, rooms, roomTypes, reservations, onRefresh, api }: Props) {
  const canOperate = user.role === 'admin' || user.role === 'reception';
  const today = new Date().toISOString().slice(0, 10);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [checkIn, setCheckIn] = useState(today);
  const [checkOut, setCheckOut] = useState('');
  const [bookingGuest, setBookingGuest] = useState('');
  const [available, setAvailable] = useState<Room[]>([]);
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupId, setGroupId] = useState('');
  const [notes, setNotes] = useState('');
  const [paymentPolicy, setPaymentPolicy] = useState('at_checkout');
  const [deposit, setDeposit] = useState('0');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [stays, setStays] = useState<Stay[]>([]);
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]);
  const selected = selectedId ? reservations.find(r => r.id === selectedId) ?? null : null;

  async function loadGroups() {
    try { setGroups(await api<Group[]>('/api/groups')); } catch { /* optional panel */ }
  }

  async function findAvailable() {
    setMessage('');
    setAllocations([]);
    if (!checkOut || checkOut <= checkIn) { setMessage('Check-out must be after check-in.'); return; }
    try {
      const result = await api<{ rooms: Room[] }>(`/api/availability?check_in=${checkIn}&check_out=${checkOut}`);
      setAvailable(result.rooms);
      await loadGroups();
      setMessage(`${result.rooms.length} room(s) available.`);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Availability check failed'); }
  }

  function addRoom(room: Room) {
    if (allocations.some(a => a.roomId === room.id)) return;
    const type = typeById.get(room.room_type_id);
    setAllocations(current => [...current, { roomId: room.id, occupantId: bookingGuest, rate: String(type?.base_rate ?? 0), discountPercent: '0', fixedDiscount: '0' }]);
  }

  function updateAllocation(roomId: number, patch: Partial<Allocation>) {
    setAllocations(current => current.map(item => item.roomId === roomId ? { ...item, ...patch } : item));
  }

  function removeRoom(roomId: number) { setAllocations(current => current.filter(item => item.roomId !== roomId)); }

  async function createReservation(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage('');
    try {
      const payload = {
        guest_id: Number(bookingGuest),
        check_in: checkIn,
        check_out: checkOut,
        room_ids: allocations.map(a => a.roomId),
        notes: notes || null,
      };
      const created = await api<Reservation>('/api/reservations', { method: 'POST', body: JSON.stringify(payload) });
      const staysPayload = allocations.map(a => ({
        room_id: a.roomId,
        guest_id: a.occupantId ? Number(a.occupantId) : Number(bookingGuest),
        agreed_rate: Number(a.rate || 0),
        discount_percent: Number(a.discountPercent || 0),
        discount_amount: Number(a.fixedDiscount || 0),
        payment_due_policy: paymentPolicy,
        deposit_required: Number(a.rate || 0) * Number(checkOut && checkIn ? Math.max(1, Math.round((new Date(`${checkOut}T00:00:00`).getTime() - new Date(`${checkIn}T00:00:00`).getTime()) / 86400000)) : 1),
        deposit_received: Number(deposit || 0),
        notes: notes || null,
      }));
      await api(`/api/reservations/${created.id}/stays`, { method: 'POST', body: JSON.stringify(staysPayload) });
      if (groupId) await api(`/api/groups/${groupId}/reservations`, { method: 'POST', body: JSON.stringify({ reservation_id: created.id, role: 'member' }) });
      setMessage(`Reservation #${created.id} created with ${allocations.length} room(s).`);
      setBookingGuest(''); setAllocations([]); setAvailable([]); setGroupId(''); setNotes(''); setDeposit('0');
      await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to create reservation'); }
    finally { setBusy(false); }
  }

  async function openReservation(reservation: Reservation) {
    setSelectedId(reservation.id); setMessage('');
    try { setStays(await api<Stay[]>(`/api/reservations/${reservation.id}/stays`)); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load stay details'); }
  }

  async function cancelReservation() {
    if (!selected) return;
    setBusy(true);
    try { await api(`/api/reservations/${selected.id}/cancel`, { method: 'POST', body: JSON.stringify({ reason: 'Cancelled from reservations register' }) }); setMessage(`Reservation #${selected.id} cancelled.`); setSelectedId(null); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to cancel reservation'); }
    finally { setBusy(false); }
  }

  async function noShowReservation() {
    if (!selected) return;
    setBusy(true);
    try { await api(`/api/reservations/${selected.id}/no-show`, { method: 'POST', body: JSON.stringify({ reason: 'Marked no-show from reservations register' }) }); setMessage(`Reservation #${selected.id} marked no-show.`); setSelectedId(null); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to mark no-show'); }
    finally { setBusy(false); }
  }

  async function extendSelected() {
    if (!selected) return;
    const newDate = window.prompt('New check-out date (YYYY-MM-DD)', selected.check_out);
    if (!newDate) return;
    setBusy(true);
    try { await api(`/api/reservations/${selected.id}/extend`, { method: 'POST', body: JSON.stringify({ new_check_out: newDate }) }); setMessage(`Reservation #${selected.id} extended to ${newDate}.`); await openReservation({ ...selected, check_out: newDate }); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to extend reservation'); }
    finally { setBusy(false); }
  }

  return <section className="page">
    <div className="page-heading"><div><p className="muted">PMS reservation control</p><h2>Reservations</h2></div><span className="room-count">{reservations.length} bookings</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="workspace" style={{ gridTemplateColumns: 'minmax(0, 1.4fr) minmax(360px, 1fr)' }}>
      <div className="panel">
        <div className="panel-head"><h2>Reservation register</h2><span>{reservations.filter(r => r.status === 'reserved').length} confirmed</span></div>
        <div className="reservation-list">
          {reservations.length ? reservations.map(r => <article key={r.id} onClick={() => openReservation(r)} style={{ cursor: 'pointer' }}>
            <div><strong>#{r.id} · {r.guest_name}</strong><span>{r.check_in} → {r.check_out} · {r.room_ids.map(id => rooms.find(room => room.id === id)?.number ?? id).join(', ') || 'No room'}</span></div><b>{r.status}</b>
          </article>) : <p className="muted">No reservations yet.</p>}
        </div>
        {selected && <div className="panel" style={{ marginTop: 16 }}>
          <div className="panel-head"><h2>Reservation #{selected.id}</h2><b>{selected.status}</b></div>
          <p><strong>{selected.guest_name}</strong> · {selected.check_in} → {selected.check_out}</p>
          <div className="reservation-list">{stays.length ? stays.map(s => <article key={s.id}><div><strong>Room {s.room_number}</strong><span>Occupant: {s.guest_name || 'Unassigned'} · Net rate {money(Number(s.agreed_rate))} · {Number(s.discount_percent)}% discount</span></div><small>{s.payment_due_policy} · deposit {money(Number(s.deposit_received))}/{money(Number(s.deposit_required))}</small></article>) : <p className="muted">No stay detail available.</p>}</div>
          {canOperate && <div className="desk-actions" style={{ marginTop: 12 }}>{selected.status === 'reserved' && <><button className="secondary-button small-button" disabled={busy} onClick={cancelReservation}>Cancel</button><button className="secondary-button small-button" disabled={busy || selected.check_in > today} onClick={noShowReservation}>No-show</button></>}{selected.status === 'checked_in' && <button className="primary-button small-button" disabled={busy} onClick={extendSelected}>Extend stay</button>}</div>}
        </div>}
      </div>

      {canOperate && <form className="panel form-panel" onSubmit={createReservation}>
        <div className="panel-head"><h2>New reservation</h2><span>Multi-room</span></div>
        <label>Booking guest<select value={bookingGuest} onChange={e => { setBookingGuest(e.target.value); setAllocations(current => current.map(a => ({ ...a, occupantId: a.occupantId || e.target.value }))); }} required><option value="">Select guest</option>{guests.map(g => <option key={g.id} value={g.id}>{g.full_name}{g.phone ? ` · ${g.phone}` : ''}</option>)}</select></label>
        <div className="two-col"><label>Check-in<input type="date" value={checkIn} onChange={e => setCheckIn(e.target.value)} required /></label><label>Check-out<input type="date" value={checkOut} onChange={e => setCheckOut(e.target.value)} required /></label></div>
        <button type="button" className="secondary-button" onClick={findAvailable}>Check room availability</button>
        {available.length > 0 && <div className="availability-picker">{available.map(room => <button type="button" key={room.id} className={allocations.some(a => a.roomId === room.id) ? 'selected' : ''} onClick={() => addRoom(room)}><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Room'} · {money(Number(typeById.get(room.room_type_id)?.base_rate ?? 0))}</span></button>)}</div>}
        {allocations.length > 0 && <div className="reservation-list">{allocations.map(a => { const room = rooms.find(r => r.id === a.roomId)!; return <article key={a.roomId}><div style={{ width: '100%' }}><div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}><strong>Room {room.number}</strong><button type="button" className="link-button" onClick={() => removeRoom(a.roomId)}>Remove</button></div><div className="two-col" style={{ marginTop: 8 }}><label>Occupant<select value={a.occupantId} onChange={e => updateAllocation(a.roomId, { occupantId: e.target.value })}><option value="">Select occupant</option>{guests.map(g => <option key={g.id} value={g.id}>{g.full_name}</option>)}</select></label><label>Negotiated rate<input type="number" min="0" step="0.01" value={a.rate} onChange={e => updateAllocation(a.roomId, { rate: e.target.value })} /></label></div><div className="two-col"><label>Discount %<input type="number" min="0" max="100" step="0.01" value={a.discountPercent} onChange={e => updateAllocation(a.roomId, { discountPercent: e.target.value, fixedDiscount: '0' })} /></label><label>Fixed discount<input type="number" min="0" step="0.01" value={a.fixedDiscount} onChange={e => updateAllocation(a.roomId, { fixedDiscount: e.target.value, discountPercent: '0' })} /></label></div></div></article>; })}</div>}
        <div className="two-col"><label>Payment policy<select value={paymentPolicy} onChange={e => setPaymentPolicy(e.target.value)}><option value="at_booking">At booking</option><option value="at_checkin">At check-in</option><option value="at_checkout">At check-out</option><option value="partial">Partial / staged</option></select></label><label>Deposit received<input type="number" min="0" step="0.01" value={deposit} onChange={e => setDeposit(e.target.value)} /></label></div>
        <label>Group / organizer<select value={groupId} onChange={e => setGroupId(e.target.value)} onFocus={loadGroups}><option value="">No group</option>{groups.map(g => <option key={g.id} value={g.id}>{g.code} · {g.name}</option>)}</select></label>
        <label>Notes<textarea value={notes} onChange={e => setNotes(e.target.value)} rows={2} placeholder="Company, organizer, special instructions, room-sharing notes…" /></label>
        <button className="primary-button" disabled={busy || !bookingGuest || !checkOut || !allocations.length || checkOut <= checkIn}>{busy ? 'Saving…' : `Create reservation · ${allocations.length} room${allocations.length === 1 ? '' : 's'}`}</button>
      </form>}
    </div>
  </section>;
}
