import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type User = { id: number; username: string; role: string };
type Dashboard = { business_date: string; total_rooms: number; available_rooms: number; reserved_rooms: number; occupied_rooms: number; dirty_rooms: number; out_of_order_rooms: number; arrivals_today: number; departures_today: number; in_house_guests: number };
type Room = { id: number; number: string; room_type_id: number; status: string };

type AuthMode = 'login' | 'bootstrap';

const modules = ['Dashboard', 'Rooms', 'Guests', 'Reservations', 'Front Desk', 'Billing'];
const TOKEN_KEY = 'la_serene_access_token';

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail ?? detail; } catch { /* ignore non-JSON errors */ }
    const error = new Error(detail);
    (error as Error & { status?: number }).status = response.status;
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
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'bootstrap') {
        await api('/api/auth/bootstrap-admin', {
          method: 'POST',
          body: JSON.stringify({ username, password }),
        });
      }
      const login = await api<{ access_token: string; user_id: number; username: string; role: string }>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      onAuthenticated({ id: login.user_id, username: login.username, role: login.role }, login.access_token);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div>
          <p className="eyebrow">LA SERENE HOTEL</p>
          <h1>{mode === 'bootstrap' ? 'Create your admin account' : 'Welcome back'}</h1>
          <p className="muted">{mode === 'bootstrap' ? 'This is a new local installation. Create the first administrator to continue.' : 'Sign in to continue to the hotel operations dashboard.'}</p>
        </div>
        <form onSubmit={submit} className="auth-form">
          <label>Username<input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={mode === 'bootstrap' ? 'new-password' : 'current-password'} minLength={mode === 'bootstrap' ? 8 : 1} required /></label>
          {error && <p className="form-error">{error}</p>}
          <button className="primary-button" disabled={busy}>{busy ? 'Please wait…' : mode === 'bootstrap' ? 'Create admin & sign in' : 'Sign in'}</button>
        </form>
        <button className="link-button" onClick={() => { setError(''); setMode(mode === 'login' ? 'bootstrap' : 'login'); }}>
          {mode === 'login' ? 'New installation? Create the first admin' : 'Already initialized? Sign in instead'}
        </button>
      </section>
    </main>
  );
}

function App() {
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<AuthMode>('login');
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      api<{ initialized: boolean }>('/api/auth/setup-status')
        .then((status) => setAuthMode(status.initialized ? 'login' : 'bootstrap'))
        .catch(() => setError('Backend is not running. Start the local API first.'))
        .finally(() => setCheckingAuth(false));
      return;
    }
    api<User>('/api/auth/me')
      .then(setUser)
      .catch(() => localStorage.removeItem(TOKEN_KEY))
      .finally(() => setCheckingAuth(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    setError('');
    Promise.all([api<Dashboard>('/api/dashboard'), api<Room[]>('/api/rooms')])
      .then(([d, r]) => { setDashboard(d); setRooms(r); })
      .catch((err) => {
        if ((err as Error & { status?: number }).status === 401) handleLogout();
        else setError(err instanceof Error ? err.message : 'Unable to load hotel data');
      });
  }, [user]);

  function handleAuthenticated(nextUser: User, token: string) {
    localStorage.setItem(TOKEN_KEY, token);
    setUser(nextUser);
  }

  function handleLogout() {
    localStorage.removeItem(TOKEN_KEY);
    setUser(null);
    setDashboard(null);
    setRooms([]);
    setAuthMode('login');
  }

  if (checkingAuth) return <main className="auth-shell"><p className="muted">Checking local session…</p></main>;
  if (!user) return <AuthScreen mode={authMode} setMode={setAuthMode} onAuthenticated={handleAuthenticated} />;

  const label = (status: string) => status.replace(/_/g, ' ');

  return (
    <main className="shell">
      <header className="topbar">
        <div><p className="eyebrow">LA SERENE HOTEL</p><h1>Hotel Management System</h1></div>
        <div className="user-actions"><div className="user-chip"><strong>{user.username}</strong><span>{user.role}</span></div><button className="logout-button" onClick={handleLogout}>Log out</button></div>
      </header>
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
