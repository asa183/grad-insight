import { google } from 'googleapis';
import fs from 'fs-extra';
import path from 'path';

const SHEET_ID = process.env.SHEET_ID || '';
const SHEET_NAME = process.env.SHEET_NAME || 'Examples';
const DRIVE_FOLDER_ID = process.env.DRIVE_FOLDER_ID || '';
const GOOGLE_CREDENTIALS_JSON = process.env.GOOGLE_CREDENTIALS_JSON;
const OAUTH_CLIENT_ID = process.env.OAUTH_CLIENT_ID || '';
const OAUTH_CLIENT_SECRET = process.env.OAUTH_CLIENT_SECRET || '';
const OAUTH_REFRESH_TOKEN = process.env.OAUTH_REFRESH_TOKEN || '';

if (!SHEET_ID || !DRIVE_FOLDER_ID) {
  console.error('SHEET_ID and DRIVE_FOLDER_ID are required');
  process.exit(2);
}

async function getAuth() {
  if (OAUTH_CLIENT_ID && OAUTH_CLIENT_SECRET && OAUTH_REFRESH_TOKEN) {
    const oauth2 = new google.auth.OAuth2({
      clientId: OAUTH_CLIENT_ID,
      clientSecret: OAUTH_CLIENT_SECRET,
      redirectUri: 'urn:ietf:wg:oauth:2.0:oob',
    } as any);
    oauth2.setCredentials({ refresh_token: OAUTH_REFRESH_TOKEN });
    return oauth2 as any;
  }
  if (GOOGLE_CREDENTIALS_JSON) {
    const creds = JSON.parse(GOOGLE_CREDENTIALS_JSON);
    const auth = new google.auth.GoogleAuth({ credentials: creds, scopes: ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive'] });
    return await auth.getClient();
  }
  return await google.auth.getClient({ scopes: ['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive'] });
}

async function ensureAnyoneReader(drive: any, fileId: string) {
  try { await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role: 'reader' }, supportsAllDrives: true }); } catch { /* ignore */ }
}

async function main() {
  const INPUT_DIR = process.env.INPUT_DIR || 'cleaned';
  const CAP_DIR = process.env.CAP_DIR || 'captures';
  const UPLOAD_SOURCE = (process.env.UPLOAD_SOURCE || 'auto').toLowerCase(); // 'auto' | 'cleaned' | 'captures'

  let source: 'cleaned'|'captures' = 'cleaned';
  let files: string[] = [];
  if (UPLOAD_SOURCE === 'captures') {
    source = 'captures';
    files = (await fs.readdir(CAP_DIR).catch(() => [] as string[])).filter(f => f.endsWith('.html'));
  } else {
    files = (await fs.readdir(INPUT_DIR).catch(() => [] as string[])).filter(f => /\.clean\.(html|txt)$/i.test(f));
    if (!files.length) {
      // fallback to captures artifact HTMLs
      source = 'captures';
      files = (await fs.readdir(CAP_DIR).catch(() => [] as string[])).filter(f => f.endsWith('.html'));
    }
  }
  if (!files.length) { console.warn(`no files to push (source=${source}). INPUT_DIR=${INPUT_DIR} CAP_DIR=${CAP_DIR}`); return; }

  const auth = await getAuth();
  const drive = google.drive({ version: 'v3', auth: auth as any });
  const sheets = google.sheets({ version: 'v4', auth: auth as any });

  const range = `${SHEET_NAME}!A:O`;
  const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range });
  const rows = (resp.data.values || []) as string[][];
  const header = rows[0] || [];
  const detectUrlCol = (): number => {
    const ucol = process.env.URL_COL ? Number(process.env.URL_COL) : NaN;
    if (!Number.isNaN(ucol) && ucol >= 0) return ucol;
    const candidates = ['出典url','url','研究科url'];
    for (let i = 0; i < header.length; i++) {
      const s = String(header[i] || '').trim().toLowerCase();
      if (candidates.includes(s)) return i;
    }
    return 2; // fallback: C列
  };
  const urlCol = detectUrlCol();
  const urlToRow = new Map<string, number>();
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i] || [];
    const url = (r[urlCol] || '').toString().trim();
    if (url) urlToRow.set(url, i);
  }

  const updates: { range: string, values: any[][] }[] = [];
  let uploaded = 0;
  for (const f of files) {
    const base = source === 'captures'
      ? f.replace(/\.html$/i,'')
      : f.replace(/\.clean\.(html|txt)$/i,'');
    // Resolve meta path with robust CAP_DIR fallbacks
    const metaRel = `${base}.meta.json`;
    const candDirs = [
      CAP_DIR,
      path.resolve(process.cwd(), 'captures'),
      path.resolve(process.cwd(), '../captures'),
      path.resolve(process.cwd(), '../../captures'),
    ].filter(Boolean);
    let metaPath = '';
    for (const d of candDirs) {
      const p = path.join(d, metaRel);
      try { if (await fs.pathExists(p)) { metaPath = p; break; } } catch {}
    }
    let url = '';
    let rowIndex: number | undefined = undefined;
    if (metaPath) {
      try {
        const meta = await fs.readJson(metaPath);
        url = String((meta as any).url || '');
        const ri = (meta as any).row_index;
        if (typeof ri === 'number' && isFinite(ri)) rowIndex = ri - 1; // 1-based in meta
      } catch {}
    }
    if (rowIndex == null) {
      if (!url) {
        console.warn(`skip ${f}: meta not found or url empty (tried: ${candDirs.join(' | ')})`);
        continue;
      }
      rowIndex = urlToRow.get(url);
    }
    if (rowIndex == null) { console.warn(`skip ${f}: url not found in sheet`); continue; }

    // upload to Drive
    const filePath = source === 'captures' ? path.join(CAP_DIR, f) : path.join(INPUT_DIR, f);
    const isTxt = source !== 'captures' && /\.clean\.txt$/i.test(f);
    const name = (source === 'captures'
      ? f.replace(/\.html$/i, '') + '.html'
      : f.replace(/\.clean\.(html|txt)$/i, '') + (isTxt ? '.txt' : '.html'));
    const mime = isTxt ? 'text/plain' : 'text/html';
    const created = await drive.files.create({
      requestBody: { name, parents: [DRIVE_FOLDER_ID], mimeType: mime },
      media: { mimeType: mime, body: await fs.readFile(filePath, 'utf8') },
      fields: 'id, webViewLink', supportsAllDrives: true,
    } as any);
    const id = created.data.id as string;
    await ensureAnyoneReader(drive, id);
    const meta = await drive.files.get({ fileId: id, fields: 'id, webViewLink', supportsAllDrives: true });
    const link = meta.data.webViewLink || `https://drive.google.com/file/d/${id}/view`;

    const r1 = rowIndex + 1;
    // 教授版要件: K列(HTML) 更新 + J列をFALSEへ
    updates.push({ range: `${SHEET_NAME}!K${r1}`, values: [[link]] });
    updates.push({ range: `${SHEET_NAME}!J${r1}`, values: [[false]] });
    uploaded++;
  }

  if (updates.length) {
    await sheets.spreadsheets.values.batchUpdate({ spreadsheetId: SHEET_ID, requestBody: { valueInputOption: 'USER_ENTERED', data: updates } });
  }
  console.log(`Push summary: uploaded=${uploaded}`);
  // アップロード 0 件でも非エラー終了（セル更新なし）
}

main().catch(err => { console.error(err); process.exit(1); });
