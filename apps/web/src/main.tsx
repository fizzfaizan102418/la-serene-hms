import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

const modules = ['Dashboard', 'Rooms', 'Guests', 'Reservations', 'Front Desk', 'Billing'];

function App() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">LA SERENE HOTEL</p>
        <h1>Hotel Management System</h1>
        <p className="muted">
          Phase 1 foundation is ready. Operations will be built around one source of truth for rooms,
          guests, reservations and billing.
        </p>
        <div className="status"><span /> Offline-first foundation</div>
      </section>
      <section className="grid">
        {modules.map((item) => (
          <article key={item}><h2>{item}</h2><p>Module scaffold</p></article>
        ))}
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><App /></React.StrictMode>,
);
