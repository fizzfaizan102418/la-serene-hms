import React, { useEffect, useMemo, useState } from 'react';

type HousekeepingRoom = {
  room_id: number;
  room_number: string;
  room_type: string;
  status: string;
  priority: string;
  occupied_by_reservation_id?: number | null;
  expected_release?: string | null;
};

type Board = {
  business_date: string;
  summary: { dirty: number; available: number; out_of_order: number };
  rooms: HousekeepingRoom[];
};

type Props = {
  userRole: string;
  api: <T>(path: string, options?: RequestInit) => Promise<T>;
  onRefresh: () => Promise<void>;
};

const label = (value: string) => value.replace(/_/g, ' ');
const priorityWeight = (value: string) => {
  const normalized = value.toLowerCase();
  if (normalized === 'high' || normalized === 'urgent' || normalized === 'critical') return 0;
  if (normalized === 'medium' || normalized === 'normal') return 1;
  return 2;
};

export default function HousekeepingView({ userRole, api, onRefresh }: Props) {
  const [board, setBoard] = useState<Board | null>(null);
  const [filter, setFilter] = useState('actionable');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const isAdmin = userRole === 'admin';

  async function load() {
    setLoading(true);
    try {
      setBoard(await api<Board>('/api/housekeeping'));
      setMessage('');
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to load housekeeping board');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function clean(room: HousekeepingRoom) {
    try {
      await api(`/api/housekeeping/rooms/${room.room_id}/clean`, { method: 'POST' });
      setMessage(`Room ${room.room_number} marked clean and returned to available.`);
      await onRefresh();
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to mark room clean');
    }
  }

  async function setOutOfOrder(room: HousekeepingRoom) {
    if (!window.confirm(`Take room ${room.room_number} out of order?`)) return;
    try {
      await api(`/api/housekeeping/rooms/${room.room_id}/out-of-order`, { method: 'POST' });
      setMessage(`Room ${room.room_number} is now out of order.`);
      await onRefresh();
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to take room out of order');
    }
  }

  async function release(room: HousekeepingRoom) {
    if (!window.confirm(`Release room ${room.room_number} back to available inventory?`)) return;
    try {
      await api(`/api/housekeeping/rooms/${room.room_id}/release`, { method: 'POST' });
      setMessage(`Room ${room.room_number} released and available.`);
      await onRefresh();
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to release room');
    }
  }

  const counts = board ? {
    dirty: board.summary.dirty,
    available: board.summary.available,
    out_of_order: board.summary.out_of_order,
    occupied: board.rooms.filter(room => room.status === 'occupied').length,
    reserved: board.rooms.filter(room => room.status === 'reserved').length,
  } : null;

  const rooms = useMemo(() => {
    if (!board) return [];
    const filtered = filter === 'all' ? board.rooms
      : filter === 'dirty' ? board.rooms.filter(room => room.status === 'dirty')
      : filter === 'available' ? board.rooms.filter(room => room.status === 'available')
      : filter === 'occupied' ? board.rooms.filter(room => room.status === 'occupied')
      : filter === 'reserved' ? board.rooms.filter(room => room.status === 'reserved')
      : filter === 'out_of_order' ? board.rooms.filter(room => room.status === 'out_of_order')
      : board.rooms.filter(room => room.status === 'dirty' || room.status === 'out_of_order');
    return [...filtered].sort((a, b) => priorityWeight(a.priority) - priorityWeight(b.priority) || a.room_number.localeCompare(b.room_number, undefined, { numeric: true }));
  }, [board, filter]);

  const queueLabel = filter === 'actionable' ? 'Actionable rooms' : `${label(filter)} rooms`;

  return <section className="page housekeeping-page">
    <div className="page-heading">
      <div><p className="muted">Cleaning, readiness and room exceptions</p><h2>Housekeeping</h2></div>
      <div className="housekeeping-heading-actions"><span className="room-count">{board?.business_date ?? '—'}</span><button className="secondary-button" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh board'}</button></div>
    </div>
    {message && <p className="notice">{message}</p>}

    <section className="housekeeping-hero">
      <div><p className="housekeeping-eyebrow">Daily room operations</p><h3>Get rooms ready for the next guest</h3><span>Start with dirty and exception rooms, then work down the priority queue.</span></div>
      <div className="housekeeping-hero-stats"><div><span>Actionable</span><strong>{(counts?.dirty ?? 0) + (counts?.out_of_order ?? 0)}</strong></div><div><span>Ready</span><strong>{counts?.available ?? '—'}</strong></div></div>
    </section>

    <div className="stats housekeeping-stats">
      {[
        ['Needs cleaning', counts?.dirty ?? '—', 'dirty', 'Action'],
        ['Ready / available', counts?.available ?? '—', 'available', 'Ready'],
        ['Reserved', counts?.reserved ?? '—', 'reserved', 'Upcoming'],
        ['Occupied', counts?.occupied ?? '—', 'occupied', 'In house'],
        ['Out of order', counts?.out_of_order ?? '—', 'out_of_order', 'Exception'],
      ].map(([name, value, tone, helper]) => <button type="button" className={`stat housekeeping-stat housekeeping-stat-${tone} ${filter === tone ? 'selected' : ''}`} key={name} onClick={() => setFilter(filter === tone ? 'actionable' : tone)}><span>{name}</span><strong>{value}</strong><small>{helper}</small></button>)}
    </div>

    <div className="panel housekeeping-toolbar">
      <div><p className="muted">Room queue</p><h2>{queueLabel}</h2><span className="housekeeping-queue-caption">{rooms.length} room{rooms.length === 1 ? '' : 's'} · highest priority first</span></div>
      <select value={filter} onChange={e => setFilter(e.target.value)} aria-label="Housekeeping room filter">
        <option value="actionable">Actionable</option><option value="all">All rooms</option><option value="dirty">Dirty</option><option value="available">Available</option><option value="reserved">Reserved</option><option value="occupied">Occupied</option><option value="out_of_order">Out of order</option>
      </select>
    </div>

    <div className="housekeeping-grid">
      {loading && !board ? <div className="panel housekeeping-empty"><strong>Loading room board…</strong><span>Fetching the current housekeeping state.</span></div> : rooms.length ? rooms.map(room => <article className={`housekeeping-card housekeeping-card-enhanced room-${room.status}`} key={room.room_id}>
        <div className="housekeeping-card-head">
          <div><strong>{room.room_number}</strong><span>{room.room_type}</span></div>
          <b className="housekeeping-status">{label(room.status)}</b>
        </div>
        <div className="housekeeping-card-focus"><span>Priority</span><strong>{label(room.priority)}</strong>{room.expected_release && <small>Expected release · {room.expected_release}</small>}</div>
        <div className="housekeeping-meta">
          {room.occupied_by_reservation_id && <><span>Reservation</span><strong>#{room.occupied_by_reservation_id}</strong></>}
          {!room.occupied_by_reservation_id && <><span>Operational state</span><strong>{room.status === 'available' ? 'Ready for assignment' : room.status === 'dirty' ? 'Cleaning required' : room.status === 'out_of_order' ? 'Unavailable' : 'Occupied / reserved'}</strong></>}
        </div>
        <div className="housekeeping-actions">
          {room.status === 'dirty' && <button className="primary-button small-button" onClick={() => void clean(room)}>Mark clean</button>}
          {isAdmin && room.status !== 'out_of_order' && room.status !== 'occupied' && room.status !== 'reserved' && <button className="secondary-button small-button" onClick={() => void setOutOfOrder(room)}>Take OOO</button>}
          {isAdmin && room.status === 'out_of_order' && <button className="primary-button small-button" onClick={() => void release(room)}>Release room</button>}
          {room.status === 'available' && <span className="housekeeping-ready-badge">Ready</span>}
          {room.status !== 'dirty' && room.status !== 'out_of_order' && room.status !== 'available' && <span className="housekeeping-muted-action">No housekeeping action</span>}
        </div>
      </article>) : <div className="panel housekeeping-empty"><strong>No rooms in this queue</strong><span>Choose another queue or refresh the board.</span></div>}
    </div>
  </section>;
}
