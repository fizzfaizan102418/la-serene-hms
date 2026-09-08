import React, { useEffect, useState } from 'react';

type BackupApi = <T>(path: string, options?: RequestInit) => Promise<T>;
type BackupInfo = { filename: string; size_bytes: number; created_at: string };
type BackupList = { database: string; backups: BackupInfo[] };

const formatSize = (bytes: number) => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export default function BackupView({ api }: { api: BackupApi }) {
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoring, setRestoring] = useState(false);

  async function refresh() {
    setLoading(true); setError('');
    try {
      const result = await api<BackupList>('/api/backup');
      setBackups(result.backups);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to load backups');
    } finally { setLoading(false); }
  }

  useEffect(() => { void refresh(); }, []);

  async function createBackup() {
    setError(''); setMessage(''); setLoading(true);
    try {
      const result = await api<BackupInfo>('/api/backup', { method: 'POST' });
      setMessage(`Backup created: ${result.filename}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to create backup');
      setLoading(false);
    }
  }

  async function downloadBackup(filename: string) {
    setError('');
    try {
      const token = localStorage.getItem('la_serene_access_token');
      const response = await fetch(`/api/backup/${encodeURIComponent(filename)}/download`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? `Download failed (${response.status})`);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to download backup'); }
  }

  async function restore() {
    if (!restoreFile) { setError('Choose a SQLite backup file first.'); return; }
    if (!window.confirm('Restore this database? The current database will be backed up automatically before replacement.')) return;
    setRestoring(true); setError(''); setMessage('');
    try {
      const token = localStorage.getItem('la_serene_access_token');
      const body = new FormData(); body.append('file', restoreFile);
      const response = await fetch('/api/backup/restore', { method: 'POST', headers: token ? { Authorization: `Bearer ${token}` } : {}, body });
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail ?? `Restore failed (${response.status})`);
      const result = await response.json();
      setRestoreFile(null);
      setMessage(`Database restored. Safety backup: ${result.safety_backup.filename}. Reload the app to reconnect to the restored database.`);
      await refresh();
    } catch (err) { setError(err instanceof Error ? err.message : 'Unable to restore database'); }
    finally { setRestoring(false); }
  }

  return <section className="page">
    <div className="page-heading"><div><p className="muted">Local data protection</p><h2>Backup & Restore</h2></div><span className="room-count">Admin only</span></div>
    {message && <p className="notice">{message}</p>}
    {error && <p className="error">{error}</p>}
    <div className="backup-grid">
      <section className="panel">
        <div className="panel-head"><h2>Create backup</h2></div>
        <p className="muted">Creates a validated SQLite snapshot of the current hotel database without changing operational data.</p>
        <button className="primary-button" onClick={createBackup} disabled={loading}>{loading ? 'Creating…' : 'Create database backup'}</button>
      </section>
      <section className="panel">
        <div className="panel-head"><h2>Restore database</h2></div>
        <p className="muted">Only valid SQLite files containing the required PMS tables are accepted. The current database is backed up before replacement.</p>
        <input type="file" accept=".sqlite3,.db,.sqlite" onChange={e => setRestoreFile(e.target.files?.[0] ?? null)} />
        <div className="backup-restore-actions"><span>{restoreFile?.name ?? 'No file selected'}</span><button className="primary-button" onClick={restore} disabled={!restoreFile || restoring}>{restoring ? 'Restoring…' : 'Restore selected backup'}</button></div>
      </section>
    </div>
    <section className="panel backup-history">
      <div className="panel-head"><h2>Available backups</h2><button className="secondary-button" onClick={refresh} disabled={loading}>{loading ? 'Refreshing…' : 'Refresh'}</button></div>
      {backups.length ? <div className="backup-list">{backups.map(backup => <article key={backup.filename}><div><strong>{backup.filename}</strong><span>{formatSize(backup.size_bytes)} · {new Date(backup.created_at).toLocaleString()}</span></div><button className="secondary-button small-button" onClick={() => downloadBackup(backup.filename)}>Download</button></article>)}</div> : <p className="muted">No backups have been created yet.</p>}
    </section>
  </section>;
}
