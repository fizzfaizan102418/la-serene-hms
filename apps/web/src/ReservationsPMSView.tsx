import React, { useMemo, useState } from 'react';

type User = { id: number; username: string; role: string };
type Guest = { id: number; full_name: string; phone?: string | null; email?: string | null };
type RoomType = { id: number; name: string; base_rate: number };
type Room = { id: number; number: string; room_type_id: number; status: string };
type Reservation = { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; room_ids: number[]; folio_id: number };
type Group = { id: number; code: string; name: string; status: string };
type Stay = { id: number; room_id: number; room_number: string; guest_id: number | null; guest_name: string | null; status: string; check_in: string; check_out: string; agreed_rate: number; discount_percent: number; discount_amount: number; payment_due_policy: string; deposit_required: number; deposit_received: number };
type Occupant = { id: number; stay_id: number; guest_id: number; guest_name: string; role: string; is_primary: boolean; check_in: string; check_out: string; notes?: string | null };
type RateSegment = { id: number; stay_id: number; from_date: string; to_date: string; rate: number; discount_percent: number; discount_amount: number; rate_plan?: string | null; source: string; notes?: string | null };
type DepositTransaction = { id: number; stay_id: number; folio_id?: number | null; transaction_type: string; amount: number; payment_method?: string | null; reference?: string | null; notes?: string | null; created_at: string };
type DepositResponse = { balance: number; transactions: DepositTransaction[] };
type RoomMove = { id: number; stay_id: number; from_room_id: number; to_room_id: number; effective_at: string; reason?: string | null; created_by?: number | null };

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

type StayDetail = {
  occupants: Occupant[];
  rateSegments: RateSegment[];
  deposits: DepositResponse;
  moves: RoomMove[];
};

const money = (value: number) => Number(value || 0).toFixed(2);
const todayValue = () => new Date().toISOString().slice(0, 10);

export default function ReservationsPMSView({ user, guests, rooms, roomTypes, reservations, onRefresh, api }: Props) {
  const canOperate = user.role === 'admin' || user.role === 'reception';
  const today = todayValue();
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
  const [details, setDetails] = useState<Record<number, StayDetail>>({});
  const [openStayId, setOpenStayId] = useState<number | null>(null);
  const [newOccupant, setNewOccupant] = useState<Record<number, string>>({});
  const [occupantRole, setOccupantRole] = useState<Record<number, string>>({});
  const [primaryOccupant, setPrimaryOccupant] = useState<Record<number, boolean>>({});
  const [newRate, setNewRate] = useState<Record<number, string>>({});
  const [newRateFrom, setNewRateFrom] = useState<Record<number, string>>({});
  const [newRateTo, setNewRateTo] = useState<Record<number, string>>({});
  const [newRateDiscount, setNewRateDiscount] = useState<Record<number, string>>({});
  const [depositAmount, setDepositAmount] = useState<Record<number, string>>({});
  const [depositType, setDepositType] = useState<Record<number, string>>({});
  const [depositMethod, setDepositMethod] = useState<Record<number, string>>({});
  const [depositReference, setDepositReference] = useState<Record<number, string>>({});
  const [moveTarget, setMoveTarget] = useState<Record<number, string>>({});
  const [moveReason, setMoveReason] = useState<Record<number, string>>({});
  const [splitRooms, setSplitRooms] = useState<number[]>([]);
  const [splitGuest, setSplitGuest] = useState('');
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]);
  const roomById = useMemo(() => new Map(rooms.map(r => [r.id, r])), [rooms]);
  const selected = selectedId ? reservations.find(r => r.id === selectedId) ?? null : null;

  async function loadGroups() {
    try { setGroups(await api<Group[]>('/api/groups')); } catch { /* optional */ }
  }

  async function findAvailable() {
    setMessage('');
    setAllocations([]);
    if (!checkOut || checkOut <= checkIn) { setMessage('Check-out must be after check-in.'); return; }
    try {
      const result = await api<{ rooms: Room[] }>(`/api/availability?check_in=${checkIn}&check_out=${checkOut}`);
      setAvailable(result.rooms);
      await loadGroups();
      setMessage(`${result.rooms.length} room(s) available for the selected dates.`);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Availability check failed'); }
  }

  function addRoom(room: Room) {
    if (allocations.some(a => a.roomId === room.id)) return;
    const type = typeById.get(room.room_type_id);
    setAllocations(current => [...current, { roomId: room.id, occupantId: bookingGuest, rate: String(type?.base_rate ?? 0), discountPercent: '0', fixedDiscount: '0' }]);
  }
  function updateAllocation(roomId: number, patch: Partial<Allocation>) { setAllocations(current => current.map(item => item.roomId === roomId ? { ...item, ...patch } : item)); }
  function removeRoom(roomId: number) { setAllocations(current => current.filter(item => item.roomId !== roomId)); }

  async function createReservation(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage('');
    try {
      await api<Reservation>('/api/reservations/workflow', {
        method: 'POST',
        body: JSON.stringify({
          guest_id: Number(bookingGuest),
          check_in: checkIn,
          check_out: checkOut,
          rooms: allocations.map(a => ({ room_id: a.roomId, occupant_guest_id: a.occupantId ? Number(a.occupantId) : Number(bookingGuest), agreed_rate: Number(a.rate || 0), discount_percent: Number(a.discountPercent || 0), fixed_discount: Number(a.fixedDiscount || 0) })),
          group_id: groupId ? Number(groupId) : null,
          notes: notes || null,
          payment_policy: paymentPolicy,
          deposit_received: Number(deposit || 0),
        }),
      });
      setMessage(`Reservation created with ${allocations.length} room(s).`);
      setBookingGuest(''); setAllocations([]); setAvailable([]); setGroupId(''); setNotes(''); setDeposit('0');
      await onRefresh();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to create reservation'); }
    finally { setBusy(false); }
  }

  async function loadStayDetails(stayId: number) {
    try {
      const [occupants, rateSegments, deposits, moves] = await Promise.all([
        api<Occupant[]>(`/api/stays/${stayId}/occupants`),
        api<RateSegment[]>(`/api/stays/${stayId}/rate-segments`),
        api<DepositResponse>(`/api/stays/${stayId}/deposits`),
        api<RoomMove[]>(`/api/stays/${stayId}/moves`),
      ]);
      setDetails(current => ({ ...current, [stayId]: { occupants, rateSegments, deposits, moves } }));
      setOpenStayId(stayId);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load stay details'); }
  }

  async function openReservation(reservation: Reservation) {
    setSelectedId(reservation.id); setMessage(''); setSplitRooms([]); setSplitGuest('');
    try {
      const result = await api<Stay[]>(`/api/reservations/${reservation.id}/stays`);
      setStays(result);
      setDetails({});
      if (result[0]) await loadStayDetails(result[0].id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load stay details'); }
  }

  async function refreshStay(stayId: number) {
    await loadStayDetails(stayId);
    await onRefresh();
    if (selected) {
      try { setStays(await api<Stay[]>(`/api/reservations/${selected.id}/stays`)); } catch { /* refresh is best effort */ }
    }
  }

  async function addOccupant(stay: Stay) {
    const guestId = Number(newOccupant[stay.id] || 0);
    if (!guestId) return setMessage('Select a guest to add as an occupant.');
    setBusy(true); setMessage('');
    try {
      await api(`/api/stays/${stay.id}/occupants`, { method: 'POST', body: JSON.stringify({ guest_id: guestId, role: occupantRole[stay.id] || 'occupant', is_primary: Boolean(primaryOccupant[stay.id]), notes: null }) });
      setMessage(`Occupant added to room ${stay.room_number}.`);
      setNewOccupant(current => ({ ...current, [stay.id]: '' }));
      setPrimaryOccupant(current => ({ ...current, [stay.id]: false }));
      await refreshStay(stay.id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add occupant'); }
    finally { setBusy(false); }
  }

  async function addRateSegment(stay: Stay) {
    const from = newRateFrom[stay.id] || '';
    const to = newRateTo[stay.id] || '';
    const rate = Number(newRate[stay.id] || 0);
    const discountPercent = Number(newRateDiscount[stay.id] || 0);
    if (!from || !to || !rate || to <= from) return setMessage('Enter a valid rate segment date range and rate.');
    setBusy(true); setMessage('');
    try {
      await api(`/api/stays/${stay.id}/rate-segments`, { method: 'POST', body: JSON.stringify({ from_date: from, to_date: to, rate, discount_percent: discountPercent, discount_amount: 0, source: 'front_desk', notes: null }) });
      setMessage(`Rate segment added for room ${stay.room_number}.`);
      setNewRate(current => ({ ...current, [stay.id]: '' }));
      setNewRateFrom(current => ({ ...current, [stay.id]: '' }));
      setNewRateTo(current => ({ ...current, [stay.id]: '' }));
      setNewRateDiscount(current => ({ ...current, [stay.id]: '0' }));
      await refreshStay(stay.id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add rate segment'); }
    finally { setBusy(false); }
  }

  async function addDeposit(stay: Stay) {
    const amount = Number(depositAmount[stay.id] || 0);
    if (!amount) return setMessage('Enter a deposit amount.');
    setBusy(true); setMessage('');
    try {
      await api(`/api/stays/${stay.id}/deposits`, { method: 'POST', body: JSON.stringify({ transaction_type: depositType[stay.id] || 'received', amount, payment_method: depositMethod[stay.id] || 'cash', reference: depositReference[stay.id] || null, notes: null }) });
      setMessage(`Deposit ${depositType[stay.id] || 'received'} recorded for room ${stay.room_number}.`);
      setDepositAmount(current => ({ ...current, [stay.id]: '' }));
      setDepositReference(current => ({ ...current, [stay.id]: '' }));
      await refreshStay(stay.id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record deposit'); }
    finally { setBusy(false); }
  }

  async function moveRoom(stay: Stay) {
    const target = Number(moveTarget[stay.id] || 0);
    if (!target) return setMessage('Select a destination room.');
    setBusy(true); setMessage('');
    try {
      await api(`/api/stays/${stay.id}/move`, { method: 'POST', body: JSON.stringify({ to_room_id: target, reason: moveReason[stay.id] || null }) });
      setMessage(`Room move completed: ${stay.room_number} → ${roomById.get(target)?.number ?? target}.`);
      setMoveTarget(current => ({ ...current, [stay.id]: '' }));
      setMoveReason(current => ({ ...current, [stay.id]: '' }));
      await refreshStay(stay.id);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to move room'); }
    finally { setBusy(false); }
  }

  async function splitReservation() {
    if (!selected || splitRooms.length === 0 || splitRooms.length === selected.room_ids.length) return setMessage('Select one or more rooms while leaving at least one room on the original reservation.');
    setBusy(true); setMessage('');
    try {
      const result = await api<{ new_reservation_id: number }>(`/api/reservations/${selected.id}/split`, { method: 'POST', body: JSON.stringify({ room_ids: splitRooms, new_guest_id: splitGuest ? Number(splitGuest) : null, reason: 'Split from reception workstation' }) });
      setMessage(`Reservation split. New reservation #${result.new_reservation_id} created.`);
      setSplitRooms([]); setSplitGuest('');
      await onRefresh();
      const refreshed = reservations.find(r => r.id === selected.id);
      if (refreshed) await openReservation(refreshed);
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to split reservation'); }
    finally { setBusy(false); }
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

  const operationalStays = stays.length > 0 ? stays : [];

  return <section className="page">
    <div className="page-heading"><div><p className="muted">PMS reservation control</p><h2>Reservations</h2></div><span className="room-count">{reservations.length} bookings</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="workspace" style={{ gridTemplateColumns: 'minmax(0, 1.25fr) minmax(360px, 1fr)' }}>
      <div className="panel">
        <div className="panel-head"><h2>Reservation register</h2><span>{reservations.filter(r => r.status === 'reserved').length} confirmed</span></div>
        <div className="reservation-list">
          {reservations.length ? reservations.map(r => <article key={r.id} onClick={() => openReservation(r)} style={{ cursor: 'pointer', border: selectedId === r.id ? '2px solid #26342a' : undefined }}>
            <div><strong>#{r.id} · {r.guest_name}</strong><span>{r.check_in} → {r.check_out} · {r.room_ids.map(id => roomById.get(id)?.number ?? id).join(', ') || 'No room'}</span></div><b>{r.status}</b>
          </article>) : <p className="muted">No reservations yet.</p>}
        </div>

        {selected && <div className="panel" style={{ marginTop: 16, background: '#fbfaf7' }}>
          <div className="panel-head"><div><p className="muted" style={{ margin: 0 }}>Stay operations</p><h2>Reservation #{selected.id}</h2></div><b>{selected.status}</b></div>
          <p><strong>{selected.guest_name}</strong> · {selected.check_in} → {selected.check_out}</p>

          {canOperate && selected.status === 'reserved' && <section style={{ padding: 14, borderRadius: 14, background: '#f4f1ea', marginBottom: 14 }}>
            <div className="panel-head" style={{ marginBottom: 10 }}><div><strong>Reservation split</strong><small className="muted" style={{ display: 'block' }}>Move selected rooms into a new reservation</small></div></div>
            <div className="reservation-list" style={{ marginTop: 0 }}>
              {selected.room_ids.map(roomId => <label key={roomId} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: 10, borderRadius: 10, background: 'white' }}>
                <input type="checkbox" checked={splitRooms.includes(roomId)} onChange={e => setSplitRooms(current => e.target.checked ? [...current, roomId] : current.filter(id => id !== roomId))} />
                <span>Room {roomById.get(roomId)?.number ?? roomId}</span>
              </label>)}
            </div>
            <div className="two-col" style={{ marginTop: 10 }}>
              <label>New reservation guest<select value={splitGuest} onChange={e => setSplitGuest(e.target.value)}><option value="">Same booking guest</option>{guests.map(g => <option key={g.id} value={g.id}>{g.full_name}</option>)}</select></label>
              <div style={{ display: 'flex', alignItems: 'end' }}><button type="button" className="secondary-button" disabled={busy || !splitRooms.length} onClick={() => void splitReservation()}>Split selected rooms</button></div>
            </div>
          </section>}

          <div className="reservation-list">
            {operationalStays.length ? operationalStays.map(stay => {
              const detail = details[stay.id];
              const availableDestinations = rooms.filter(r => r.status === 'available' && r.id !== stay.room_id);
              const open = openStayId === stay.id;
              return <article key={stay.id} style={{ display: 'block', padding: 16, background: 'white', border: open ? '1px solid #c7d2c4' : '1px solid transparent' }}>
                <div className="panel-head" style={{ marginBottom: 8 }}><div><strong>Room {stay.room_number}</strong><span style={{ display: 'block' }}>{stay.check_in} → {stay.check_out} · {stay.status}</span></div><b>{money(Number(stay.agreed_rate))} / night</b></div>
                <div className="two-col">
                  <div><small className="muted">Booking guest</small><div><strong>{stay.guest_name || 'Unassigned'}</strong></div></div>
                  <div><small className="muted">Payment</small><div>{stay.payment_due_policy} · deposit {money(Number(stay.deposit_received))} / {money(Number(stay.deposit_required))}</div></div>
                </div>
                <div className="desk-actions" style={{ marginTop: 10 }}><button type="button" className="secondary-button small-button" onClick={() => void loadStayDetails(stay.id)}>{open ? 'Refresh stay' : 'Manage stay'}</button>{canOperate && stay.status === 'checked_in' && <button type="button" className="primary-button small-button" onClick={() => setMoveTarget(current => ({ ...current, [stay.id]: '' }))}>Room move</button>}</div>

                {open && detail && <div style={{ marginTop: 14, display: 'grid', gap: 12 }}>
                  <section style={{ padding: 12, borderRadius: 12, background: '#f4f1ea' }}>
                    <div className="panel-head" style={{ marginBottom: 8 }}><strong>Occupants & room sharing</strong><span>{detail.occupants.length} occupant(s)</span></div>
                    {detail.occupants.length ? <div className="reservation-list" style={{ marginTop: 0 }}>{detail.occupants.map(o => <article key={o.id}><div><strong>{o.guest_name}</strong><span>{o.role} · {o.check_in} → {o.check_out}</span></div><small>{o.is_primary ? 'Primary' : 'Occupant'}</small></article>)}</div> : <p className="muted">No occupant records yet.</p>}
                    {canOperate && <div className="two-col" style={{ marginTop: 10 }}><label>Add occupant<select value={newOccupant[stay.id] || ''} onChange={e => setNewOccupant(current => ({ ...current, [stay.id]: e.target.value }))}><option value="">Select guest</option>{guests.map(g => <option key={g.id} value={g.id}>{g.full_name}</option>)}</select></label><label>Role<input value={occupantRole[stay.id] || 'occupant'} onChange={e => setOccupantRole(current => ({ ...current, [stay.id]: e.target.value }))} placeholder="occupant, child, driver…" /></label></div>}
                    {canOperate && <div className="desk-actions" style={{ marginTop: 8 }}><label style={{ display: 'flex', alignItems: 'center', gap: 7 }}><input type="checkbox" checked={Boolean(primaryOccupant[stay.id])} onChange={e => setPrimaryOccupant(current => ({ ...current, [stay.id]: e.target.checked }))} /> Make primary occupant</label><button type="button" className="secondary-button small-button" disabled={busy || !newOccupant[stay.id]} onClick={() => void addOccupant(stay)}>Add occupant</button></div>}
                  </section>

                  <section style={{ padding: 12, borderRadius: 12, background: '#f4f1ea' }}>
                    <div className="panel-head" style={{ marginBottom: 8 }}><strong>Rate schedule</strong><span>{detail.rateSegments.length} segment(s)</span></div>
                    {detail.rateSegments.length ? <div className="reservation-list" style={{ marginTop: 0 }}>{detail.rateSegments.map(s => <article key={s.id}><div><strong>{s.from_date} → {s.to_date}</strong><span>{money(Number(s.rate))} · {Number(s.discount_percent)}% discount · net {money(Number(s.rate) - Number(s.discount_amount))}</span></div><small>{s.rate_plan || s.source}</small></article>)}</div> : <p className="muted">No rate segments.</p>}
                    {canOperate && <><div className="two-col" style={{ marginTop: 10 }}><label>From<input type="date" value={newRateFrom[stay.id] || ''} min={stay.check_in} max={stay.check_out} onChange={e => setNewRateFrom(current => ({ ...current, [stay.id]: e.target.value }))} /></label><label>To<input type="date" value={newRateTo[stay.id] || ''} min={stay.check_in} max={stay.check_out} onChange={e => setNewRateTo(current => ({ ...current, [stay.id]: e.target.value }))} /></label></div><div className="two-col"><label>Nightly rate<input type="number" min="0" step="0.01" value={newRate[stay.id] || ''} onChange={e => setNewRate(current => ({ ...current, [stay.id]: e.target.value }))} /></label><label>Discount %<input type="number" min="0" max="100" step="0.01" value={newRateDiscount[stay.id] || '0'} onChange={e => setNewRateDiscount(current => ({ ...current, [stay.id]: e.target.value }))} /></label></div><button type="button" className="secondary-button small-button" disabled={busy} onClick={() => void addRateSegment(stay)}>Add rate segment</button></>}
                  </section>

                  <section style={{ padding: 12, borderRadius: 12, background: '#f4f1ea' }}>
                    <div className="panel-head" style={{ marginBottom: 8 }}><strong>Deposits</strong><span>Balance {money(Number(detail.deposits.balance))}</span></div>
                    {detail.deposits.transactions.length ? <div className="reservation-list" style={{ marginTop: 0 }}>{detail.deposits.transactions.map(t => <article key={t.id}><div><strong>{t.transaction_type} · {money(Number(t.amount))}</strong><span>{t.payment_method || '—'}{t.reference ? ` · ${t.reference}` : ''}</span></div><small>{new Date(t.created_at).toLocaleString()}</small></article>)}</div> : <p className="muted">No deposit transactions.</p>}
                    {canOperate && <><div className="two-col" style={{ marginTop: 10 }}><label>Transaction<select value={depositType[stay.id] || 'received'} onChange={e => setDepositType(current => ({ ...current, [stay.id]: e.target.value }))}><option value="received">Receive</option><option value="applied">Apply</option><option value="refunded">Refund</option><option value="adjusted">Adjust</option></select></label><label>Amount<input type="number" min="0.01" step="0.01" value={depositAmount[stay.id] || ''} onChange={e => setDepositAmount(current => ({ ...current, [stay.id]: e.target.value }))} /></label></div><div className="two-col"><label>Payment method<select value={depositMethod[stay.id] || 'cash'} onChange={e => setDepositMethod(current => ({ ...current, [stay.id]: e.target.value }))}><option value="cash">Cash</option><option value="card">Card</option><option value="bank_transfer">Bank transfer</option><option value="other">Other</option></select></label><label>Reference<input value={depositReference[stay.id] || ''} onChange={e => setDepositReference(current => ({ ...current, [stay.id]: e.target.value }))} placeholder="Receipt / transfer reference" /></label></div><button type="button" className="secondary-button small-button" disabled={busy} onClick={() => void addDeposit(stay)}>Record deposit transaction</button></>}
                  </section>

                  <section style={{ padding: 12, borderRadius: 12, background: '#f4f1ea' }}>
                    <div className="panel-head" style={{ marginBottom: 8 }}><strong>Room moves</strong><span>{detail.moves.length} recorded</span></div>
                    {detail.moves.length ? <div className="reservation-list" style={{ marginTop: 0 }}>{detail.moves.map(m => <article key={m.id}><div><strong>{roomById.get(m.from_room_id)?.number ?? m.from_room_id} → {roomById.get(m.to_room_id)?.number ?? m.to_room_id}</strong><span>{m.reason || 'No reason recorded'}</span></div><small>{new Date(m.effective_at).toLocaleString()}</small></article>)}</div> : <p className="muted">No room moves recorded.</p>}
                    {canOperate && stay.status === 'checked_in' && <><div className="two-col" style={{ marginTop: 10 }}><label>Destination room<select value={moveTarget[stay.id] || ''} onChange={e => setMoveTarget(current => ({ ...current, [stay.id]: e.target.value }))}><option value="">Select available room</option>{availableDestinations.map(r => <option key={r.id} value={r.id}>{r.number}</option>)}</select></label><label>Reason<input value={moveReason[stay.id] || ''} onChange={e => setMoveReason(current => ({ ...current, [stay.id]: e.target.value }))} placeholder="Upgrade, maintenance, guest request…" /></label></div><button type="button" className="secondary-button small-button" disabled={busy || !moveTarget[stay.id]} onClick={() => void moveRoom(stay)}>Confirm room move</button></>}
                  </section>
                </div>}
              </article>;
            }) : <p className="muted">No stay detail available.</p>}
          </div>

          {canOperate && <div className="desk-actions" style={{ marginTop: 14 }}>{selected.status === 'reserved' && <><button className="secondary-button small-button" disabled={busy} onClick={cancelReservation}>Cancel</button><button className="secondary-button small-button" disabled={busy || selected.check_in > today} onClick={noShowReservation}>No-show</button></>}{selected.status === 'checked_in' && <button className="primary-button small-button" disabled={busy} onClick={extendSelected}>Extend stay</button>}</div>}
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
