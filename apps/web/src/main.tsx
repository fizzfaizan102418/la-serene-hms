import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type User = { id: number; username: string; role: string };
type Dashboard = { business_date: string; total_rooms: number; available_rooms: number; reserved_rooms: number; occupied_rooms: number; dirty_rooms: number; out_of_order_rooms: number; arrivals_today: number; departures_today: number; in_house_guests: number };
type RoomType = { id: number; name: string; base_rate: number; description?: string | null };
type Room = { id: number; number: string; room_type_id: number; status: string };
type AuthMode = 'login' | 'bootstrap';
type View = 'Dashboard' | 'Rooms';

const modules: View[] = ['Dashboard', 'Rooms'];
const TOKEN_KEY = 'la_serene_access_token';
const statuses = ['available', 'reserved', 'occupied', 'dirty', 'out_of_order'] as const;

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail ?? detail; } catch { /* ignore */ }
    const error = new Error(detail) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return response.json() as Promise<T>;
}

function AuthScreen({ mode, setMode, onAuthenticated }: { mode: AuthMode; setMode: (mode: AuthMode) => void; onAuthenticated: (user: User, token: string) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(''); setBusy(true);
    try {
      if (mode === 'bootstrap') await api('/api/auth/bootstrap-admin', { method: 'POST', body: JSON.stringify({ username, password }) });
      const login = await api<{ access_token: string; user_id: number; username: string; role: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
      onAuthenticated({ id: login.user_id, username: login.username, role: login.role }, login.access_token);
    } catch (err) { setError(err instanceof Error ? err.message : 'Something went wrong'); }
    finally { setBusy(false); }
  }

  return <main className="auth-shell"><section className="auth-card"><div><p className="eyebrow">LA SERENE HOTEL</p><h1>{mode === 'bootstrap' ? 'Create your admin account' : 'Welcome back'}</h1><p className="muted">{mode === 'bootstrap' ? 'Create the first administrator for this local installation.' : 'Sign in to continue to hotel operations.'}</p></div><form onSubmit={submit} className="auth-form"><label>Username<input value={username} onChange={e => setUsername(e.target.value)} required minLength={3} autoComplete="username" /></label><label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={mode === 'bootstrap' ? 8 : 1} autoComplete={mode === 'bootstrap' ? 'new-password' : 'current-password'} /></label>{error && <p className="form-error">{error}</p>}<button className="primary-button" disabled={busy}>{busy ? 'Please wait…' : mode === 'bootstrap' ? 'Create admin & sign in' : 'Sign in'}</button></form><button className="link-button" onClick={() => { setError(''); setMode(mode === 'login' ? 'bootstrap' : 'login'); }}>{mode === 'login' ? 'New installation? Create the first admin' : 'Already initialized? Sign in instead'}</button></section></main>;
}

function DashboardView({ dashboard, rooms, roomTypes }: { dashboard: Dashboard | null; rooms: Room[]; roomTypes: RoomType[] }) {
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]);
  const label = (status: string) => status.replace(/_/g, ' ');
  return <>
    <section className="stats">{[['Total Rooms', dashboard?.total_rooms], ['Available', dashboard?.available_rooms], ['Occupied', dashboard?.occupied_rooms], ['Reserved', dashboard?.reserved_rooms], ['Arrivals', dashboard?.arrivals_today], ['Departures', dashboard?.departures_today]].map(([name, value]) => <article className="stat" key={name as string}><span>{name}</span><strong>{value ?? '—'}</strong></article>)}</section>
    <section className="workspace"><div className="panel"><div className="panel-head"><h2>Room Status</h2><span>{rooms.length} rooms</span></div><div className="rooms">{rooms.length ? rooms.map(room => <div className={`room room-${room.status}`} key={room.id}><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Unassigned'} · {label(room.status)}</span></div>) : <p className="muted">No rooms configured yet.</p>}</div></div><div className="panel"><div className="panel-head"><h2>Operations</h2></div><div className="operations"><div><span>Dirty rooms</span><strong>{dashboard?.dirty_rooms ?? '—'}</strong></div><div><span>Out of order</span><strong>{dashboard?.out_of_order_rooms ?? '—'}</strong></div><div><span>In-house guests</span><strong>{dashboard?.in_house_guests ?? '—'}</strong></div></div></div></section>
  </>;
}

function RoomsView({ user, rooms, setRooms, roomTypes, setRoomTypes, onRefresh }: { user: User; rooms: Room[]; setRooms: React.Dispatch<React.SetStateAction<Room[]>>; roomTypes: RoomType[]; setRoomTypes: React.Dispatch<React.SetStateAction<RoomType[]>>; onRefresh: () => Promise<void> }) {
  const [typeName, setTypeName] = useState('');
  const [rate, setRate] = useState('');
  const [typeDescription, setTypeDescription] = useState('');
  const [roomNumber, setRoomNumber] = useState('');
  const [roomTypeId, setRoomTypeId] = useState('');
  const [filter, setFilter] = useState('all');
  const [message, setMessage] = useState('');
  const isAdmin = user.role === 'admin';

  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]);
  const visibleRooms = filter === 'all' ? rooms : rooms.filter(r => r.status === filter);

  async function createType(event: React.FormEvent) {
    event.preventDefault(); setMessage('');
    try { await api<RoomType>('/api/room-types', { method: 'POST', body: JSON.stringify({ name: typeName, base_rate: Number(rate || 0), description: typeDescription || null }) }); setTypeName(''); setRate(''); setTypeDescription(''); setMessage('Room type created.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to create room type'); }
  }

  async function createRoom(event: React.FormEvent) {
    event.preventDefault(); setMessage('');
    try { await api<Room>('/api/rooms', { method: 'POST', body: JSON.stringify({ number: roomNumber, room_type_id: Number(roomTypeId) }) }); setRoomNumber(''); setRoomTypeId(''); setMessage('Room added.'); await onRefresh(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to create room'); }
  }

  async function changeStatus(room: Room, nextStatus: string) {
    setMessage('');
    try { const updated = await api<Room>(`/api/rooms/${room.id}/status`, { method: 'PATCH', body: JSON.stringify({ status: nextStatus }) }); setRooms(current => current.map(r => r.id === updated.id ? updated : r)); setMessage(`Room ${updated.number} is now ${nextStatus.replace(/_/g, ' ')}.`); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to change room status'); }
  }

  return <section className="rooms-page">
    <div className="page-heading"><div><p className="muted">Inventory & housekeeping</p><h2>Rooms</h2></div><span className="room-count">{rooms.length} rooms</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="room-layout">
      <div className="panel room-map-panel"><div className="panel-head"><h2>Room map</h2><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All statuses</option>{statuses.map(status => <option key={status} value={status}>{status.replace(/_/g, ' ')}</option>)}</select></div><div className="room-map">{visibleRooms.length ? visibleRooms.map(room => <article className={`room-card room-${room.status}`} key={room.id}><div className="room-card-top"><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Unknown type'}</span></div><small>{typeById.get(room.room_type_id) ? `Rate ${Number(typeById.get(room.room_type_id)!.base_rate).toFixed(2)}` : 'No rate'}</small><select value={room.status} onChange={e => changeStatus(room, e.target.value)} disabled={user.role !== 'admin' && user.role !== 'reception' && user.role !== 'housekeeping'}>{statuses.map(status => <option key={status} value={status}>{status.replace(/_/g, ' ')}</option>)}</select></article>) : <p className="muted">No rooms match this filter.</p>}</div></div>
      <div className="side-stack">
        <div className="panel"><div className="panel-head"><h2>Room types</h2><span>{roomTypes.length}</span></div>{roomTypes.length ? <div className="type-list">{roomTypes.map(type => <div key={type.id}><div><strong>{type.name}</strong><span>{type.description || 'No description'}</span></div><b>{Number(type.base_rate).toFixed(2)}</b></div>)}</div> : <p className="muted">Create your first room type.</p>}</div>
        {isAdmin && <>
          <form className="panel form-panel" onSubmit={createType}><div className="panel-head"><h2>Add room type</h2></div><label>Name<input value={typeName} onChange={e => setTypeName(e.target.value)} required /></label><label>Base rate<input type="number" min="0" step="0.01" value={rate} onChange={e => setRate(e.target.value)} required /></label><label>Description<textarea value={typeDescription} onChange={e => setTypeDescription(e.target.value)} rows={2} /></label><button className="primary-button">Create room type</button></form>
          <form className="panel form-panel" onSubmit={createRoom}><div className="panel-head"><h2>Add room</h2></div><label>Room number<input value={roomNumber} onChange={e => setRoomNumber(e.target.value)} placeholder="101" required /></label><label>Room type<select value={roomTypeId} onChange={e => setRoomTypeId(e.target.value)} required><option value="">Select type</option>{roomTypes.map(type => <option key={type.id} value={type.id}>{type.name}</option>)}</select></label><button className="primary-button" disabled={!roomTypes.length}>Add room</button></form>
        </>}
      </div>
    </div>
  </section>;
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<AuthMode>('login');
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [roomTypes, setRoomTypes] = useState<RoomType[]>([]);
  const [view, setView] = useState<View>('Dashboard');
  const [error, setError] = useState('');

  async function loadHotelData() {
    const [d, r, rt] = await Promise.all([api<Dashboard>('/api/dashboard'), api<Room[]>('/api/rooms'), api<RoomType[]>('/api/room-types')]);
    setDashboard(d); setRooms(r); setRoomTypes(rt);
  }

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) { api<{ initialized: boolean }>('/api/auth/setup-status').then(s => setAuthMode(s.initialized ? 'login' : 'bootstrap')).catch(() => setError('Backend is not running.')).finally(() => setCheckingAuth(false)); return; }
    api<User>('/api/auth/me').then(setUser).catch(() => localStorage.removeItem(TOKEN_KEY)).finally(() => setCheckingAuth(false));
  }, []);

  useEffect(() => { if (!user) return; setError(''); loadHotelData().catch(err => { if ((err as Error & { status?: number }).status === 401) handleLogout(); else setError(err instanceof Error ? err.message : 'Unable to load hotel data'); }); }, [user]);

  function handleAuthenticated(nextUser: User, token: string) { localStorage.setItem(TOKEN_KEY, token); setUser(nextUser); setView('Dashboard'); }
  function handleLogout() { localStorage.removeItem(TOKEN_KEY); setUser(null); setDashboard(null); setRooms([]); setRoomTypes([]); setAuthMode('login'); }

  if (checkingAuth) return <main className="auth-shell"><p className="muted">Checking local session…</p></main>;
  if (!user) return <AuthScreen mode={authMode} setMode={setAuthMode} onAuthenticated={handleAuthenticated} />;

  return <main className="shell"><header className="topbar"><div><p className="eyebrow">LA SERENE HOTEL</p><h1>Hotel Management System</h1></div><div className="user-actions"><div className="user-chip"><strong>{user.username}</strong><span>{user.role}</span></div><button className="logout-button" onClick={handleLogout}>Log out</button></div></header>
    <nav className="module-nav">{modules.map(item => <button key={item} className={view === item ? 'active' : ''} onClick={() => setView(item)}>{item}</button>)}</nav>
    <section className="welcome"><div><p className="muted">{view === 'Rooms' ? 'Room inventory, rates and housekeeping' : 'Operations dashboard'}</p><h2>{dashboard ? `Business date · ${dashboard.business_date}` : 'Loading hotel data…'}</h2></div>{error && <p className="error">{error}</p>}</section>
    {view === 'Dashboard' ? <DashboardView dashboard={dashboard} rooms={rooms} roomTypes={roomTypes} /> : <RoomsView user={user} rooms={rooms} setRooms={setRooms} roomTypes={roomTypes} setRoomTypes={setRoomTypes} onRefresh={loadHotelData} />}
  </main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
