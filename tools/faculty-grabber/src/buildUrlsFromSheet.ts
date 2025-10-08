import { google } from 'googleapis';
import fs from 'fs-extra';
import path from 'path';

const SHEET_ID = process.env.SHEET_ID || '';
const SHEET_NAME = process.env.SHEET_NAME || 'Examples';
const GOOGLE_CREDENTIALS_JSON = process.env.GOOGLE_CREDENTIALS_JSON || '';

function toBool(v: any): boolean {
  const s = String(v ?? '').trim().toLowerCase();
  return ['true','1','yes','y','有効','ok'].includes(s);
}

function detectSite(url: string, site?: string): string {
  if (site) return site.toLowerCase();
  if (/let\.hokudai\.ac\.jp\/research\/staff-g/.test(url)) return 'let';
  if (/agr\.hokudai\.ac\.jp\/r\/faculty/.test(url)) return 'agr';
  if (/edu\.hokudai\.ac\.jp\/graduate_school\/department\/academic/.test(url)) return 'edu';
  if (/faculty-member/.test(url)) return 'fish';
  return 'other';
}

async function main() {
  if (!SHEET_ID) { console.error('SHEET_ID required'); process.exit(2); }
  if (!GOOGLE_CREDENTIALS_JSON) { console.error('GOOGLE_CREDENTIALS_JSON required'); process.exit(2); }
  const creds = JSON.parse(GOOGLE_CREDENTIALS_JSON);
  const auth = new google.auth.GoogleAuth({ credentials: creds, scopes: ['https://www.googleapis.com/auth/spreadsheets.readonly'] });
  const client = await auth.getClient();
  const sheets = google.sheets({ version: 'v4', auth: client as any });
  const range = `${SHEET_NAME}!A:O`;
  const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range });
  const rows = (resp.data.values || []) as string[][];
  if (!rows.length) { console.error('Sheet empty'); process.exit(1); }
  const header = rows[0];
  const getIdx = (names: string[]) => names.map(n => header.findIndex(h => String(h).trim().toLowerCase() === n)).find(i => i>=0) ?? -1;
  const urlIdx = (() => {
    const cands = ['url','出典url','研究科url'];
    for (const h of header) {
      const s = String(h).trim().toLowerCase();
      if (cands.includes(s)) return header.indexOf(h);
    }
    // fallback: C列（2）
    return 2;
  })();
  const capIdx = (() => {
    const cands = ['capture_html','html','有効'];
    for (const h of header) {
      const s = String(h).trim().toLowerCase();
      if (cands.includes(s)) return header.indexOf(h);
    }
    return -1;
  })();
  const siteIdx = header.findIndex(h => String(h).trim().toLowerCase() === 'site');
  const uniIdx = header.findIndex(h => String(h).trim().toLowerCase() === '大学名');
  const gradIdx = header.findIndex(h => String(h).trim().toLowerCase() === '研究科');

  const outLines: string[] = [];
  outLines.push('url,capture_html,site,university,graduate_school');
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i] || [];
    const url = (r[urlIdx] || '').toString().trim();
    if (!url) continue;
    const capture = capIdx >= 0 ? toBool(r[capIdx]) : true; // cap列が無ければTRUE扱い
    const site = detectSite(url, siteIdx>=0 ? r[siteIdx] : undefined);
    const uni = uniIdx>=0 ? (r[uniIdx] || '') : '';
    const grad = gradIdx>=0 ? (r[gradIdx] || '') : '';
    outLines.push([url, capture ? 'TRUE' : 'FALSE', site, uni, grad].map(v => String(v).replace(/,/g,' ')).join(','));
  }
  const urlsPath = path.resolve(process.cwd(), 'urls.csv');
  await fs.writeFile(urlsPath, outLines.join('\n'), 'utf8');
  // Required URLs present?
  const text = outLines.join('\n');
  const reqs = [
    'https://www.let.hokudai.ac.jp/research/staff-g',
    'https://www.agr.hokudai.ac.jp/r/faculty',
    'https://www.edu.hokudai.ac.jp/graduate_school/department/academic/'
  ];
  const missing = reqs.filter(u => !text.includes(u));
  if (missing.length) { console.error('Required URLs missing in Examples:', missing.join(', ')); process.exit(1); }
  console.log(`Built urls.csv with ${outLines.length-1} rows at ${urlsPath}`);
}

main().catch(err => { console.error(err); process.exit(1); });
