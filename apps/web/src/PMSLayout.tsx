import React from 'react';

export type PMSView = 'Dashboard' | 'Rooms' | 'Guests' | 'Reservations' | 'Front Desk' | 'Housekeeping' | 'Reports' | 'Billing' | 'Backup';

type Props = {
  user: { username: string; role: string };
  view: PMSView;
  modules: PMSView[];
  onViewChange: (view: PMSView) => void;
  onLogout: () => void;
  error?: string;
  children: React.ReactNode;
};

const meta: Record<PMSView, { label: string; group: 'Daily work' | 'Management' }> = {
  Dashboard: { label: 'Home', group: 'Daily work' },
  Rooms: { label: 'Rooms', group: 'Daily work' },
  Guests: { label: 'Guests', group: 'Daily work' },
  Reservations: { label: 'Reservations', group: 'Daily work' },
  'Front Desk': { label: 'Front Desk', group: 'Daily work' },
  Housekeeping: { label: 'Housekeeping', group: 'Daily work' },
  Reports: { label: 'Reports', group: 'Management' },
  Billing: { label: 'Billing', group: 'Management' },
  Backup: { label: 'Backup', group: 'Management' },
};

const icon: Record<PMSView, string> = {
  Dashboard: '⌂', Rooms: '▦', Guests: '◉', Reservations: '▤', 'Front Desk': '▣',
  Housekeeping: '✓', Reports: '▥', Billing: '₨', Backup: '↥',
};

export default function PMSLayout({ user, view, modules, onViewChange, onLogout, error, children }: Props) {
  const daily = modules.filter(v => meta[v].group === 'Daily work');
  const management = modules.filter(v => meta[v].group === 'Management');

  const renderNav = (items: PMSView[]) => items.map(item => (
    <button key={item} type="button" className={`simple-nav-item ${view === item ? 'active' : ''}`} onClick={() => onViewChange(item)} aria-current={view === item ? 'page' : undefined}>
      <span className="simple-nav-icon" aria-hidden="true">{icon[item]}</span><span>{meta[item].label}</span>
    </button>
  ));

  return <div className="simple-pms">
    <aside className="simple-sidebar">
      <div className="simple-brand"><div className="simple-brand-mark">LS</div><div><strong>LA SERENE</strong><small>Hotel Management</small></div></div>
      <div className="simple-property"><small>PROPERTY</small><strong>La Serene Hotel & Resort</strong><span>Business date managed in Home</span></div>
      <nav aria-label="Hotel modules">
        <div className="simple-nav-label">DAILY WORK</div>{renderNav(daily)}
        {management.length > 0 && <><div className="simple-nav-label">MANAGEMENT</div>{renderNav(management)}</>}
      </nav>
      <div className="simple-sidebar-bottom"><span className="simple-online-dot" /> System online</div>
    </aside>
    <div className="simple-main">
      <header className="simple-topbar">
        <div><div className="simple-page-kicker">LA SERENE HOTEL</div><h1>{meta[view].label}</h1></div>
        <div className="simple-account"><div className="simple-avatar">{user.username.slice(0,1).toUpperCase()}</div><div><strong>{user.username}</strong><small>{user.role}</small></div><button type="button" onClick={onLogout}>Log out</button></div>
      </header>
      {error && <div className="simple-error" role="alert">{error}</div>}
      <main className="simple-content">{children}</main>
    </div>
  </div>;
}
