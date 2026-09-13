import React, { useEffect, useMemo, useState } from 'react';

type Api = <T>(path: string, options?: RequestInit) => Promise<T>;
type Expense = { id: number; expense_no: string; expense_date: string; category: string; description: string; amount: number; payment_method: string; paid_to?: string | null; reference?: string | null; department: string; notes?: string | null; created_by?: number | null; status: string };
type Summary = { from_date: string; to_date: string; total: number; by_category: { category: string; amount: number }[]; by_department: { department: string; amount: number }[]; by_payment_method: { payment_method: string; amount: number }[]; daily: { date: string; amount: number }[] };
type Meta = { categories: string[]; departments: string[]; payment_methods: string[] };
type Report = { revenue: { net: number }; };

const today = () => new Date().toISOString().slice(0, 10);
const money = (value: number) => Number(value || 0).toFixed(2);

export default function ExpensesView({ api }: { api: Api }) {
  const [meta, setMeta] = useState<Meta>({ categories: [], departments: [], payment_methods: [] });
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [revenue, setRevenue] = useState(0);
  const [fromDate, setFromDate] = useState(today());
  const [toDate, setToDate] = useState(today());
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ expense_date: today(), category: '', description: '', amount: '', payment_method: 'Cash', paid_to: '', reference: '', department: 'Hotel', notes: '' });

  async function load() {
    setMessage('');
    try {
      const [metaData, rows, totals, report] = await Promise.all([
        api<Meta>('/api/expenses/meta'),
        api<Expense[]>(`/api/expenses?from_date=${fromDate}&to_date=${toDate}`),
        api<Summary>(`/api/expenses/summary?from_date=${fromDate}&to_date=${toDate}`),
        api<Report>(`/api/reports/summary?from_date=${fromDate}&to_date=${toDate}`),
      ]);
      setMeta(metaData); setExpenses(rows); setSummary(totals); setRevenue(Number(report.revenue.net || 0));
      if (!form.category && metaData.categories.length) setForm(current => ({ ...current, category: metaData.categories[0] }));
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to load expenses'); }
  }

  useEffect(() => { void load(); }, []);

  async function create(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setMessage('');
    try {
      await api('/api/expenses', { method: 'POST', body: JSON.stringify({ ...form, amount: Number(form.amount) }) });
      setForm(current => ({ ...current, description: '', amount: '', paid_to: '', reference: '', notes: '' }));
      setMessage('Expense recorded successfully.'); await load();
    } catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to record expense'); }
    finally { setBusy(false); }
  }

  async function voidExpense(id: number) {
    if (!window.confirm('Void this expense? It will remain in the audit history but will no longer count in totals.')) return;
    try { await api(`/api/expenses/${id}/void`, { method: 'PATCH' }); setMessage('Expense voided.'); await load(); }
    catch (err) { setMessage(err instanceof Error ? err.message : 'Unable to void expense'); }
  }

  const netOperating = useMemo(() => revenue - Number(summary?.total || 0), [revenue, summary]);
  return <section className="page">
    <div className="page-heading"><div><p className="muted">Daily hotel operating costs</p><h2>Expenses</h2></div><div className="desk-actions"><button className="secondary-button" onClick={() => void load()}>Refresh</button></div></div>
    {message && <p className="notice">{message}</p>}

    <section className="report-stat-grid">
      <article className="stat"><span>Total revenue</span><strong>{money(revenue)}</strong></article>
      <article className="stat"><span>Total expenses</span><strong>{money(Number(summary?.total || 0))}</strong></article>
      <article className="stat"><span>Net operating result</span><strong>{money(netOperating)}</strong></article>
      <article className="stat"><span>Expense records</span><strong>{expenses.length}</strong></article>
    </section>

    <div className="panel report-filters"><label>From<input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} /></label><label>To<input type="date" value={toDate} onChange={e => setToDate(e.target.value)} /></label><button className="primary-button" onClick={() => void load()}>Run expense report</button></div>

    <div className="content-layout">
      <form className="panel form-panel" onSubmit={create}>
        <div className="panel-head"><h2>Record daily expense</h2><span>Admin / Reception</span></div>
        <label>Date<input type="date" value={form.expense_date} onChange={e => setForm({ ...form, expense_date: e.target.value })} required /></label>
        <label>Category<select value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} required>{meta.categories.map(item => <option key={item}>{item}</option>)}</select></label>
        <label>Description<input value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} placeholder="e.g. Monthly electricity bill" required /></label>
        <label>Amount<input type="number" min="0.01" step="0.01" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} required /></label>
        <label>Payment method<select value={form.payment_method} onChange={e => setForm({ ...form, payment_method: e.target.value })}>{meta.payment_methods.map(item => <option key={item}>{item}</option>)}</select></label>
        <label>Paid to / Supplier<input value={form.paid_to} onChange={e => setForm({ ...form, paid_to: e.target.value })} placeholder="Supplier / utility / person" /></label>
        <label>Reference / Invoice #<input value={form.reference} onChange={e => setForm({ ...form, reference: e.target.value })} /></label>
        <label>Department<select value={form.department} onChange={e => setForm({ ...form, department: e.target.value })}>{meta.departments.map(item => <option key={item}>{item}</option>)}</select></label>
        <label>Notes<textarea rows={3} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
        <button className="primary-button" disabled={busy || !form.category}>{busy ? 'Saving…' : 'Record expense'}</button>
      </form>

      <section className="panel">
        <div className="panel-head"><h2>Expense ledger</h2><span>{expenses.length} records</span></div>
        {expenses.length ? <div className="guest-list">{expenses.map(item => <article key={item.id}><div><strong>{item.expense_no} · {item.category}</strong><span>{item.description} · {money(item.amount)}</span><small>{item.expense_date} · {item.department} · {item.payment_method}{item.paid_to ? ` · ${item.paid_to}` : ''}</small></div>{item.status === 'posted' && <button className="secondary-button" type="button" onClick={() => void voidExpense(item.id)}>Void</button>}</article>)}</div> : <p className="muted">No expenses recorded for this period.</p>}
      </section>
    </div>

    {summary && <div className="report-grid">
      <section className="panel"><div className="panel-head"><h2>By category</h2></div>{summary.by_category.length ? <div className="report-list">{summary.by_category.map(row => <div key={row.category}><span>{row.category}</span><strong>{money(row.amount)}</strong></div>)}</div> : <p className="muted">No category totals.</p>}</section>
      <section className="panel"><div className="panel-head"><h2>By department</h2></div>{summary.by_department.length ? <div className="report-list">{summary.by_department.map(row => <div key={row.department}><span>{row.department}</span><strong>{money(row.amount)}</strong></div>)}</div> : <p className="muted">No department totals.</p>}</section>
      <section className="panel"><div className="panel-head"><h2>Cash / bank</h2></div>{summary.by_payment_method.length ? <div className="report-list">{summary.by_payment_method.map(row => <div key={row.payment_method}><span>{row.payment_method}</span><strong>{money(row.amount)}</strong></div>)}</div> : <p className="muted">No payment totals.</p>}</section>
      <section className="panel"><div className="panel-head"><h2>Daily trend</h2></div>{summary.daily.length ? <div className="report-list">{summary.daily.map(row => <div key={row.date}><span>{row.date}</span><strong>{money(row.amount)}</strong></div>)}</div> : <p className="muted">No daily expense activity.</p>}</section>
    </div>}
  </section>;
}
