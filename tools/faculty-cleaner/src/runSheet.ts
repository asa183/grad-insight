import { google } from 'googleapis';
import { cleanFacultyHtml } from './index.js';
import { createHash } from 'crypto';

const SHEET_ID = process.env.SHEET_ID || '';
const SHEET_NAME = process.env.SHEET_NAME || 'Examples';
const DRIVE_FOLDER_ID = process.env.DRIVE_FOLDER_ID || '';
const CONCURRENCY = Number(process.env.CONCURRENCY || '3');
const GOOGLE_CREDENTIALS_JSON = process.env.GOOGLE_CREDENTIALS_JSON;

if (!SHEET_ID) { console.error('SHEET_ID is required'); process.exit(2); }
if (!DRIVE_FOLDER_ID) { console.error('DRIVE_FOLDER_ID is required'); process.exit(2); }

type Row = { rowIndex: number; values: string[] };

const COL = { URL: 2, FLAG: 9, OUT_URL: 10, FILE_ID: 11, STATUS: 12, MESSAGE: 13, UPDATED_AT: 14 };

function nowIso() { return new Date().toISOString(); }
function delay(ms: number) { return new Promise(res => setTimeout(res, ms)); }
function slugifyUrl(u: string): string {
  try {
    const url = new URL(u);
    const path = url.pathname.replace(/\/+$/, '').replace(/^\//, '').replace(/\//g, '-');
    const host = url.hostname.replace(/^www\./, '');
    const base = `${host}-${path || 'index'}`.toLowerCase();
    const clean = base.replace(/[^a-z0-9._-]+/g, '-').replace(/-+/g, '-').replace(/^-|-$|\.+$/g, '');
    const hash = createHash('sha1').update(u).digest('hex').slice(0, 8);
    return `${clean}-${hash}.html`;
  } catch {
    const hash = createHash('sha1').update(u).digest('hex').slice(0, 8);
    return `page-${hash}.html`;
  }
}

async function fetchHtml(url: string, timeoutMs = 15000): Promise<string> {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { redirect: 'follow', headers: { 'user-agent': 'grad-insight-bot/1.0' }, signal: ctrl.signal } as any);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.text();
  } finally { clearTimeout(to); }
}

async function getAuth() {
  if (GOOGLE_CREDENTIALS_JSON) {
    const creds = JSON.parse(GOOGLE_CREDENTIALS_JSON);
    const auth = new google.auth.GoogleAuth({ credentials: creds, scopes: ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive'] });
    return await auth.getClient();
  }
  return await google.auth.getClient({ scopes: ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive'] });
}

async function ensureAnyoneReader(drive: any, fileId: string) {
  try {
    await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role: 'reader' }, supportsAllDrives: true });
  } catch (e: any) {
    if (e?.response?.status !== 400) console.warn('permission warn:', e?.message || e);
  }
}

async function uploadOrUpdateHtml(drive: any, name: string, html: string, existingId?: string): Promise<{ id: string, webViewLink?: string }>
{
  if (existingId) {
    await drive.files.update({ fileId: existingId, media: { mimeType: 'text/html', body: html }, supportsAllDrives: true });
    const meta = await drive.files.get({ fileId: existingId, fields: 'id, webViewLink', supportsAllDrives: true });
    return { id: meta.data.id as string, webViewLink: meta.data.webViewLink };
  }
  const created = await drive.files.create({ requestBody: { name, parents: [DRIVE_FOLDER_ID], mimeType: 'text/html' }, media: { mimeType: 'text/html', body: html }, fields: 'id, webViewLink', supportsAllDrives: true });
  const id = created.data.id as string; await ensureAnyoneReader(drive, id);
  const meta = await drive.files.get({ fileId: id, fields: 'id, webViewLink', supportsAllDrives: true });
  return { id: meta.data.id as string, webViewLink: meta.data.webViewLink };
}

async function main() {
  const auth = await getAuth();
  const sheets = google.sheets({ version: 'v4', auth: auth as any });
  const drive = google.drive({ version: 'v3', auth: auth as any });

  const range = `${SHEET_NAME}!A:O`;
  const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range });
  const rows = (resp.data.values || []) as string[][];
  if (!rows.length) { console.log('No data.'); return; }

  const targets: Row[] = [];
  for (let i = 1; i < rows.length; i++) {
    const values = rows[i] || [];
    const url = values[COL.URL] || '';
    const flag = (values[COL.FLAG] || '').toString().toLowerCase();
    if (!url) continue; if (flag !== 'true') continue;
    targets.push({ rowIndex: i, values });
  }

  console.log(`Targets: ${targets.length}`);
  const updates: { range: string, values: any[][] }[] = [];

  let idx = 0;
  async function worker(wid: number) {
    while (true) {
      const t = targets[idx++]; if (!t) return;
      const r1 = t.rowIndex + 1; const url = t.values[COL.URL]; const existingId = t.values[COL.FILE_ID]; const name = slugifyUrl(url);
      try {
        console.log(`[${wid}] row ${r1} fetch ${url}`);
        const raw = await fetchHtml(url);
        const cleaned = cleanFacultyHtml(raw, url);
        const up = await uploadOrUpdateHtml(drive, name, cleaned, existingId);
        const k = up.webViewLink || `https://drive.google.com/file/d/${up.id}/view`;
        updates.push({ range: `${SHEET_NAME}!K${r1}:O${r1}`, values: [[k, up.id, 'success', '', nowIso()]] });
      } catch (e: any) {
        console.error(`[${wid}] error row ${r1}:`, e?.message || e);
        updates.push({ range: `${SHEET_NAME}!M${r1}:O${r1}`, values: [['error', (e?.message || `${e}`).toString().slice(0,500), nowIso()]] });
      }
      await delay(250);
    }
  }

  const workers = Array.from({ length: Math.max(1, CONCURRENCY) }, (_, i) => worker(i+1));
  await Promise.all(workers);

  if (updates.length) {
    const data = updates.map(u => ({ range: u.range, values: u.values }));
    await sheets.spreadsheets.values.batchUpdate({ spreadsheetId: SHEET_ID, requestBody: { valueInputOption: 'RAW', data } });
    console.log(`Updated ${updates.length} row segments.`);
  } else {
    console.log('No updates to write.');
  }
}

main().catch(err => { console.error(err); process.exit(1); });
