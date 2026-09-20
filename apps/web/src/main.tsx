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
type DashboardClosingPack = { report: { revenue: { room: number; gross: number }; payments: Record<string, number>; outstanding: number; occupancy: { total_rooms: number; occupied_rooms: number }; finance?: { reconciliation_status?: string } } };
type DashboardHistoryDay = { date: string; occupancy: number; room_revenue: number | null; adr: number | null; revpar: number | null; total_revenue: number; payments: number; outstanding: number; closed: boolean };
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

function formatDashboardDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  return new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short' }).format(new Date(Date.UTC(year, month - 1, day)));
}

function previousBusinessDates(endDate: string, count: number) {
  const [year, month, day] = endDate.split('-').map(Number);
  const cursor = new Date(Date.UTC(year, month - 1, day));
  const dates: string[] = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    const d = new Date(cursor);
    d.setUTCDate(cursor.getUTCDate() - i);
    dates.push(d.toISOString().slice(0, 10));
  }
  return dates;
}

function DashboardView({ dashboard, management, finance, history, rooms, roomTypes, selectedDate, historicalPack, reservations, frontDesk, onSelectDate }: { dashboard: Dashboard | null; management: ManagementReport | null; finance: DashboardFinance | null; history: DashboardHistoryDay[]; rooms: Room[]; roomTypes: RoomType[]; selectedDate: string | null; historicalPack: DashboardClosingPack | null; reservations: Reservation[]; frontDesk: FrontDeskData; onSelectDate: (value: string) => Promise<void> }) {
  const historical = Boolean(selectedDate && dashboard && selectedDate !== dashboard.business_date);
  const report = historicalPack?.report;
  const typeById = useMemo(() => new Map(roomTypes.map(t => [t.id, t])), [roomTypes]);
  const statusCounts = useMemo(() => statuses.map(status => ({ status, count: rooms.filter(room => room.status === status).length })), [rooms]);
  const paymentRows = historical
    ? (report?.payments ? Object.entries(report.payments).filter(([method, value]) => method !== 'total' && Number(value || 0) !== 0).map(([method, value]) => ({ method, amount: Number(value || 0) })) : [])
    : (finance?.payment_breakdown || []).map(row => ({ method: row.method, amount: Number(row.amount || 0) }));
  const paymentTotal = Math.max(0, paymentRows.reduce((sum, row) => sum + Math.max(0, row.amount), 0));
  let paymentOffset = 0;
  const paymentGradient = paymentRows.length && paymentTotal > 0
    ? paymentRows.map((row, index) => { const start = paymentOffset; paymentOffset += (Math.max(0, row.amount) / paymentTotal) * 100; return `var(--chart-${Math.min(index, 5) + 1}) ${start.toFixed(2)}% ${paymentOffset.toFixed(2)}%`; }).join(', ')
    : 'var(--chart-muted) 0 100%';
  let roomOffset = 0;
  const roomColors: Record<string, string> = { occupied: '#3B82F6', available: '#E2E8F0', reserved: '#F59E0B', dirty: '#64748B', out_of_order: '#DC2626' };
  const roomChartCounts = statusCounts;
  const roomTotal = Math.max(0, roomChartCounts.reduce((sum, row) => sum + row.count, 0));
  const roomGradient = roomTotal > 0
    ? roomChartCounts.map(row => { const start = roomOffset; roomOffset += (row.count / roomTotal) * 100; return `${roomColors[row.status]} ${start.toFixed(2)}% ${roomOffset.toFixed(2)}%`; }).join(', ')
    : '#E2E8F0 0 100%';
  const occupied = historical ? Number(report?.occupancy?.occupied_rooms || 0) : Number(management?.occupancy.occupied_room_nights ?? dashboard?.occupied_rooms ?? 0);
  const totalRooms = historical ? Number(report?.occupancy?.total_rooms || 0) : Number(management?.rooms.total ?? dashboard?.total_rooms ?? rooms.length);
  const occupancy = totalRooms ? (occupied / totalRooms) * 100 : 0;
  const selectedHistory = history.find(day => day.date === selectedDate);
  const currentReservations = reservations.filter(r => r.status !== 'cancelled');
  const arrivals = historical ? [] : frontDesk.arrivals;
  const departures = historical ? [] : frontDesk.departures;
  const inHouse = historical ? [] : frontDesk.in_house;
  const chartMaxRevenue = Math.max(1, ...history.map(item => item.total_revenue));
  const revenuePoints = history.map((day, index) => ({ ...day, x: history.length === 1 ? 50 : 10 + (index / (history.length - 1)) * 80, y: 92 - (Math.max(0, day.total_revenue) / chartMaxRevenue) * 72 }));
  const revenuePath = revenuePoints.map((point, index) => `${index ? 'L' : 'M'} ${point.x} ${point.y}`).join(' ');
  const kpis = historical
    ? [['Occupancy', `${occupancy.toFixed(1)}%`], ['Room revenue', Number(report?.revenue?.room || 0).toFixed(2)], ['ADR', occupied ? (Number(report?.revenue?.room || 0) / occupied).toFixed(2) : '—'], ['RevPAR', totalRooms ? (Number(report?.revenue?.room || 0) / totalRooms).toFixed(2) : '—'], ['Payments received', Number(report?.payments?.total || 0).toFixed(2)], ['Outstanding', Number(report?.outstanding || 0).toFixed(2)]]
    : [['Occupancy', `${occupancy.toFixed(1)}%`], ['Room revenue', management ? Number(management.revenue.room).toFixed(2) : '—'], ['ADR', management ? Number(management.revenue.adr).toFixed(2) : '—'], ['RevPAR', management ? Number(management.revenue.revpar).toFixed(2) : '—'], ['Payments received', finance ? Number(finance.payments_received).toFixed(2) : '—'], ['Outstanding', management ? Number(management.receivables.outstanding).toFixed(2) : '—']];

  const roomLabel = (status: string) => status.replace(/_/g, ' ');
  const reservationGuest = (r: Reservation) => r.guest_name || 'Guest';
  const reservationRooms = (r: Reservation) => r.room_ids.length ? r.room_ids.map(id => rooms.find(room => room.id === id)?.number || String(id)).join(', ') : '—';

  return <>
    <section className={`dashboard-hero dashboard-hero-final ${historical ? 'historical' : 'live'}`}>
      <div className="dashboard-hero-copy">
        <p className="dashboard-hero-eyebrow">{historical ? 'Archived hotel performance' : 'Live hotel operations'}</p>
        <h2>{historical ? 'Closing snapshot' : 'Operations at a glance'}</h2>
        <span>{historical ? `Daily Closing record · ${formatDashboardDate(selectedDate!)}` : 'Revenue, occupancy, rooms and front-desk activity in one view.'}</span>
      </div>
      <div className="dashboard-hero-meta">
        <div className={`dashboard-mode-badge ${historical ? 'historical' : 'live'}`}>{historical ? 'HISTORICAL · CLOSED' : 'LIVE · BUSINESS DATE'}</div>
        <div className="dashboard-hero-stat"><span>Occupancy</span><strong>{occupancy.toFixed(1)}%</strong></div>
        <div className="dashboard-hero-stat"><span>Rooms</span><strong>{totalRooms}</strong></div>
      </div>
    </section>

    {historical && <div className="dashboard-history-banner"><strong>Historical View · {formatDashboardDate(selectedDate!)}</strong><span>Archived closing figures only. Current room-level operations are intentionally excluded.</span></div>}

    <section className="dashboard-attention-strip">
      <div><span className="dashboard-attention-icon">!</span><div><strong>Operational attention</strong><small>{historical ? 'Historical mode shows archived closing figures only.' : `${dashboard?.dirty_rooms ?? 0} room${(dashboard?.dirty_rooms ?? 0) === 1 ? '' : 's'} need cleaning · ${dashboard?.arrivals_today ?? arrivals.length} arrival${(dashboard?.arrivals_today ?? arrivals.length) === 1 ? '' : 's'} · ${dashboard?.departures_today ?? departures.length} departure${(dashboard?.departures_today ?? departures.length) === 1 ? '' : 's'}`}</small></div></div>
      {!historical && <div className="dashboard-attention-metrics"><span>Rooms <strong>{totalRooms}</strong></span><span>In house <strong>{dashboard?.in_house_guests ?? inHouse.length}</strong></span><span>Dirty <strong>{dashboard?.dirty_rooms ?? 0}</strong></span></div>}
    </section>
    <section className="dashboard-kpi-grid">{kpis.map(([name, value]) => <article className="dashboard-kpi" key={name as string}><span>{name}</span><strong>{value}</strong></article>)}</section>

    <section className="dashboard-main-grid">
      <section className="panel dashboard-line-panel dashboard-revenue-card">
        <div className="panel-head"><div><p className="muted">Actual hotel history</p><h2>Revenue performance</h2></div><span>Last 5 business dates</span></div>
        {history.length ? <div className="dashboard-line-chart"><svg viewBox="0 0 100 100" role="img" aria-label="Five-day total revenue trend">
          <line x1="10" y1="20" x2="90" y2="20" className="chart-grid-line" /><line x1="10" y1="56" x2="90" y2="56" className="chart-grid-line" /><line x1="10" y1="92" x2="90" y2="92" className="chart-axis-line" />
          <path d={revenuePath} className="chart-line revenue-line" />
          {revenuePoints.map(point => <circle key={point.date} cx={point.x} cy={point.y} r={point.date === selectedDate ? 2.7 : 2.1} className={point.date === selectedDate ? 'chart-point revenue-point selected' : 'chart-point revenue-point'} onClick={() => void onSelectDate(point.date)}><title>{`${formatDashboardDate(point.date)} · Revenue PKR ${Number(point.total_revenue).toLocaleString('en-PK')} · Occupancy ${point.occupancy.toFixed(1)}% · ${point.closed ? 'Closed' : 'Open'}`}</title></circle>)}
        </svg>
        <div className="line-chart-labels">{history.map(day => <button type="button" key={day.date} className={day.date === selectedDate ? 'selected' : ''} onClick={() => void onSelectDate(day.date)}><span>{formatDashboardDate(day.date)}</span><small>PKR {Number(day.total_revenue).toLocaleString('en-PK', { maximumFractionDigits: 0 })}</small></button>)}</div>
        <div className="dashboard-chart-detail">{selectedHistory ? <><div><span>{formatDashboardDate(selectedHistory.date)}</span><strong>PKR {Number(selectedHistory.total_revenue).toLocaleString('en-PK', { maximumFractionDigits: 0 })}</strong></div><div><span>Occupancy</span><strong>{selectedHistory.occupancy.toFixed(1)}%</strong></div><div><span>Status</span><strong>{selectedHistory.closed ? 'Closed' : 'Open'}</strong></div></> : <p className="muted">Select a business date to inspect its details.</p>}</div>
        </div> : <p className="muted">Historical dashboard data is loading…</p>}
        <div className="chart-legend"><span><i className="legend-dot revenue" />Total revenue</span><span className="chart-legend-note">Historical values come from Daily Closing packs when available.</span></div>
      </section>

      <section className="panel dashboard-ops-card">
        <div className="panel-head"><div><p className="muted">{historical ? 'Archived performance' : 'Today at a glance'}</p><h2>{historical ? 'Closing snapshot' : 'Operations'}</h2></div><span>{historical ? 'Closed' : dashboard?.business_date || '—'}</span></div>
        {!historical && <div className="dashboard-room-pie-layout">
          <div className="dashboard-pie dashboard-room-pie" style={{ background: `conic-gradient(${roomGradient})` }} aria-label="Room status distribution pie chart" />
          <div className="dashboard-pie-list">
            {roomChartCounts.map(row => <div key={row.status}><span><i className="legend-dot" style={{ background: roomColors[row.status] }} />{roomLabel(row.status)}</span><strong>{row.count}</strong></div>)}
          </div>
        </div>}
        <div className="dashboard-ops-grid">
          <div><span>Occupancy</span><strong>{occupancy.toFixed(1)}%</strong></div>
          <div><span>Occupied rooms</span><strong>{occupied}</strong></div>
          <div><span>Total rooms</span><strong>{totalRooms}</strong></div>
          {!historical && <><div><span>Arrivals</span><strong>{dashboard?.arrivals_today ?? arrivals.length}</strong></div><div><span>Departures</span><strong>{dashboard?.departures_today ?? departures.length}</strong></div><div><span>In house</span><strong>{dashboard?.in_house_guests ?? inHouse.length}</strong></div></>}
          {historical && <><div><span>Gross revenue</span><strong>PKR {Number(report?.revenue?.gross || 0).toLocaleString('en-PK')}</strong></div><div><span>Outstanding</span><strong>PKR {Number(report?.outstanding || 0).toLocaleString('en-PK')}</strong></div><div><span>Ledger</span><strong>{report?.finance?.reconciliation_status || 'Archived'}</strong></div></>}
        </div>
      </section>
    </section>

    {!historical && <section className="dashboard-three-grid">
      <section className="panel"><div className="panel-head"><div><p className="muted">Live inventory</p><h2>Room status</h2></div><span>{rooms.length} rooms</span></div><div className="dashboard-room-grid">{rooms.map(room => <div className={`dashboard-room room-${room.status}`} key={room.id}><strong>{room.number}</strong><span>{roomLabel(room.status)}</span><small>{typeById.get(room.room_type_id)?.name || 'Unassigned'}</small></div>)}</div></section>

      <section className="panel"><div className="panel-head"><div><p className="muted">Front desk</p><h2>Today's arrivals</h2></div><span>{arrivals.length}</span></div>{arrivals.length ? <div className="dashboard-list">{arrivals.map(r => <div key={r.id}><div><strong>{reservationGuest(r)}</strong><span>Room {reservationRooms(r)}</span><small className={r.status === 'checked_in' ? 'dashboard-arrival-status checked-in' : 'dashboard-arrival-status'}>{r.status === 'checked_in' ? 'Checked in' : 'Expected arrival'}</small></div><b>{r.check_in}</b></div>)}</div> : <p className="muted">No arrivals recorded for this business date.</p>}<div className="dashboard-mini-stats"><span>Departures <strong>{departures.length}</strong></span><span>In house <strong>{inHouse.length}</strong></span><span>Reservations <strong>{currentReservations.length}</strong></span></div></section>

      <section className="panel"><div className="panel-head"><div><p className="muted">Cashier</p><h2>Payment mix</h2></div><span>Posted</span></div><div className="dashboard-pie-layout"><div className="dashboard-pie" style={{ background: `conic-gradient(${paymentGradient})` }} aria-label="Payment mix pie chart" /><div className="dashboard-pie-list">{paymentRows.length ? paymentRows.map((row, index) => <div key={row.method}><span><i className={`legend-dot chart-${Math.min(index, 5) + 1}`} />{row.method.replace(/_/g, ' ')}</span><strong>PKR {Number(row.amount).toLocaleString('en-PK')}</strong></div>) : <p className="muted">No posted payment activity for this business date.</p>}</div></div><div className="cashier-summary-grid dashboard-cashier-summary-grid"><div><span>Received</span><strong>PKR {finance ? Number(finance.payments_received).toLocaleString('en-PK') : '—'}</strong></div><div><span>Refunded</span><strong>PKR {finance ? Number(finance.payments_refunded).toLocaleString('en-PK') : '—'}</strong></div><div><span>Net posted</span><strong>PKR {finance ? Number(finance.payments_net).toLocaleString('en-PK') : '—'}</strong></div><div><span>Outstanding</span><strong>PKR {management ? Number(management.receivables.outstanding).toLocaleString('en-PK') : '—'}</strong></div></div></section>
    </section>}

    {!historical && <section className="dashboard-two-grid">
      <section className="panel"><div className="panel-head"><div><p className="muted">Financial controls</p><h2>Hotel performance</h2></div><span>{management?.finance.reconciliation_status || 'Loading'}</span></div><div className="dashboard-control-grid"><div><span>Total revenue</span><strong>PKR {management ? Number(management.revenue.total).toLocaleString('en-PK') : '—'}</strong></div><div><span>Ledger</span><strong>{management ? (management.finance.ledger_balanced ? 'Balanced' : 'Review') : '—'}</strong></div><div><span>Revenue difference</span><strong>PKR {management ? Number(management.finance.revenue_difference).toLocaleString('en-PK') : '—'}</strong></div><div><span>Cash difference</span><strong>PKR {management ? Number(management.finance.cash_difference).toLocaleString('en-PK') : '—'}</strong></div><div><span>Room charges pending</span><strong>{management?.period.posting_open ? 'Yes' : 'No'}</strong></div><div><span>Guest outstanding</span><strong>PKR {management ? Number(management.receivables.outstanding).toLocaleString('en-PK') : '—'}</strong></div></div>{management?.period.posting_open && <p className="dashboard-audit-note">The business date is still open. Room revenue remains based on posted production data; pending Night Audit charges are not invented or pre-posted.</p>}</section>

      <section className="panel"><div className="panel-head"><div><p className="muted">End of day</p><h2>Night Audit</h2></div><span>{management?.period.posting_open ? 'OPEN' : 'READY'}</span></div><div className="dashboard-control-grid"><div><span>Business date</span><strong>{dashboard?.business_date || '—'}</strong></div><div><span>Posting status</span><strong>{management?.period.posting_open ? 'Open' : 'Closed'}</strong></div><div><span>Daily closing</span><strong>{management?.period.posting_open ? 'Pending' : 'Completed'}</strong></div><div><span>Reconciliation</span><strong>{management?.finance.reconciliation_status || '—'}</strong></div></div></section>
    </section>}

    {historical && <section className="dashboard-two-grid"><section className="panel"><div className="panel-head"><div><p className="muted">Archived financial controls</p><h2>Closing summary</h2></div><span>Closed</span></div><div className="dashboard-control-grid"><div><span>Total revenue</span><strong>PKR {Number(report?.revenue?.gross || 0).toLocaleString('en-PK')}</strong></div><div><span>Payments</span><strong>PKR {Number(report?.payments?.total || 0).toLocaleString('en-PK')}</strong></div><div><span>Outstanding</span><strong>PKR {Number(report?.outstanding || 0).toLocaleString('en-PK')}</strong></div><div><span>Ledger</span><strong>{report?.finance?.reconciliation_status || 'Archived'}</strong></div></div></section><section className="panel"><div className="panel-head"><div><p className="muted">Archived inventory</p><h2>Occupancy snapshot</h2></div><span>{formatDashboardDate(selectedDate!)}</span></div><div className="historical-room-summary"><strong>{occupied} / {totalRooms}</strong><span>occupied rooms · {occupancy.toFixed(1)}% occupancy</span></div><p className="dashboard-source">Historical mode uses the archived closing record. Live room-level status is not shown.</p></section></section>}
  </>;
}
function GuestsView({ user, guests, setGuests, reservations, onRefresh }: { user: User; guests: Guest[]; setGuests: React.Dispatch<React.SetStateAction<Guest[]>>; reservations: Reservation[]; onRefresh: () => Promise<void> }) {
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
  const [selectedGuestId, setSelectedGuestId] = useState<number | null>(null);
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
  const selectedGuest = selectedGuestId === null ? null : guests.find(g => g.id === selectedGuestId) || null;
  const selectedGuestReservations = selectedGuest ? reservations.filter(r => r.guest_id === selectedGuest.id).sort((a, b) => b.check_in.localeCompare(a.check_in)) : [];
  const selectedGuestActiveStays = selectedGuestReservations.filter(r => r.status === 'reserved' || r.status === 'checked_in');

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
            {guests.length ? guests.map(g => <tr key={g.id} className={selectedGuestId === g.id ? 'guest-row-selected' : ''} onClick={() => setSelectedGuestId(g.id)}>
              <td><button type="button" className="guest-name-button" onClick={() => setSelectedGuestId(g.id)}><strong>{g.full_name}</strong><small>Guest #{g.id}</small></button></td>
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

    {selectedGuest && <section className="panel guest-profile-panel">
      <div className="panel-head"><div><p className="muted">Guest record</p><h2>{selectedGuest.full_name}</h2></div><div className="guest-profile-actions">{canManage && <button className="secondary-button small-button" type="button" onClick={() => startEdit(selectedGuest)}>Edit profile</button>}<button className="link-button" type="button" onClick={() => setSelectedGuestId(null)}>Close</button></div></div>
      <div className="guest-profile-grid">
        <div><span>Phone</span><strong>{selectedGuest.phone || '—'}</strong></div><div><span>Email</span><strong>{selectedGuest.email || '—'}</strong></div><div><span>Address</span><strong>{selectedGuest.address || '—'}</strong></div><div><span>ID document</span><strong>{selectedGuest.id_document ? `${'•'.repeat(Math.max(0, selectedGuest.id_document.length - 4))}${selectedGuest.id_document.slice(-4)}` : '—'}</strong></div>
      </div>
      <div className="guest-history-summary"><div><span>Active reservations</span><strong>{selectedGuestActiveStays.length}</strong></div><div><span>Total reservations</span><strong>{selectedGuestReservations.length}</strong></div></div>
      <div className="guest-history-list"><div className="panel-head"><div><p className="muted">Stay history</p><h3>Reservations & stays</h3></div><span>{selectedGuestReservations.length} record{selectedGuestReservations.length === 1 ? '' : 's'}</span></div>
        {selectedGuestReservations.length ? selectedGuestReservations.slice(0, 8).map(r => <div className="guest-history-row" key={r.id}><div><strong>Reservation #{r.id}</strong><span>{r.check_in} → {r.check_out} · {r.room_ids.length} room{r.room_ids.length === 1 ? '' : 's'}</span></div><b className={`guest-history-status status-${r.status}`}>{r.status.replace(/_/g, ' ')}</b></div>) : <p className="muted">No reservation history is available in the current operational dataset.</p>}
      </div>
    </section>}

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
  const counts = statuses.reduce<Record<string, number>>((acc, status) => { acc[status] = rooms.filter(r => r.status === status).length; return acc; }, {});
  const visible = filter === 'all' ? rooms : rooms.filter(r => r.status === filter);
  const label = (s: string) => s.replace(/_/g, ' ');
  return <section className="page rooms-page">
    <div className="page-heading"><div><p className="muted">Inventory & housekeeping</p><h2>Rooms</h2></div><span className="room-count">{rooms.length} rooms</span></div>
    {message && <p className="notice">{message}</p>}
    <div className="room-status-summary">{statuses.map(status => <button type="button" key={status} className={filter === status ? 'active' : ''} onClick={() => setFilter(filter === status ? 'all' : status)}><i className={`room-status-dot room-status-${status}`} /><span>{label(status)}</span><strong>{counts[status] || 0}</strong></button>)}</div>
    <div className="room-layout rooms-workspace">
      <div className="panel room-map-panel">
        <div className="panel-head"><div><p className="muted">Live inventory</p><h2>{filter === 'all' ? 'Room map' : `${label(filter)} rooms`}</h2></div><select aria-label="Filter rooms by status" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">All statuses</option>{statuses.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></div>
        <div className="room-map room-map-modern">{visible.length ? visible.map(room => <article className={`room-card room-card-modern room-${room.status}`} key={room.id}>
          <div className="room-card-top"><div><strong>{room.number}</strong><span>{typeById.get(room.room_type_id)?.name ?? 'Unknown type'}</span></div><span className={`room-status-pill room-status-pill-${room.status}`}>{label(room.status)}</span></div>
          <div className="room-card-rate">PKR {typeById.get(room.room_type_id) ? Number(typeById.get(room.room_type_id)!.base_rate).toLocaleString('en-PK') : '—'} <span>base rate</span></div>
          <label className="room-status-control"><span>Status</span><select value={room.status} onChange={e => void changeStatus(room, e.target.value)}>{statuses.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></label>
          {admin && <button type="button" className="secondary-button room-edit-button" onClick={() => beginEdit(room)} disabled={room.status !== 'available'} title={room.status === 'available' ? 'Edit room number and room type' : 'Only available rooms can be edited'}>Edit room</button>}
        </article>) : <div className="room-empty"><strong>No rooms in this status</strong><span>Choose another status to view the live room inventory.</span></div>}</div>
      </div>
      <div className="side-stack">
        <div className="panel"><div className="panel-head"><div><p className="muted">Configuration</p><h2>Room types</h2></div><span>{roomTypes.length}</span></div><div className="type-list">{roomTypes.map(t => <div key={t.id}><div><strong>{t.name}</strong><span>{t.description || 'Standard room type'}</span></div><b>PKR {Number(t.base_rate).toLocaleString('en-PK')}</b></div>)}</div></div>
        {admin && <form className="panel form-panel" onSubmit={createType}><div className="panel-head"><div><p className="muted">Admin</p><h2>Add room type</h2></div></div><label>Name<input value={typeName} onChange={e => setTypeName(e.target.value)} required /></label><label>Base rate<input type="number" min="0" step="0.01" value={rate} onChange={e => setRate(e.target.value)} required /></label><label>Description<textarea value={desc} onChange={e => setDesc(e.target.value)} rows={2} /></label><button className="primary-button">Create room type</button></form>}
        {admin && <form className="panel form-panel" onSubmit={createRoom}><div className="panel-head"><div><p className="muted">Admin</p><h2>Add room</h2></div></div><label>Room number<input value={number} onChange={e => setNumber(e.target.value)} required /></label><label>Room type<select value={typeId} onChange={e => setTypeId(e.target.value)} required><option value="">Select type</option>{roomTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label><button className="primary-button" disabled={!roomTypes.length}>Add room</button></form>}
        {admin && editingRoom && <form className="panel form-panel" onSubmit={saveEdit}><div className="panel-head"><div><p className="muted">Room configuration</p><h2>Edit room {editingRoom.number}</h2></div></div><label>Room number<input value={editNumber} onChange={e => setEditNumber(e.target.value)} required /></label><label>Room type<select value={editTypeId} onChange={e => setEditTypeId(e.target.value)} required><option value="">Select type</option>{roomTypes.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label><div className="form-actions"><button type="button" className="secondary-button" onClick={cancelEdit} disabled={savingEdit}>Cancel</button><button type="submit" className="primary-button" disabled={savingEdit || !roomTypes.length}>{savingEdit ? 'Saving...' : 'Save room changes'}</button></div></form>}
      </div>
    </div>
  </section>;
}
function App() {
  const [user, setUser] = useState<User | null>(null); const [management, setManagement] = useState<ManagementReport | null>(null); const [dashboardFinance, setDashboardFinance] = useState<DashboardFinance | null>(null); const [dashboardDate, setDashboardDate] = useState<string | null>(null); const [dashboardHistoricalPack, setDashboardHistoricalPack] = useState<DashboardClosingPack | null>(null); const [dashboardHistory, setDashboardHistory] = useState<DashboardHistoryDay[]>([]); const [authMode, setAuthMode] = useState<AuthMode>('login'); const [checking, setChecking] = useState(true); const [dashboard, setDashboard] = useState<Dashboard | null>(null); const [rooms, setRooms] = useState<Room[]>([]); const [roomTypes, setRoomTypes] = useState<RoomType[]>([]); const [guests, setGuests] = useState<Guest[]>([]); const [reservations, setReservations] = useState<Reservation[]>([]); const [frontDesk, setFrontDesk] = useState<FrontDeskData>({ arrivals: [], departures: [], in_house: [] }); const [billing, setBilling] = useState<BillingSummary[]>([]); const [view, setView] = useState<View>('Dashboard'); const [error, setError] = useState('');
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
        const historyDates = previousBusinessDates(d.business_date, 5);
        const historyRows = await Promise.all(historyDates.map(async date => {
          const liveReport = await api<any>(`/api/reports/summary?from_date=${date}&to_date=${date}`);
          try {
            const pack = await api<DashboardClosingPack>(`/api/night-audit/pack/${date}/daily-closing.json`);
            const p = pack.report;
            const roomsSold = Number(p.occupancy?.occupied_rooms || 0);
            const roomsTotal = Math.max(0, Number(p.occupancy?.total_rooms || 0));
            const roomRevenue = Number(p.revenue?.room || 0);
            return {
              date,
              occupancy: roomsTotal ? (roomsSold / roomsTotal) * 100 : 0,
              room_revenue: roomRevenue,
              adr: roomsSold ? roomRevenue / roomsSold : 0,
              revpar: roomsTotal ? roomRevenue / roomsTotal : 0,
              total_revenue: Number(p.revenue?.gross || 0),
              payments: Number(p.payments?.total || 0),
              outstanding: Number(p.outstanding || 0),
              closed: true,
            } satisfies DashboardHistoryDay;
          } catch {
            return {
              date,
              occupancy: Number(liveReport.rooms?.occupancy_rate || 0),
              room_revenue: date === d.business_date ? Number(managementData.revenue?.room || 0) : null,
              adr: date === d.business_date ? Number(managementData.revenue?.adr || 0) : null,
              revpar: date === d.business_date ? Number(managementData.revenue?.revpar || 0) : null,
              total_revenue: Number(liveReport.revenue?.gross || liveReport.revenue?.net || 0),
              payments: Number(liveReport.revenue?.payments_received || 0),
              outstanding: Number(liveReport.revenue?.outstanding_balance || 0),
              closed: false,
            } satisfies DashboardHistoryDay;
          }
        }));
        if (!isCurrentSession(refreshToken, generation)) return;
        setDashboardHistory(historyRows);

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

  async function selectDashboardDate(value: string) {
    if (!dashboard || !value || value > dashboard.business_date) return;
    setError('');
    if (value === dashboard.business_date) { setDashboardDate(value); setDashboardHistoricalPack(null); return; }
    try {
      const pack = await api<DashboardClosingPack>(`/api/night-audit/pack/${value}/daily-closing.json`);
      setDashboardDate(value);
      setDashboardHistoricalPack(pack);
    } catch (err) {
      setDashboardHistoricalPack(null);
      setError(err instanceof Error ? `No archived Daily Closing is available for ${formatDashboardDate(value)}.` : 'Unable to load historical dashboard data.');
    }
  }

  function returnDashboardToToday() {
    if (!dashboard) return;
    setDashboardDate(dashboard.business_date);
    setDashboardHistoricalPack(null);
    setError('');
  }

  function logout() {
    refreshGeneration.current += 1;
    localStorage.removeItem(TOKEN_KEY); setUser(null); setDashboard(null); setManagement(null); setDashboardFinance(null); setDashboardDate(null); setDashboardHistoricalPack(null); setDashboardHistory([]); setRooms([]); setRoomTypes([]); setGuests([]); setReservations([]); setFrontDesk({ arrivals: [], departures: [], in_house: [] }); setBilling([]); setAuthMode('login');
  }

  if (checking) return <main className="auth-shell"><p className="muted">Checking local session…</p></main>;
  if (!user) return <AuthScreen mode={authMode} setMode={setAuthMode} onAuthenticated={authenticated} />;

  return <main className="shell"><header className="topbar"><div><div className="brand-lockup" aria-label="HighFly AI"><span className="brand-highfly">HighFly</span> <span className="brand-ai">AI</span></div><div><p className="eyebrow">LA SERENE HOTEL & RESORT</p><h1>Hotel Management System</h1></div></div><div className="user-actions"><div className="user-chip"><strong>{user.username}</strong><span>{user.role}</span></div><button className="logout-button" onClick={logout}>Log out</button></div></header><nav className="module-nav">{visibleModules.map(m => <button key={m} className={view === m ? 'active' : ''} onClick={() => setView(m)}>{m}</button>)}</nav>{error && <p className="error">{error}</p>}{view === 'Dashboard' && <><section className="welcome dashboard-welcome"><div><p className="muted">{dashboardDate && dashboard && dashboardDate !== dashboard.business_date ? 'Historical dashboard' : 'Operations dashboard'}</p><h2>{dashboardDate && dashboard && dashboardDate !== dashboard.business_date ? `Historical View · ${formatDashboardDate(dashboardDate)}` : dashboard ? `Business date · ${dashboard.business_date}` : 'Loading hotel data…'}</h2></div><div className="dashboard-date-controls"><label>Business date<input type="date" value={dashboardDate || dashboard?.business_date || ''} max={dashboard?.business_date || undefined} onChange={e => void selectDashboardDate(e.target.value)} disabled={!dashboard} /></label><label>Recent dates<select value={dashboardDate || dashboard?.business_date || ''} onChange={e => void selectDashboardDate(e.target.value)} disabled={!dashboard}><option value={dashboard?.business_date || ''}>{dashboard ? `${formatDashboardDate(dashboard.business_date)} · Open / Today` : 'Loading…'}</option>{dashboardHistory.filter(day => day.date !== dashboard?.business_date).reverse().map(day => <option key={day.date} value={day.date}>{formatDashboardDate(day.date)} · {day.closed ? 'Closed' : 'Open'}</option>)}</select></label>{dashboardDate && dashboard && dashboardDate !== dashboard.business_date && <button className="secondary-button" type="button" onClick={returnDashboardToToday}>Return to Today</button>}</div></section><DashboardView dashboard={dashboard} management={management} finance={dashboardFinance} history={dashboardHistory} rooms={rooms} roomTypes={roomTypes} selectedDate={dashboardDate} historicalPack={dashboardHistoricalPack} reservations={reservations} frontDesk={frontDesk} onSelectDate={selectDashboardDate} /></>}{view === 'Rooms' && <RoomsView user={user} rooms={rooms} setRooms={setRooms} roomTypes={roomTypes} onRefresh={refresh} />}{view === 'Guests' && <GuestsView user={user} guests={guests} setGuests={setGuests} reservations={reservations} onRefresh={refresh} />}{view === 'Reservations' && <ReservationsPMSView user={user} guests={guests} rooms={rooms} roomTypes={roomTypes} reservations={reservations} onRefresh={refresh} api={api} />}{view === 'Front Desk' && <FrontDeskPMSView user={user} data={frontDesk} rooms={rooms} reservations={reservations} guests={guests} roomTypes={roomTypes} onRefresh={refresh} api={api} />}{view === 'Housekeeping' && <HousekeepingView userRole={user.role} api={api} onRefresh={refresh} />}{view === 'Reports' && <ReportsView api={api} />}{view === 'Billing' && <BillingView userRole={user.role} summaries={billing} onRefresh={refresh} api={api} />}{view === 'Backup' && <BackupView api={api} />}<footer className="app-footer" aria-label="Application developer credit"><strong><span className="brand-highfly">HighFly</span> <span className="brand-ai">AI</span></strong></footer></main>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
