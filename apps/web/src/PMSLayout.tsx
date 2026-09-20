import React from 'react';

export type PMSView = 'Dashboard' | 'Rooms' | 'Guests' | 'Reservations' | 'Front Desk' | 'Housekeeping' | 'Reports' | 'Billing' | 'Backup';

type PMSLayoutProps = {
  user: { username: string; role: string };
  view: PMSView;
  modules: PMSView[];
  onViewChange: (view: PMSView) => void;
  onLogout: () => void;
  error?: string;
  children: React.ReactNode;
};

const moduleMeta: Record<PMSView, { label: string; short: string; group: 'Operations' | 'Finance & control' }> = {
  Dashboard: { label: 'Dashboard', short: 'D', group: 'Operations' },
  Rooms: { label: 'Rooms', short: 'R', group: 'Operations' },
  Guests: { label: 'Guests', short: 'G', group: 'Operations' },
  Reservations: { label: 'Reservations', short: 'RS', group: 'Operations' },
  'Front Desk': { label: 'Front Desk', short: 'FD', group: 'Operations' },
  Housekeeping: { label: 'Housekeeping', short: 'HK', group: 'Operations' },
  Reports: { label: 'Reports', short: 'RP', group: 'Finance & control' },
  Billing: { label: 'Billing', short: 'BL', group: 'Finance & control' },
  Backup: { label: 'Backup', short: 'BK', group: 'Finance & control' },
};

export default function PMSLayout({ user, view, modules, onViewChange, onLogout, error, children }: PMSLayoutProps) {
  const operations = modules.filter(item => moduleMeta[item].group === 'Operations');
  const controls = modules.filter(item => moduleMeta[item].group === 'Finance & control');

  const nav = (items: PMSView[]) => items.map(item => (
    <button
      type="button"
      key={item}
      className={`pms-nav-item ${view === item ? 'active' : ''}`}
      onClick={() => onViewChange(item)}
      aria-current={view === item ? 'page' : undefined}
    >
      <span className="pms-nav-icon" aria-hidden="true">{moduleMeta[item].short}</span>
      <span>{moduleMeta[item].label}</span>
    </button>
  ));

  return (
    <div className="pms-app">
      <aside className="pms-sidebar">
        <div className="pms-brand">
          <div className="pms-brand-mark">LS</div>
          <div><strong>LA SERENE</strong><span>Hotel Management</span></div>
        </div>
        <div className="pms-hotel-context">
          <span>PROPERTY</span>
          <strong>La Serene Hotel & Resort</strong>
          <small>Operations workspace</small>
        </div>
        <nav className="pms-nav" aria-label="Hotel modules">
          <div className="pms-nav-section">Operations</div>
          {nav(operations)}
          {controls.length > 0 && <div className="pms-nav-section">Finance & control</div>}
          {nav(controls)}
        </nav>
        <div className="pms-sidebar-footer">
          <span className="pms-live-dot" /> System online
        </div>
      </aside>

      <div className="pms-main">
        <header className="pms-topbar">
          <div className="pms-breadcrumb"><span>La Serene</span><b>/</b><strong>{moduleMeta[view].label}</strong></div>
          <div className="pms-topbar-actions">
            <div className="pms-user">
              <span className="pms-avatar">{user.username.slice(0, 1).toUpperCase()}</span>
              <div><strong>{user.username}</strong><small>{user.role}</small></div>
            </div>
            <button type="button" className="pms-logout" onClick={onLogout}>Log out</button>
          </div>
        </header>
        {error && <div className="pms-global-error" role="alert">{error}</div>}
        <main className="pms-content">{children}</main>
      </div>
    </div>
  );
}
