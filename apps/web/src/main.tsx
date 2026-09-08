import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Dashboard = { business_date: string; total_rooms: number; available_rooms: number; reserved_rooms: number; occupied_rooms: number; dirty_rooms: number; out_of_order_rooms: number; arrivals_today: number; departures_today: number; in_house_guests: number };
type Room = { id: number; number: string; room_type_id: number; status: string };

const modules = ['Dashboard', 'Rooms', 'Guests', 'Reservations', 'Front Desk', 'Billing'];

function App() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([fetch('/api/dashboard'), fetch('/api/rooms')])
      .then(async ([d, r]) => { if (!d.ok || !r.ok) throw new Error(); return Promise.all([d.json(), r.json()]); })
      .then(([d, r]) => { setDashboard(d); setRooms(r); })
      .catch(() => setError('Backend is not running. Start the local API to load live hotel data.'));
  }, []);

  const label = (status: string) => status.replace('_', ' ');

  return (
    <main className="shell">
      <header className="topbar"><div><p className="eyebrow">LA SERENE HOTEL</p><h1>Hotel Management System</h1></div><div className="status"><span /> Offline-first</div></header>
      <section className="welcome"><div><p className="muted">Operations dashboard</p><h2>{dashboard ? `Business date · ${dashboard.business_date}` : 'Loading hotel data…'}</h2></div>{error && <p className="error">{error}</p>}</section>
      <section className="stats">{[['Total Rooms', dashboard?.total_rooms], ['Available', dashboard?.available_rooms], ['Occupied', dashboard?.occupied_rooms], ['Reserved', dashboard?.reserved_rooms], ['Arrivals', dashboard?.arrivals_today], ['Departures', dashboard?.departures_today]].map(([name, value]) => <article className="stat" key={name as string}><span>{name}</span><strong>{value ?? '—'}</strong></article>)}</section>
      <section className="workspace">
        <div className="panel"><div className="panel-head"><h2>Room Status</h2><span>{rooms.length} rooms</span></div><div className="rooms">{rooms.length ? rooms.map(room => <div className={`room room-${room.status}`} key={room.id}><strong>{room.number}</strong><span>{label(room.status)}</span></div>) : <p className="muted">No rooms configured yet.</p>}</div></div>
        <div className="panel"><div className="panel-head"><h2>Operations</h2></div><div className="operations"><div><span>Dirty rooms</span><strong>{dashboard?.dirty_rooms ?? '—'}</strong></div><div><span>Out of order</span><strong>{dashboard?.out_of_order_rooms ?? '—'}</strong></div><div><span>In-house guests</span><strong>{dashboard?.in_house_guests ?? '—'}</strong></div></div></div>
      </section>
      <section className="grid">{modules.map((item, index) => <article key={item} className={index === 0 ? 'active' : ''}><h2>{item}</h2><p>{index === 0 ? 'Live operational overview' : 'Module coming next'}</p></article>)}</section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
