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
};

const label = (value: string) => value.replace(/_/g, ' ');

export default function HousekeepingView({ userRole, api }: Props) {
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
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to mark room clean');
    }
  }

  async function setOutOfOrder(room: HousekeepingRoom) {
    try {
      await api(`/api/housekeeping/rooms/${room.room_id}/out-of-order`, { method: 'POST' });
      setMessage(`Room ${room.room_number} is now out of order.`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to take room out of order');
    }
  }

  async function release(room: HousekeepingRoom) {
    try {
      await api(`/api/housekeeping/rooms/${room.room_id}/release`, { method: 'POST' });
      setMessage(`Room ${room.room_number} released and available.`);
      await load();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to release room');
    }
  }

  const rooms = useMemo(() => {
    if (!board) return [];
    if (filter === 'all') return board.rooms;
    if (filter === 'dirty') return board.rooms.filter(room => room.status === 'dirty');
    if (filter === 'available') return board.rooms.filter(room => room.status === 'available');
    if (filter === 'out_of_order') return board.rooms.filter(room => room.status === 'out_of_order');
    return board.rooms.filter(room => room.status === 'dirty' || room.status === 'out_of_order');
  }, [board, filter]);

  return <section className="page">
    <div className="page-heading">
      <div><p className="muted">Cleaning, readiness and room exceptions</p><h2>Housekeeping</h2></div>
      <div className="housekeeping-heading-actions"><span className="room-count">{board?.business_date ?? '—'}</span><button className="secondary-button" onClick={() => void load()} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button></div>
    </div>
    {message && <p className="notice">{message}</p>}

    <div className="stats housekeeping-stats">
      <article className="stat"><span>Dirty / needs cleaning</span><strong>{board?.summary.dirty ?? '—'}</strong></article>
      <article className="stat"><span>Ready / available</span><strong>{board?.summary.available ?? '—'}</strong></article>
      <article className="stat"><span>Out of order</span><strong>{board?.summary.out_of_order ?? '—'}</strong></article>
    </div>

    <div className="panel housekeeping-toolbar">
      <div><p className="muted">Room queue</p><h2>Daily room board</h2></div>
      <select value={filter} onChange={e => setFilter(e.target.value)} aria-label="Housekeeping room filter">
        <option value="actionable">Actionable</option>
        <option value="all">All rooms</option>
        <option value="dirty">Dirty</option>
        <option value="available">Available</option>
        <option value="out_of_order">Out of order</option>
      </select>
    </div>

    <div className="housekeeping-grid">
      {loading && !board ? <div className="panel"><p className="muted">Loading housekeeping board…</p></div> : rooms.length ? rooms.map(room => <article className={`housekeeping-card room-${room.status}`} key={room.room_id}>
        <div className="housekeeping-card-head">
          <div><strong>{room.room_number}</strong><span>{room.room_type}</span></div>
          <b className="housekeeping-status">{label(room.status)}</b>
        </div>
        <div className="housekeeping-meta">
          <span>Priority</span><strong>{label(room.priority)}</strong>
          {room.occupied_by_reservation_id && <><span>Reservation</span><strong>#{room.occupied_by_reservation_id}</strong></>}
          {room.expected_release && <><span>Expected release</span><strong>{room.expected_release}</strong></>}
        </div>
        <div className="housekeeping-actions">
          {room.status === 'dirty' && <button className="primary-button small-button" onClick={() => void clean(room)}>Mark clean</button>}
          {isAdmin && room.status !== 'out_of_order' && room.status !== 'occupied' && room.status !== 'reserved' && <button className="secondary-button small-button" onClick={() => void setOutOfOrder(room)}>Take OOO</button>}
          {isAdmin && room.status === 'out_of_order' && <button className="primary-button small-button" onClick={() => void release(room)}>Release room</button>}
        </div>
      </article>) : <div className="panel"><p className="muted">No rooms match this queue.</p></div>}
    </div>
  </section>;
}
