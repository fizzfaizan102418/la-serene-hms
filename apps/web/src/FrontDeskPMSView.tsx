import React, { useMemo, useState } from 'react';

type User = { id: number; username: string; role: string };
type Room = { id: number; number: string; room_type_id: number; status: string };
type Reservation = { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; room_ids: number[]; folio_id: number };
type FrontDeskData = { arrivals: Reservation[]; departures: Reservation[]; in_house: Reservation[] };
type Props = { user: User; data: FrontDeskData; rooms: Room[]; reservations: Reservation[]; onRefresh: () => Promise<void>; api: <T>(path: string, options?: RequestInit) => Promise<T> };

const addDays = (value: string, amount: number) => { const d = new Date(`${value}T00:00:00`); d.setDate(d.getDate() + amount); return d.toISOString().slice(0, 10); };
const nights = (start: string, end: string) => Math.max(0, Math.round((new Date(`${end}T00:00:00`).getTime() - new Date(`${start}T00:00:00`).getTime()) / 86400000));
const active = (r: Reservation) => r.status === 'reserved' || r.status === 'checked_in';

export default function FrontDeskPMSView({ user, data, rooms, reservations, onRefresh, api }: Props) {
  const canOperate = user.role === 'admin' || user.role === 'reception';
  const today = new Date().toISOString().slice(0, 10);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [rackStart, setRackStart] = useState(today);
  const [moveFrom, setMoveFrom] = useState('');
  const [moveTo, setMoveTo] = useState('');
  const [extension, setExtension] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const roomById = useMemo(() => new Map(rooms.map(r => [r.id, r])), [rooms]);
  const rackDays = useMemo(() => Array.from({ length: 14 }, (_, i) => addDays(rackStart, i)), [rackStart]);
  const selected = selectedId ? reservations.find(r => r.id === selectedId) ?? null : null;

  async function act(id: number, action: 'check-in' | 'check-out') {
    setBusy(true); setMessage('');
    try { await api(`/api/reservations/${id}/${action}`, { method: 'POST' }); setMessage(`Reservation #${id} ${action === 'check-in' ? 'checked in' : 'checked out'}.`); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Operation failed'); }
    finally { setBusy(false); }
  }

  async function transfer(event: React.FormEvent) {
    event.preventDefault(); if (!selected || !moveFrom || !moveTo) return;
    setBusy(true); setMessage('');
    try { await api(`/api/reservations/${selected.id}/transfer`, { method: 'POST', body: JSON.stringify({ from_room_id: Number(moveFrom), to_room_id: Number(moveTo) }) }); setMessage(`Room transfer completed for #${selected.id}.`); setMoveFrom(''); setMoveTo(''); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to transfer room'); }
    finally { setBusy(false); }
  }

  async function extend(event: React.FormEvent) {
    event.preventDefault(); if (!selected || !extension) return;
    setBusy(true); setMessage('');
    try { await api(`/api/reservations/${selected.id}/extend`, { method: 'POST', body: JSON.stringify({ new_check_out: extension }) }); setMessage(`Reservation #${selected.id} extended to ${extension}.`); setExtension(''); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to extend stay'); }
    finally { setBusy(false); }
  }

  const card = (r: Reservation, kind: 'arrival' | 'departure' | 'in-house') => <article className="desk-card" key={r.id} onClick={() => setSelectedId(r.id)} style={{ cursor: 'pointer' }}>
    <div><strong>#{r.id} · {r.guest_name}</strong><span>{r.check_in} → {r.check_out} · {nights(r.check_in, r.check_out)} night(s)</span><small>Room(s): {r.room_ids.map(id => roomById.get(id)?.number ?? id).join(', ') || 'Unassigned'}</small></div>
    <div className="desk-actions">{kind === 'arrival' && canOperate && <button className="primary-button small-button" disabled={busy} onClick={e => { e.stopPropagation(); void act(r.id, 'check-in'); }}>Check in</button>}{kind === 'departure' && canOperate && <button className="primary-button small-button" disabled={busy} onClick={e => { e.stopPropagation(); void act(r.id, 'check-out'); }}>Check out</button>}{kind === 'in-house' && canOperate && r.room_ids.map(id => <button key={id} className="secondary-button small-button" disabled={busy} onClick={e => { e.stopPropagation(); setSelectedId(r.id); setMoveFrom(String(id)); setMoveTo(''); }}>Move room</button>)}</div>
  </article>;

  return <section className="page">
    <div className="page-heading"><div><p className="muted">Front office control</p><h2>Front Desk 2.0</h2></div><span className="room-count">{data.in_house.length} in house</span></div>
    {message && <p className="notice">{message}</p>}

    <section className="stats">
      {[["Arrivals today", data.arrivals.length], ["Departures today", data.departures.length], ["In house", data.in_house.length], ["Reserved", reservations.filter(r => r.status === 'reserved').length], ["Occupied rooms", rooms.filter(r => r.status === 'occupied').length], ["Dirty rooms", rooms.filter(r => r.status === 'dirty').length]].map(([label, value]) => <article className="stat" key={String(label)}><span>{label}</span><strong>{value}</strong></article>)}
    </section>

    <div className="desk-grid">
      <div className="panel"><div className="panel-head"><h2>Arrivals</h2><span>{data.arrivals.length}</span></div>{data.arrivals.length ? data.arrivals.map(r => card(r, 'arrival')) : <p className="muted">No arrivals today.</p>}</div>
      <div className="panel"><div className="panel-head"><h2>Departures</h2><span>{data.departures.length}</span></div>{data.departures.length ? data.departures.map(r => card(r, 'departure')) : <p className="muted">No departures today.</p>}</div>
      <div className="panel"><div className="panel-head"><h2>In house</h2><span>{data.in_house.length}</span></div>{data.in_house.length ? data.in_house.map(r => card(r, 'in-house')) : <p className="muted">No checked-in guests.</p>}</div>
    </div>

    {selected && <section className="panel transfer-panel">
      <div className="panel-head"><div><p className="muted">Selected reservation</p><h2>#{selected.id} · {selected.guest_name}</h2></div><b>{selected.status}</b></div>
      <p className="muted">{selected.check_in} → {selected.check_out} · {selected.room_ids.map(id => roomById.get(id)?.number ?? id).join(', ')}</p>
      {canOperate && selected.status === 'reserved' && <button className="primary-button small-button" disabled={busy || selected.check_in > today} onClick={() => void act(selected.id, 'check-in')}>Check in</button>}
      {canOperate && selected.status === 'checked_in' && <div className="two-col" style={{ marginTop: 12 }}>
        <form className="form-panel" onSubmit={transfer}><div className="panel-head"><h2>Move room</h2></div><label>Current room<select value={moveFrom} onChange={e => setMoveFrom(e.target.value)}><option value="">Select room</option>{selected.room_ids.map(id => <option key={id} value={id}>{roomById.get(id)?.number ?? id}</option>)}</select></label><label>Destination<select value={moveTo} onChange={e => setMoveTo(e.target.value)}><option value="">Select available room</option>{rooms.filter(r => r.status === 'available' && !selected.room_ids.includes(r.id)).map(r => <option key={r.id} value={r.id}>{r.number}</option>)}</select></label><button className="secondary-button" disabled={busy || !moveFrom || !moveTo}>Confirm move</button></form>
        <form className="form-panel" onSubmit={extend}><div className="panel-head"><h2>Extend stay</h2></div><label>New check-out<input type="date" min={addDays(selected.check_out, 1)} value={extension} onChange={e => setExtension(e.target.value)} /></label><button className="secondary-button" disabled={busy || !extension}>Extend reservation</button></form>
      </div>}
      {canOperate && selected.status === 'checked_in' && <button className="primary-button small-button" style={{ marginTop: 12 }} disabled={busy} onClick={() => void act(selected.id, 'check-out')}>Check out</button>}
    </section>}

    <section className="panel" style={{ marginTop: 16, overflowX: 'auto' }}>
      <div className="panel-head"><div><p className="muted">Room availability by night</p><h2>Room Rack / 14-Day Calendar</h2></div><div className="desk-actions"><button className="secondary-button small-button" onClick={() => setRackStart(addDays(rackStart, -7))}>← 7 days</button><input type="date" value={rackStart} onChange={e => setRackStart(e.target.value)} style={{ padding: '9px 10px', border: '1px solid #d8d4cb', borderRadius: 10 }} /><button className="secondary-button small-button" onClick={() => setRackStart(addDays(rackStart, 7))}>7 days →</button></div></div>
      <div style={{ display: 'grid', gridTemplateColumns: '120px repeat(14, minmax(74px, 1fr))', minWidth: 1180, gap: 4 }}>
        <div style={{ padding: 9, fontWeight: 700 }}>Room</div>
        {rackDays.map(day => <div key={day} style={{ padding: 8, background: '#f4f1ea', borderRadius: 8, textAlign: 'center', fontSize: 11 }}><strong>{day.slice(5)}</strong><br />{new Date(`${day}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short' })}</div>)}
        {rooms.map(room => <React.Fragment key={room.id}><div style={{ padding: 9, background: '#f7f5f0', borderRadius: 8 }}><strong>{room.number}</strong><br /><small>{room.status.replace(/_/g, ' ')}</small></div>{rackDays.map(day => { const booking = reservations.find(r => active(r) && r.room_ids.includes(room.id) && day >= r.check_in && day < r.check_out); const current = day === today ? room.status : ''; return <button key={`${room.id}-${day}`} type="button" onClick={() => booking && setSelectedId(booking.id)} style={{ minHeight: 56, border: '1px solid #dedbd2', borderRadius: 8, background: booking ? '#eef5ed' : '#fbfaf7', padding: 6, textAlign: 'left' }}>{booking ? <><strong style={{ fontSize: 11 }}>#{booking.id}</strong><br /><span style={{ fontSize: 10 }}>{booking.guest_name.slice(0, 14)}</span></> : current ? <span style={{ fontSize: 10, textTransform: 'capitalize' }}>{current.replace(/_/g, ' ')}</span> : <span style={{ color: '#9aa09b', fontSize: 11 }}>Free</span>}</button>; })}</React.Fragment>)}
      </div>
    </section>
  </section>;
}
