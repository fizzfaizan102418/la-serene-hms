import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import './frontdesk.css';
import BillingView from './BillingView';
import HousekeepingView from './HousekeepingView';
import ReportsView from './ReportsView';
import BackupView from './BackupView';
import ReservationsPMSView from './ReservationsPMSView';
import FrontDeskPMSView from './FrontDeskPMSView';

type User = { id: number; username: string; role: string };
type Dashboard = { business_date: string; total_rooms: number; available_rooms: number; reserved_rooms: number; occupied_rooms: number; dirty_rooms: number; out_of_order_rooms: number; arrivals_today: number; departures_today: number; in_house_guests: number };
type RoomType = { id: number; name: string; base_rate: number; description?: string | null };
type Room = { id: number; number: string; room_type_id: number; status: string };
type Guest = { id: number; full_name: string; phone?: string | null; email?: string | null; address?: string | null; id_document?: string | null };
type GuestDirectory = { items: Guest[]; total: number; limit: number; offset: number };
type Reservation = { id: number; guest_id: number; guest_name: string; check_in: string; check_out: string; status: string; room_ids: number[]; folio_id: number };
type FrontDeskData = { arrivals: Reservation[]; departures: Reservation[]; in_house: Reservation[] };
type BillingSummary = { folio_id: number; reservation_id: number; guest_name: string; status: string; total: number; paid: number; balance: number };
type ManagementReport = { business_date: string; rooms: { total: number; out_of_order: number; available_room_nights: number }; occupancy: { occupied_room_nights: number; occupancy_rate: number; checked_in_guests: number; arrivals: number; departures: number }; revenue: { room: number; total: number; adr: number; revpar: number }; finance: { reconciliation_status: string; revenue_difference: number; cash_difference: number; ledger_balanced: boolean }; receivables: { outstanding: number }; period: { posting_open: boolean } };
type DashboardFinance = { payments_received: number; payments_refunded: number; payments_net: number; payment_breakdown: { method: string; amount: number; received: number; refunded: number }[] };
type AuthMode = 'login' | 'bootstrap';
type View = 'Dashboard' | 'Rooms' | 'Guests' | 'Reservations' | 'Front Desk' | 'Housekeeping' | 'Reports' | 'Billing' | 'Backup';
type ApiError = Error & { status?: number; authToken?: string | null };

const TOKEN_KEY = 'la_serene_access_token';
const modules: View[] = ['Dashboard', 'Rooms', 'Guests', 'Reservations', 'Front Desk', 'Housekeeping', 'Reports', 'Billing', 'Backup'];
const statuses = ['available', 'reserved', 'occupied', 'dirty', 'out_of_order'] as const;

function sortRooms(rooms: Room[]): Room[] {
  return [...rooms].sort((a, b) => a.number.localeCompare(b.number, undefined, { numeric: true, sensitivity: 'base' }));
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = new Headers(options.headers);
  headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try { detail = (await response.json()).detail ?? detail; } catch { /* ignore */ }
    const error = new Error(detail) as ApiError;
    error.status = response.status;
    error.authToken = token;
    throw error;
  }
  return response.json() as Promise<T>;
}

function AuthScreen({ mode, setMode, onAuthenticated }: { mode: AuthMode; setMode: (mode: AuthMode) => void; onAuthenticated: (user: User, token: string) => void }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) { event.preventDefault(); setError(''); setBusy(true); try { if (mode === 'bootstrap') await api('/api/auth/bootstrap-admin', { method: 'POST', body: JSON.stringify({ username, password }) }); const login = await api<{ access_token: string; user_id: number; username: string; role: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }); onAuthenticated({ id: login.user_id, username: login.username, role: login.role }, login.access_token); } catch (err) { setError(err instanceof Error ? err.message : 'Something went wrong'); } finally { setBusy(false); } }
  return <main className="auth-shell"><section className="auth-card"><p className="eyebrow">LA SERENE HOTEL</p><h1>{mode === 'bootstrap' ? 'Create your admin account' : 'Welcome back'}</h1><p className="muted">{mode === 'bootstrap' ? 'Create the first administrator for this local installation.' : 'Sign in to continue to hotel operations.'}</p><form onSubmit={submit} className="auth-form"><label>Username<input value={username} onChange={e => setUsername(e.target.value)} required minLength={3} /></label><label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={mode === 'bootstrap' ? 8 : 1} /></label>{error && <p className="form-error">{error}</p>}<button className="primary-button" disabled={busy}>{busy ? 'Please wait…' : mode === 'bootstrap' ? 'Create admin & sign in' : 'Sign in'}</button></form><button className="link-button" onClick={() => { setError(''); setMode(mode === 'login' ? 'bootstrap' : 'login'); }}>{mode === 'login' ? 'New installation? Create the first admin' : 'Already initialized? Sign in instead'}</button></section><footer className="app-footer" aria-label="Application developer credit"><strong><span className="brand-highfly">HighFly</span> <span className="brand-ai">AI</span></strong></footer></main>;
}

function DashboardView({ dashboard, management, finance, rooms, roomTypes }: { dashboard: Dashboard | null; management: ManagementReport | null; finance: DashboardFinance | null; rooms: Room[]; roomTypes: RoomType[] }) {
  const statusCounts = useMemo(() => statuses.map(status => ({ status, count: rooms.filter(room => room.status === status).length })), [rooms]);
  const maxStatus = Math.max(1, ...statusCounts.map(item => item.count));
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]); const label = (status: string) => status.replace(/_/g, ' ');
  return <><section className="stats">{[['Occupancy', management ? management.occupancy.occupancy_rate.toFixed(1) + '%' : '—'], ['Room revenue', management ? Number(management.revenue.room).toFixed(2) : '—'], ['ADR', management ? Number(management.revenue.adr).toFixed(2) : '—'], ['RevPAR', management ? Number(management.revenue.revpar).toFixed(2) : '—'], ['Payments received', finance ? Number(finance.payments_received).toFixed(2) : '—'], ['Outstanding', management ? Number(management.receivables.outstanding).toFixed(2) : '—']].map(([name, value]) => <article className="stat" key={name as string}><span>{name}</span><strong>{value}</strong></article>)}</section><section className="workspace"><div className="panel dashboard-status-panel"><div className="panel-head"><div><p className="muted">Live inventory</p><h2>Room Status</h2></div><span>{rooms.length} rooms</span></div><div className="rooms">{rooms.length ? rooms.map(room => <div className={`room room-${room.status}`} key={room.id}><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Unassigned'} · {label(room.status)}</span></div>) : <p className="muted">No rooms configured yet.</p>}</div></div><div className="panel dashboard-analytics-panel"><div className="panel-head"><div><p className="muted">At a glance</p><h2>Room mix</h2></div><span>Live</span></div><div className="status-chart">{statusCounts.map(item => <div className="status-bar-row" key={item.status}><div><span>{item.status.replace(/_/g, " ")}</span><strong>{item.count}</strong></div><div className="status-bar-track"><i style={{ width: `${(item.count / maxStatus) * 100}%` }} /></div></div>)}</div><div className="dashboard-insight"><strong>{management?.occupancy.occupied_room_nights ?? dashboard?.occupied_rooms ?? 0} occupied room-night(s)</strong><span>{management ? management.occupancy.occupancy_rate.toFixed(1) : '0.0'}% occupancy</span></div></div><div className="panel"><div className="panel-head"><div><p className="muted">Live financial & operations</p><h2>Hotel performance</h2></div><span>{management?.finance.reconciliation_status || 'Loading'}</span></div><div className="operations"><div><span>Total revenue</span><strong>{management ? Number(management.revenue.total).toFixed(2) : '—'}</strong></div><div><span>In-house guests</span><strong>{management?.occupancy.checked_in_guests ?? dashboard?.in_house_guests ?? '—'}</strong></div><div><span>Arrivals / departures</span><strong>{management ? management.occupancy.arrivals + ' / ' + management.occupancy.departures : '—'}</strong></div><div><span>Ledger</span><strong>{management ? (management.finance.ledger_balanced ? 'Balanced' : 'Review') : '—'}</strong></div></div></div><div className="panel dashboard-finance-panel"><div className="panel-head"><div><p className="muted">Actual cashier activity</p><h2>Payment mix</h2></div><span>Current business date</span></div>{finance?.payment_breakdown.length ? <div className="status-chart">{finance.payment_breakdown.map(row => <div className="status-bar-row" key={row.method}><div><span>{row.method.replace(/_/g, ' ')}</span><strong>{Number(row.amount).toFixed(2)}</strong></div><div className="status-bar-track"><i style={{ width: Math.min(100, Math.max(0, (Number(row.amount) / Math.max(1, finance.payments_net)) * 100)) + '%' }} /></div></div>)}</div> : <p className="muted">No posted payment activity for this business date.</p>}<p className="dashboard-source">Dashboard financial figures are read from the hotel's current reporting and ledger data. No sample, generic, or forecast values are shown.</p></div></section></>;
}

function GuestsView({ user, guests, setGuests, onRefresh }: { user: User; guests: Guest[]; setGuests: React.Dispatch<React.SetStateAction<Guest[]>>; onRefresh: () => Promise<void> }) {
  const emptyForm = { full_name: '', phone: '', email: '', address: '', id_document: '' };
  const [query, setQuery] = useState('');
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [duplicateMatches, setDuplicateMatches] = useState<Guest[]>([]);
  const pageSize = 25;
  const canManage = user.role === 'admin' || user.role === 'reception';

  async function loadDirectory(nextPage = page, nextQuery = query) {
    setLoading(true);
    try {
      const offset = (nextPage - 1) * pageSize;
      const params = new URLSearchParams({ limit: String(pageSize), offset: String(offset) });
      if (nextQuery.trim()) params.set('q', nextQuery.trim());
      const result = await api<GuestDirectory>(`/api/guests/directory?${params.toString()}`);
      setGuests(result.items);
      setTotal(result.total);
      setPage(nextPage);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to load guests');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDirectory(1, '');
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadDirectory(1, query);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  async function checkDuplicates() {
    const params = new URLSearchParams();
    if (form.phone.trim()) params.set('phone', form.phone.trim());
    if (form.id_document.trim()) params.set('id_document', form.id_document.trim());
    if (editingId !== null) params.set('exclude_guest_id', String(editingId));
    if (!params.toString()) {
      setDuplicateMatches([]);
      return [];
    }
    const matches = await api<Guest[]>(`/api/guests/duplicate-check?${params.toString()}`);
    setDuplicateMatches(matches);
    return matches;
  }

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      const matches = await checkDuplicates();
      if (matches.length) {
        setMessage('A possible matching guest already exists. Review the record before creating another one.');
        return;
      }
      await api('/api/guests', { method: 'POST', body: JSON.stringify({ ...form, phone: form.phone || null, email: form.email || null, address: form.address || null, id_document: form.id_document || null }) });
      setForm(emptyForm);
      setDuplicateMatches([]);
      setMessage('Guest created.');
      await onRefresh();
      await loadDirectory(1, query);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to create guest');
    } finally {
      setBusy(false);
    }
  }

  function startEdit(guest: Guest) {
    setEditingId(guest.id);
    setForm({ full_name: guest.full_name, phone: guest.phone || '', email: guest.email || '', address: guest.address || '', id_document: guest.id_document || '' });
    setDuplicateMatches([]);
    setMessage('Editing guest profile.');
  }

  function cancelEdit() {
    setEditingId(null);
    setForm(emptyForm);
    setDuplicateMatches([]);
    setMessage('');
  }

  async function update(event: React.FormEvent) {
    event.preventDefault();
    if (editingId === null) return;
    setBusy(true);
    setMessage('');
    try {
      const matches = await checkDuplicates();
      if (matches.length) {
        setMessage('A possible matching guest already exists. Review the record before saving.');
        return;
      }
      await api<Guest>(`/api/guests/${editingId}`, { method: 'PATCH', body: JSON.stringify({ ...form, phone: form.phone || null, email: form.email || null, address: form.address || null, id_document: form.id_document || null }) });
      setMessage('Guest profile updated.');
      setEditingId(null);
      setForm(emptyForm);
      setDuplicateMatches([]);
      await onRefresh();
      await loadDirectory(page, query);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Unable to update guest');
    } finally {
      setBusy(false);
    }
  }

  const editingGuest = editingId === null ? null : guests.find(g => g.id === editingId) || null;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const firstRecord = total ? (page - 1) * pageSize + 1 : 0;
  const lastRecord = total ? Math.min(page * pageSize, total) : 0;

  return <section className="page">
    <div className="page-heading">
      <div><p className="muted">Guest master records</p><h2>Guests</h2></div>
      <div className="guest-heading-actions"><span className="room-count">{total.toLocaleString()} records</span>{canManage && <button className="primary-button" type="button" onClick={cancelEdit}>+ New guest</button>}</div>
    </div>

    {message && <p className="notice">{message}</p>}

    <div className="guest-directory panel">
      <div className="guest-toolbar">
        <div>
          <strong>Guest directory</strong>
          <span>{query.trim() ? `Searching for “${query.trim()}”` : 'Search by name, phone, email, address or ID document'}</span>
        </div>
        {loading && <span className="muted">Loading…</span>}
      </div>

      <form className="search-bar guest-search" onSubmit={event => { event.preventDefault(); void loadDirectory(1, query); }}>
        <input aria-label="Search guests" placeholder="Search name, phone, email, address or ID document…" value={query} onChange={e => setQuery(e.target.value)} />
        {query && <button className="secondary-button" type="button" onClick={() => setQuery('')}>Clear</button>}
      </form>

      <div className="guest-table-wrap">
        <table className="guest-table">
          <thead><tr><th>Guest</th><th>Phone</th><th>Email</th><th>Location</th><th>ID document</th><th></th></tr></thead>
          <tbody>
            {guests.length ? guests.map(g => <tr key={g.id}>
              <td><strong>{g.full_name}</strong></td>
              <td>{g.phone || '—'}</td>
              <td>{g.email || '—'}</td>
              <td>{g.address || '—'}</td>
              <td>{g.id_document ? `${g.id_document.slice(0, Math.max(0, g.id_document.length - 4)).replace(/./g, '•')}${g.id_document.slice(-4)}` : '—'}</td>
              <td className="guest-actions-cell">{canManage && <button className="secondary-button" type="button" onClick={() => startEdit(g)}>{editingId === g.id ? 'Editing' : 'Edit'}</button>}</td>
            </tr>) : <tr><td colSpan={6} className="guest-empty">{loading ? 'Loading guests…' : 'No guests found.'}</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="guest-pagination">
        <span>Showing {firstRecord.toLocaleString()}–{lastRecord.toLocaleString()} of {total.toLocaleString()}</span>
        <div>
          <button className="secondary-button" type="button" disabled={page <= 1 || loading} onClick={() => void loadDirectory(page - 1, query)}>Previous</button>
          <span>Page {page} of {totalPages}</span>
          <button className="secondary-button" type="button" disabled={page >= totalPages || loading} onClick={() => void loadDirectory(page + 1, query)}>Next</button>
        </div>
      </div>
    </div>

    {canManage && <form className="panel form-panel guest-form-panel" onSubmit={editingGuest ? update : create}>
      <div className="panel-head"><div><p className="muted">{editingGuest ? 'Update master record' : 'Create a new master record'}</p><h2>{editingGuest ? 'Edit guest' : 'New guest'}</h2></div>{editingGuest && <button className="link-button" type="button" onClick={cancelEdit}>Cancel</button>}</div>
      {duplicateMatches.length > 0 && <div className="duplicate-warning"><strong>Possible existing guest</strong><span>We found a matching phone number or ID document. Use the existing record if this is the same person.</span>{duplicateMatches.map(match => <div className="duplicate-row" key={match.id}><div><strong>{match.full_name}</strong><span>{match.phone || 'No phone'} · {match.id_document || 'No ID document'}</span></div><button className="secondary-button" type="button" onClick={() => startEdit(match)}>Open record</button></div>)}</div>}
      {([['full_name','Full name'],['phone','Phone'],['email','Email'],['address','Address'],['id_document','ID document']] as const).map(([key,label]) => <label key={key}>{label}{key === 'address' ? <textarea value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} rows={3} /> : <input type={key === 'email' ? 'email' : 'text'} value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} required={key === 'full_name'} />}</label>)}
      <p className="muted">Guest identity and contact details are maintained here. Reservations, Front Desk and Billing use this master record.</p>
      <button className="primary-button" disabled={busy}>{busy ? 'Saving…' : editingGuest ? 'Save guest changes' : 'Create guest'}</button>
    </form>}
  </section>;
}
function RoomsView({ user, rooms, setRooms, roomTypes, onRefresh }: { user: User; rooms: Room[]; setRooms: React.Dispatch<React.SetStateAction<Room[]>>; roomTypes: RoomType[]; onRefresh: () => Promise<void> }) {
  const [filter, setFilter] = useState('all'); const [message, setMessage] = useState(''); const [typeName, setTypeName] = useState(''); const [rate, setRate] = useState(''); const [desc, setDesc] = useState(''); const [number, setNumber] = useState(''); const [typeId, setTypeId] = useState(''); const [editingRoom, setEditingRoom] = useState<Room | null>(null); const [editNumber, setEditNumber] = useState(''); const [editTypeId, setEditTypeId] = useState(''); const [savingEdit, setSavingEdit] = useState(false);
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]); const admin = user.role === 'admin';
  async function createType(e: React.FormEvent) { e.preventDefault(); try { await api('/api/room-types', { method: 'POST', body: JSON.stringify({ name: typeName, base_rate: Number(rate || 0), description: desc || null }) }); setTypeName(''); setRate(''); setDesc(''); setMessage('Room type created.'); await onRefresh(); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to create room type'); } }
  async function createRoom(e: React.FormEvent) { e.preventDefault(); try { await api('/api/rooms', { method: 'POST', body: JSON.stringify({ number, room_type_id: Number(typeId) }) }); setNumber(''); setTypeId(''); setMessage('Room added.'); await onRefresh(); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to add room'); } }
  async function changeStatus(room: Room, next: string) { try { const updated = await api<Room>(`/api/rooms/${room.id}/status`, { method: 'PATCH', body: JSON.stringify({ status: next }) }); setRooms(cur => sortRooms(cur.map(r => r.id === updated.id ? updated : r))); await onRefresh(); setMessage(`Room ${updated.number} is now ${next.replace(/_/g, ' ')}.`); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to change status'); } }
  function beginEdit(room: Room) { if (!admin || room.status !== 'available') return; setEditingRoom(room); setEditNumber(room.number); setEditTypeId(String(room.room_type_id)); setMessage(''); }
  function cancelEdit() { if (savingEdit) return; setEditingRoom(null); setEditNumber(''); setEditTypeId(''); }
  async function saveEdit(e: React.FormEvent) { e.preventDefault(); if (!editingRoom) return; const trimmedNumber = editNumber.trim(); const selectedTypeId = Number(editTypeId); if (!trimmedNumber) { setMessage('Room number is required.'); return; } if (!Number.isInteger(selectedTypeId) || selectedTypeId <= 0) { setMessage('Please select a room type.'); return; } setSavingEdit(true); try { const updated = await api<Room>(`/api/rooms/${editingRoom.id}`, { method: 'PATCH', body: JSON.stringify({ number: trimmedNumber, room_type_id: selectedTypeId }) }); setRooms(cur => sortRooms(cur.map(r => r.id === updated.id ? updated : r))); setEditingRoom(null); setEditNumber(''); setEditTypeId(''); setMessage(`Room ${updated.number} updated successfully.`); await onRefresh(); } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to update room'); } finally { setSavingEdit(false); } }
  const visible = filter === 'all' ? rooms : rooms.filter(r => r.status === filter);
  return <section className="page"><div className="page-heading"><div><p className="muted">Inventory & housekeeping</p><h2>Rooms</h2></div><span className="room-count">{rooms.length} rooms</span></div>{message && <p className="notice">{message}</p>}<div className="room-layout"><div className="panel"><div className="panel-head"><h2>Room map</h2><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All statuses</option>{statuses.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}</select></div><div className="room-map">{visible.length ? visible.map(room => <article className={`room-card room-${room.status}`} key={room.id}><div className="room-card-top"><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Unknown type'}</span></div><small>{typeById.get(room.room_type_id) ? Number(typeById.get(room.room_type_id)!.base_rate).toFixed(2) : 'No rate'}</small><select value={room.status} onChange={e => void changeStatus(room, e.target.value)}>{statuses.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}</select>{admin && <button type="button" className="secondary-button" onClick={() => beginEdit(room)} disabled={room.status !== 'available'} title={room.status === 'available' ? 'Edit room number and room type' : 'Only available rooms can be edited'}>Edit</button>}</article>) : <p className="muted">No rooms match this filter.</p>}</div></div><div className="side-stack"><div className="panel"><div className="panel-head"><h2>Room types</h2><span>{roomTypes.length}</span></div><div className="type-list">{roomTypes.map(t => <div key={t.id}><div><strong>{t.name}</strong><span>{t.description || 'Standard room type'}</span></div><b>{Number(t.base_rate).toFixed(2)}</b></div>)}</div></div>{admin && <form className="panel form-panel" onSubmit={createType}><div className="panel-head"><h2>Add room type</h2></div><label>Name<input value={typeName} onChange={e => setTypeName(e.target.value)} required /></label><label>Base rate<input type="number" min="0" step="0.01" value={rate} onChange={e => setRate(e.target.value)} required /></label><label>Description<textarea value={desc} onChange={e => setDesc(e.target.value)} rows={2} /></label><button className="primary-button">Create room type</button></form>}{admin && <form className="panel form-panel" onSubmit={createRoom}><div className="panel-head"><h2>Add room</h2></div><label>Room number<input value={number} onChange={e => setNumber(e.target.value)} required /></label><label>Room type<select value={typeId} onChange={e => setTypeId(e.target.value)} required><option value="">Select type</option>{roomTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label><button className="primary-button" disabled={!roomTypes.length}>Add room</button></form>}{admin && editingRoom && <form className="panel form-panel" onSubmit={saveEdit}><div className="panel-head"><h2>Edit room {editingRoom.number}</h2></div><label>Room number<input value={editNumber} onChange={e => setEditNumber(e.target.value)} required /></label><label>Room type<select value={editTypeId} onChange={e => setEditTypeId(e.target.value)} required><option value="">Select type</option>{roomTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label><div className="form-actions"><button type="button" className="secondary-button" onClick={cancelEdit} disabled={savingEdit}>Cancel</button><button type="submit" className="primary-button" disabled={savingEdit || !roomTypes.length}>{savingEdit ? 'Saving...' : 'Save room changes'}</button></div></form>}</div></div></section>;
}

function App() {
  const [user, setUser] = useState<User | null>(null); const [management, setManagement] = useState<ManagementReport | null>(null); const [dashboardFinance, setDashboardFinance] = useState<DashboardFinance | null>(null); const [authMode, setAuthMode] = useState<AuthMode>('login'); const [checking, setChecking] = useState(true); const [dashboard, setDashboard] = useState<Dashboard | null>(null); const [rooms, setRooms] = useState<Room[]>([]); const [roomTypes, setRoomTypes] = useState<RoomType[]>([]); const [guests, setGuests] = useState<Guest[]>([]); const [reservations, setReservations] = useState<Reservation[]>([]); const [frontDesk, setFrontDesk] = useState<FrontDeskData>({ arrivals: [], departures: [], in_house: [] }); const [billing, setBilling] = useState<BillingSummary[]>([]); const [view, setView] = useState<View>('Dashboard'); const [error, setError] = useState('');
  const refreshGeneration = useRef(0);
  const visibleModules = user?.role === 'admin' ? modules : user?.role === 'reception' ? modules.filter(module => module !== 'Backup') : modules.filter(module => module !== 'Billing' && module !== 'Reports' && module !== 'Backup');

  function isCurrentSession(token: string | null, generation: number) {
    return generation === refreshGeneration.current && localStorage.getItem(TOKEN_KEY) === token && user !== null;
  }

  function clearAuthIfCurrentToken(token: string | null) {
    const currentToken = localStorage.getItem(TOKEN_KEY);
    if (currentToken === token) {
      localStorage.removeItem(TOKEN_KEY);
      return true;
    }
    return false;
  }

  async function refresh() {
    const generation = ++refreshGeneration.current;
    const refreshToken = localStorage.getItem(TOKEN_KEY);
    if (!refreshToken || !user) return;

    try {
      const [d, r, rt, g, rs, fd] = await Promise.all([
        api<Dashboard>('/api/dashboard'), api<Room[]>('/api/rooms'), api<RoomType[]>('/api/room-types'), api<Guest[]>('/api/guests'), api<Reservation[]>('/api/reservations'), api<FrontDeskData>('/api/front-desk')
      ]);

      if (!isCurrentSession(refreshToken, generation)) return;

      setDashboard(d); setRooms(sortRooms(r)); setRoomTypes(rt); setGuests(g); setReservations(rs); setFrontDesk(fd);

      if (user.role === 'admin' || user.role === 'reception') {
        const [managementData, reportData] = await Promise.all([
          api<ManagementReport>('/api/reports/management'),
          api<any>('/api/reports/summary?from_date=' + d.business_date + '&to_date=' + d.business_date),
        ]);
        if (!isCurrentSession(refreshToken, generation)) return;
        setManagement(managementData);
        setDashboardFinance({
          payments_received: Number(reportData.revenue?.payments_received || 0),
          payments_refunded: Number(reportData.revenue?.payments_refunded || 0),
          payments_net: Number(reportData.revenue?.payments_net || 0),
          payment_breakdown: Array.isArray(reportData.payment_breakdown) ? reportData.payment_breakdown.map((row: any) => ({ method: String(row.method), amount: Number(row.amount || 0), received: Number(row.received || 0), refunded: Number(row.refunded || 0) })) : [],
        });
      } else {
        setManagement(null); setDashboardFinance(null);
      }

      if (user.role === 'admin' || user.role === 'reception') {
        const billingData = await api<BillingSummary[]>('/api/billing');
        if (!isCurrentSession(refreshToken, generation)) return;
        setBilling(billingData);
      } else {
        setBilling([]);
      }
    } catch (err) {
      const apiError = err as ApiError;
      if (apiError.status === 401) {
        const cleared = clearAuthIfCurrentToken(apiError.authToken ?? refreshToken);
        if (cleared && localStorage.getItem(TOKEN_KEY) === null) logout();
        return;
      }
      throw err;
    }
  }

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      api<{ initialized: boolean }>('/api/auth/setup-status').then(s => setAuthMode(s.initialized ? 'login' : 'bootstrap')).catch(() => setError('Backend is not running.')).finally(() => setChecking(false));
      return;
    }

    api<User>('/api/auth/me').then(nextUser => {
      if (localStorage.getItem(TOKEN_KEY) === token) setUser(nextUser);
    }).catch(err => {
      const apiError = err as ApiError;
      if (apiError.status === 401) clearAuthIfCurrentToken(apiError.authToken ?? token);
    }).finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    if (!user) return;
    refresh().catch(err => setError(err instanceof Error ? err.message : 'Unable to load hotel data'));
  }, [user]);

  useEffect(() => {
    if (!visibleModules.includes(view)) setView('Dashboard');
  }, [user?.role]);

  useEffect(() => {
    const stopNumberWheelChange = (event: WheelEvent) => {
      const target = event.target;
      if (target instanceof HTMLInputElement && target.type === 'number' && document.activeElement === target) {
        event.preventDefault();
      }
    };
    document.addEventListener('wheel', stopNumberWheelChange, { passive: false });
    return () => document.removeEventListener('wheel', stopNumberWheelChange);
  }, []);


  function authenticated(next: User, token: string) {
    refreshGeneration.current += 1;
    localStorage.setItem(TOKEN_KEY, token);
    setUser(next); setView('Dashboard'); setError('');
  }

  function logout() {
    refreshGeneration.current += 1;
    localStorage.removeItem(TOKEN_KEY); setUser(null); setDashboard(null); setManagement(null); setDashboardFinance(null); setRooms([]); setRoomTypes([]); setGuests([]); setReservations([]); setFrontDesk({ arrivals: [], departures: [], in_house: [] }); setBilling([]); setAuthMode('login');
  }

  if (checking) return <main className="auth-shell"><p className="muted">Checking local session…</p></main>;
  if (!user) return <AuthScreen mode={authMode} setMode={setAuthMode} onAuthenticated={authenticated} />;

  return <main className="shell"><header className="topbar"><div><div className="brand-lockup" aria-label="HighFly AI"><span className="brand-highfly">HighFly</span> <span className="brand-ai">AI</span></div><div><p className="eyebrow">LA SERENE HOTEL & RESORT</p><h1>Hotel Management System</h1></div></div><div className="user-actions"><div className="user-chip"><strong>{user.username}</strong><span>{user.role}</span></div><button className="logout-button" onClick={logout}>Log out</button></div></header><nav className="module-nav">{visibleModules.map(m => <button key={m} className={view === m ? 'active' : ''} onClick={() => setView(m)}>{m}</button>)}</nav>{error && <p className="error">{error}</p>}{view === 'Dashboard' && <><section className="welcome"><div><p className="muted">Operations dashboard</p><h2>{dashboard ? `Business date · ${dashboard.business_date}` : 'Loading hotel data…'}</h2></div></section><DashboardView dashboard={dashboard} management={management} finance={dashboardFinance} rooms={rooms} roomTypes={roomTypes} /></>}{view === 'Rooms' && <RoomsView user={user} rooms={rooms} setRooms={setRooms} roomTypes={roomTypes} onRefresh={refresh} />}{view === 'Guests' && <GuestsView user={user} guests={guests} setGuests={setGuests} onRefresh={refresh} />}{view === 'Reservations' && <ReservationsPMSView user={user} guests={guests} rooms={rooms} roomTypes={roomTypes} reservations={reservations} onRefresh={refresh} api={api} />}{view === 'Front Desk' && <FrontDeskPMSView user={user} data={frontDesk} rooms={rooms} reservations={reservations} guests={guests} roomTypes={roomTypes} onRefresh={refresh} api={api} />}{view === 'Housekeeping' && <HousekeepingView userRole={user.role} api={api} onRefresh={refresh} />}{view === 'Reports' && <ReportsView api={api} />}{view === 'Billing' && <BillingView userRole={user.role} summaries={billing} onRefresh={refresh} api={api} />}{view === 'Backup' && <BackupView api={api} />}<footer className="app-footer" aria-label="Application developer credit"><strong><span className="brand-highfly">HighFly</span> <span className="brand-ai">AI</span></strong></footer></main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
