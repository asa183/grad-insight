import { chromium, Page } from 'playwright';
import { google } from 'googleapis';
import fs from 'fs-extra';
import path from 'path';
import * as cheerio from 'cheerio';

const SHEET_ID = process.env.SHEET_ID || '';
const SHEET_NAME = process.env.SHEET_NAME || 'examples';
const DRIVE_FOLDER_ID = process.env.DRIVE_FOLDER_ID || '';
const OAUTH_CLIENT_ID = process.env.OAUTH_CLIENT_ID || '';
const OAUTH_CLIENT_SECRET = process.env.OAUTH_CLIENT_SECRET || '';
const OAUTH_REFRESH_TOKEN = process.env.OAUTH_REFRESH_TOKEN || '';
const METHOD_OVERRIDE = (process.env.METHOD || '').toLowerCase() as 'http'|'playwright'|'';

type SiteKind = 'let'|'agr'|'edu'|'fish'|'other';
type Method = 'http'|'playwright';

function detectSite(url: string): SiteKind {
  if (/let\.hokudai\.ac\.jp\/research\/staff-g/.test(url)) return 'let';
  if (/agr\.hokudai\.ac\.jp\/r\/faculty/.test(url)) return 'agr';
  if (/edu\.hokudai\.ac\.jp\/graduate_school\/department\/academic\//.test(url)) return 'edu';
  if (/www2\.fish\.hokudai\.ac\.jp\/faculty-member\//.test(url)) return 'fish';
  return 'other';
}

function chooseMethod(site: SiteKind, url?: string): Method {
  // 明示オーバーライドを最優先
  if (METHOD_OVERRIDE === 'http' || METHOD_OVERRIDE === 'playwright') return METHOD_OVERRIDE;
  // 既知の失敗/JS依存ドメインは強制 Playwright
  if (url) {
    try {
      const h = new URL(url).hostname.toLowerCase();
      if (
        /\.u-tokyo\.ac\.jp$/.test(h) ||
        /^ist\.hokudai\.ac\.jp$/.test(h) ||
        /^www\.ist\.hokudai\.ac\.jp$/.test(h) ||
        /^chemsys\.t\.u-tokyo\.ac\.jp$/.test(h) ||
        /^www\.f\.u-tokyo\.ac\.jp$/.test(h) || /^f\.u-tokyo\.ac\.jp$/.test(h)
      ) {
        return 'playwright';
      }
    } catch {}
  }
  // デフォルトも Playwright（fetch 失敗箇所を最優先で救う）
  return 'playwright';
}

function truthy(v: any): boolean {
  const s = String(v ?? '').trim().toLowerCase();
  return ['true','1','yes','y','有効','ok'].includes(s);
}

function absolutize(u: string, base: string): string {
  if (!u) return u;
  if (/^(mailto:|tel:|javascript:|#)/i.test(u)) return u;
  try { return new URL(u, base).toString(); } catch { return u; }
}

async function captureHttp(url: string) {
  const ctrl = new AbortController();
  const to = setTimeout(() => ctrl.abort(), 20000);
  const headers = {
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'accept-language': 'ja,en-US;q=0.9,en;q=0.8',
    'cache-control': 'no-cache',
    'pragma': 'no-cache',
    'upgrade-insecure-requests': '1',
  } as any;
  const res = await fetch(url as any, { redirect: 'follow', headers, signal: ctrl.signal } as any);
  if (!(res as any).ok) throw new Error(`HTTP ${res.status}`);
  const raw = await (res as any).text();
  clearTimeout(to);
  const $ = cheerio.load(raw);
  const node = $('main').first();
  let outer = node.length ? $.html(node.get(0)!) : $('body').length ? $.html($('body').get(0)!) : raw;
  // absolutize links
  const $$ = cheerio.load(outer);
  $$('a[href]').each((_, a) => { const href = $$(a).attr('href') || ''; $$(a).attr('href', absolutize(href, url)); });
  $$('img[src]').each((_, img) => { const src = $$(img).attr('src') || ''; $$(img).attr('src', absolutize(src, url)); });
  outer = $$.root().html() || outer;
  // metrics
  const anchors = $$('a').toArray().map((a: any) => $$(a).attr('href') || '');
  const staff = anchors.filter((h: string) => h.includes('/staff/')).length;
  const rlab = anchors.filter((h: string) => h.includes('/r/lab/')).length;
  const fish = anchors.filter((h: string) => h.includes('/faculty-member/')).length;
  const names = $$('.name, .m-name, dt.name').toArray().filter((el: any) => ($$(el).text() || '').trim().length >= 2).length;
  const textLen = $$.root().text().replace(/\s+/g, ' ').trim().length;
  return { html: outer, metrics: { staff, rlab, fish, names, textLen } };
}

async function capturePlaywright(url: string) {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    locale: 'ja-JP', timezoneId: 'Asia/Tokyo', viewport: { width: 1366, height: 900 }, javaScriptEnabled: true,
  });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    // @ts-ignore
    window.chrome = { runtime: {} };
    // @ts-ignore
    navigator.plugins = [1,2,3];
    // @ts-ignore
    navigator.languages = ['ja-JP', 'ja', 'en-US'];
  });
  await page.route('**/*', (route) => {
    const u = route.request().url();
    // 大きな動画のみ中断。フォント等は許可してUI崩れを防ぐ
    if (/\.(mp4|webm)$/i.test(u)) return route.abort();
    return route.continue();
  });

  let html = '';
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
    await page.waitForFunction(() => !!document.body, { timeout: 30000 });

    // 同意/クッキーバナー対応
    try {
      const texts = [
        '同意', '許可', '同意する', 'OK', 'Ok', 'オーケー', 'わかった', '閉じる', '閉',
        'Accept', 'I agree', 'Agree', 'Accept all', 'Accept All', 'Consent', 'Allow', 'Continue'
      ];
      for (const t of texts) {
        try { await page.getByRole('button', { name: new RegExp(t, 'i') }).first().click({ timeout: 1500 }); } catch {}
        try { await page.getByRole('link', { name: new RegExp(t, 'i') }).first().click({ timeout: 1500 }); } catch {}
      }
      await page.evaluate(() => {
        const match = (s: string) => /cookie|consent|同意|許可|ポップアップ|バナー|閉/.test(s);
        const nodes = Array.from(document.querySelectorAll<HTMLElement>('button, a, [role="button"]'));
        for (const el of nodes) {
          const label = (el.innerText || el.getAttribute('aria-label') || '').trim();
          if (label && match(label)) { try { el.click(); } catch {} }
        }
      });
      try { await page.waitForLoadState('networkidle', { timeout: 20000 }); } catch {}
    } catch {}
    // generic clicks
    await page.evaluate(() => {
      const sels = ['[aria-controls]','[aria-expanded="false"]','.accordion button','.tab a','.tab button','.more a','.more button'];
      sels.forEach(sel => document.querySelectorAll<HTMLElement>(sel).forEach(el => { try { el.click(); } catch {} }));
    });
    // scroll waves
    await page.evaluate(async () => {
      const sleep = (ms:number)=>new Promise(r=>setTimeout(r,ms));
      const getDoc = () => (document.scrollingElement || document.documentElement || document.body) as (HTMLElement | null);
      let last = 0;
      for (let i=0; i<10; i++) {
        const d = getDoc(); if (!d) break; const h = d.scrollHeight || 0;
        window.scrollTo(0, Math.max(0, h - (i*50)));
        await sleep(250 + Math.random()*200);
        const d2=getDoc(); const h2 = d2 ? d2.scrollHeight||0 : 0; if (h2===last) break; last=h2;
      }
    });
    try { await page.waitForLoadState('networkidle', { timeout: 30000 }); } catch {}
    await page.waitForTimeout(1000);
    // extract
    html = await page.evaluate(() => {
      const node = (document.querySelector('main') ?? document.body) as HTMLElement | null;
      return node ? node.outerHTML : '<body></body>';
    });
  } finally {
    await browser.close();
  }
  // metrics via cheerio
  const $ = cheerio.load(html);
  const anchors = $('a').toArray().map((a: any) => $(a).attr('href') || '');
  const staff = anchors.filter((h: string) => h.includes('/staff/')).length;
  const rlab = anchors.filter((h: string) => h.includes('/r/lab/')).length;
  const fish = anchors.filter((h: string) => h.includes('/faculty-member/')).length;
  const names = $('.name, .m-name, dt.name').toArray().filter((el: any) => ($(el).text() || '').trim().length >= 2).length;
  const textLen = $.root().text().replace(/\s+/g, ' ').trim().length;
  return { html, metrics: { staff, rlab, fish, names, textLen } };
}

function selfCheck(site: SiteKind, method: Method, metrics: any): boolean {
  // Require minimum visible text to avoid tag-only outputs
  const textOk = (metrics?.textLen ?? 0) > 50;
  if (!textOk) return false;
  if (site === 'edu' || site === 'let') {
    return (metrics.staff >= 1) || (metrics.names >= 1);
  }
  if (site === 'agr') {
    return (metrics.rlab >= 1);
  }
  if (site === 'fish') {
    return (metrics.fish >= 1);
  }
  return true; // other
}

function getOAuthClient() {
  if (!OAUTH_CLIENT_ID || !OAUTH_CLIENT_SECRET || !OAUTH_REFRESH_TOKEN) return null as any;
  const oauth2 = new google.auth.OAuth2({
    clientId: OAUTH_CLIENT_ID,
    clientSecret: OAUTH_CLIENT_SECRET,
    redirectUri: 'urn:ietf:wg:oauth:2.0:oob',
  } as any);
  oauth2.setCredentials({ refresh_token: OAUTH_REFRESH_TOKEN });
  return oauth2;
}

async function ensureLinkSharing(drive: any, fileId: string) {
  try { await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role: 'reader' }, supportsAllDrives: true }); } catch {}
}

function toSlug(u: URL) {
  const host = u.host.replace(/[:.]/g,'-');
  const parts = u.pathname.replace(/\/+/g,'/').split('/').filter(Boolean);
  const tail = parts.slice(-1)[0] || 'root';
  return `${host}-${tail}`;
}

function sanitizeName(s: string): string {
  // Driveのファイル名として不向きな記号を除去（日本語は保持）
  return (s || '')
    .replace(/[\\/:*?"<>|]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80);
}

function parseForcePw(): { urls: Set<string>, substr: string[] } {
  const urls = new Set<string>();
  const u = (process.env.FORCE_PW_URLS || '').trim();
  if (u) {
    for (const line of u.split(/\r?\n|,/)) { const s = line.trim(); if (s) urls.add(s); }
  }
  const substr = (process.env.FORCE_PW_SUBSTR || '').split(',').map(s => s.trim()).filter(Boolean);
  return { urls, substr };
}

async function main() {
  const ALLOW_PLAYWRIGHT = (process.env.ALLOW_PLAYWRIGHT ?? '1') !== '0';
  const force = parseForcePw();
  const ONLY_FAILED = (process.env.ONLY_FAILED ?? '0') === '1';
  if (!SHEET_ID || !DRIVE_FOLDER_ID) {
    console.error('SHEET_ID and DRIVE_FOLDER_ID are required');
    process.exit(2);
  }
  // OAuth を必須にして Sheets/Drive を初期化
  const oauth = getOAuthClient();
  if (!oauth) {
    console.error('OAuth credentials are required (OAUTH_CLIENT_ID/SECRET/REFRESH_TOKEN).');
    process.exit(2);
  }
  const sheets = google.sheets({ version: 'v4', auth: oauth as any });
  let drive = google.drive({ version: 'v3', auth: oauth as any });
  console.log(`Grab mode: METHOD_OVERRIDE=${METHOD_OVERRIDE || '(auto-playwright)'}`);
  try {
    const about = await drive.about.get({ fields: 'user' });
    const email = (about.data as any)?.user?.emailAddress || '(unknown)';
    console.log(`Using OAuth for Drive uploads as ${email}`);
  } catch (e:any) {
    console.error('OAuth initialization failed:', e?.message || e);
    process.exit(2);
  }

  // Resolve sheet name case-insensitively
  let sheetName = SHEET_NAME;
  try {
    const meta = await sheets.spreadsheets.get({ spreadsheetId: SHEET_ID });
    const titles = (meta.data.sheets || []).map(s => s.properties?.title || '').filter(Boolean);
    const found = titles.find(t => t.toLowerCase() === SHEET_NAME.toLowerCase());
    if (found) sheetName = found;
    else throw new Error(`Sheet tab '${SHEET_NAME}' not found. Available: ${titles.join(', ')}`);
  } catch (e:any) {
    console.error('Sheet metadata error:', e?.message || e);
    throw e;
  }

  // 共有ドライブ/マイドライブのログ（診断用）
  try {
    const folderMeta = await drive.files.get({ fileId: DRIVE_FOLDER_ID, fields: 'id,name,driveId', supportsAllDrives: true });
    const dId = (folderMeta.data as any).driveId;
    const dName = (folderMeta.data as any).name;
    console.log(`Drive folder preflight: name="${dName}" sharedDrive=${dId ? 'yes' : 'no'}`);
  } catch (e:any) {
    console.warn('Folder preflight failed:', e?.message || e);
  }

  const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `${sheetName}!A:K` });
  const rows = (resp.data.values || []) as string[][];
  if (!rows.length) { console.error('Sheet empty'); process.exit(1); }
  const header = rows[0];
  // Columns: A=大学名, B=研究科, J=有効, K=HTML は固定。
  // URL 列はデフォルト検出だが、`URL_COL` が指定されていればその列を使用（0-based）。
  const univCol = 0, gradCol = 1, enabledCol = 9, htmlCol = 10;
  const nameCol = (() => {
    const candidates = ['氏名','教員名','名前','name'];
    for (let i = 0; i < header.length; i++) {
      const s = String(header[i] || '').trim().toLowerCase();
      if (candidates.includes(s)) return i;
    }
    return 4; // fallback: E列
  })();
  const detectUrlCol = (): number => {
    // env override
    const ucol = process.env.URL_COL ? Number(process.env.URL_COL) : NaN;
    if (!Number.isNaN(ucol) && ucol >= 0) return ucol;
    const candidates = ['出典url','url','研究科url'];
    for (let i = 0; i < header.length; i++) {
      const s = String(header[i] || '').trim().toLowerCase();
      if (candidates.includes(s)) return i;
    }
    // フォールバック: 従来(研究科)の C 列
    return 2;
  };
  const urlCol = detectUrlCol();

  await fs.ensureDir('captures');
  let failedSet: Set<string> | null = null;
  if (ONLY_FAILED) {
    try {
      const sum = await fs.readJson(path.join('captures','_summary.json')) as any;
      const arr = Array.isArray(sum?.failedUrls) ? sum.failedUrls as string[] : [];
      failedSet = new Set(arr.filter(Boolean));
      console.log(`ONLY_FAILED=1 active, targeting ${failedSet.size} URLs`);
    } catch { console.warn('ONLY_FAILED=1 but captures/_summary.json not found or invalid'); failedSet = new Set(); }
  }
  let okCnt = 0, skipCnt = 0, failCnt = 0;
  const failedUrls: string[] = [];

  for (let i=1; i<rows.length; i++) {
    const r = rows[i] || [];
    const url = (r[urlCol] || '').toString().trim();
    const enabled = truthy(r[enabledCol]);
    if (!url) continue;
    if (ONLY_FAILED && failedSet && !failedSet.has(url)) { continue; }
    const rowNo = i+1;
    if (!enabled) { console.log(`SKIP row=${rowNo} url=${url} (有効=false)`); skipCnt++; continue; }
    const site = detectSite(url);
    const univ = (r[univCol] || '').toString();
    const grad = (r[gradCol] || '').toString();
    const person = (r[nameCol] || '').toString();
    const prefix = sanitizeName(`${person}_${univ}`) || 'output';
    let method: Method = 'http';
    if (METHOD_OVERRIDE === 'playwright') method = 'playwright';
    const isForced = force.urls.has(url) || force.substr.some(s => url.includes(s));
    if (isForced && ALLOW_PLAYWRIGHT) method = 'playwright';
    console.log(`[${rowNo}] chosen-initial=${method} url=${url}`);

    try {
      let html = ''; let metrics: any = {};
      const tryHttp = async () => { ({ html, metrics } = await captureHttp(url)); };
      const tryPw = async () => { ({ html, metrics } = await capturePlaywright(url)); };
      // HTTP優先（失敗・不十分ならPlaywrightへフォールバック）
      if (METHOD_OVERRIDE === 'playwright' || (isForced && ALLOW_PLAYWRIGHT)) {
        console.log(`[${rowNo}] try=playwright url=${url}`);
        await tryPw();
        method = 'playwright';
      } else {
        try {
          console.log(`[${rowNo}] try=http url=${url}`);
          await tryHttp();
          method = 'http';
        } catch (e) {
          console.warn(`[${rowNo}] http failed: ${(e as any)?.message || e}; fallback to Playwright`);
          console.log(`[${rowNo}] try=playwright url=${url}`);
          await tryPw();
          method = 'playwright';
        }
      if (!selfCheck(site, method, metrics)) {
        console.warn(`[${rowNo}] self-check failed after ${method}; retry other method`);
        if (method === 'http' && ALLOW_PLAYWRIGHT) {
          console.log(`[${rowNo}] retry=playwright url=${url}`);
          await tryPw(); method = 'playwright';
        } else if (method === 'playwright') {
          try {
            console.log(`[${rowNo}] retry=http url=${url}`);
            await tryHttp(); method = 'http';
          } catch {}
        }
      }
      }
      console.log(`[${rowNo}] metrics staff=${metrics?.staff ?? '-'} rlab=${metrics?.rlab ?? '-'} fish=${metrics?.fish ?? '-'} names=${metrics?.names ?? '-'}`);
      if (!selfCheck(site, method, metrics)) {
        console.log(`FAIL row=${rowNo} url=${url} reason=self-check`);
        failedUrls.push(url);
        failCnt++; continue;
      }

      // Save local (artifact) + meta
      const u = new URL(url); const slug = toSlug(u);
      const stamp = new Date().toISOString().replace(/[-:]/g,'').replace('T','_').slice(0,15);
      const fname = `${prefix}-${slug}-${stamp}.html`;
      const fpath = path.join('captures', fname);
      await fs.writeFile(fpath, html, 'utf8');
      const metaPath = path.join('captures', `${prefix}-${slug}-${stamp}.meta.json`);
      const capMeta = {
        url,
        university: univ || null,
        graduate_school: grad || null,
        site,
        methodUsed: method,
        saved_at_iso: new Date().toISOString(),
        output: path.resolve(fpath),
        metrics,
        row_index: rowNo,
      };
      try { await fs.writeJson(metaPath, capMeta, { spaces: 2 }); } catch {}

      okCnt++;
      console.log(`OK row=${rowNo} url=${url} method=${method} site=${site} out=${fname} (no Drive upload at capture stage)`);
    } catch (e: any) {
      console.log(`FAIL row=${rowNo} url=${url} reason=${e?.message || String(e)}`);
      failedUrls.push(url);
      failCnt++;
    }
  }

  // No sheet updates here — K/J are updated after cleaning in pushDir.
  console.log(`Summary: ok=${okCnt} skip=${skipCnt} fail=${failCnt}`);
  try { await fs.writeJson(path.join('captures', '_summary.json'), { ok: okCnt, skip: skipCnt, fail: failCnt, failedUrls }, { spaces: 2 }); } catch {}
  // ok が 0 件でもジョブ全体は継続できるよう非エラー終了
  // （後段ステップで captures/ の有無を確認しつつ処理する）
  // if (okCnt === 0) process.exit(1);
}

main().catch(err => { console.error(err); process.exit(1); });
