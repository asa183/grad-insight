import { chromium } from 'playwright';
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
const METHOD_OVERRIDE = (process.env.METHOD || '').toLowerCase();
function detectSite(url) {
    if (/let\.hokudai\.ac\.jp\/research\/staff-g/.test(url))
        return 'let';
    if (/agr\.hokudai\.ac\.jp\/r\/faculty/.test(url))
        return 'agr';
    if (/edu\.hokudai\.ac\.jp\/graduate_school\/department\/academic\//.test(url))
        return 'edu';
    if (/www2\.fish\.hokudai\.ac\.jp\/faculty-member\//.test(url))
        return 'fish';
    return 'other';
}
function chooseMethod(site) {
    if (METHOD_OVERRIDE === 'http' || METHOD_OVERRIDE === 'playwright')
        return METHOD_OVERRIDE;
    if (site === 'agr' || site === 'fish' || site === 'other')
        return 'http';
    return 'playwright';
}
function truthy(v) {
    const s = String(v ?? '').trim().toLowerCase();
    return ['true', '1', 'yes', 'y', '有効', 'ok'].includes(s);
}
function absolutize(u, base) {
    if (!u)
        return u;
    if (/^(mailto:|tel:|javascript:|#)/i.test(u))
        return u;
    try {
        return new URL(u, base).toString();
    }
    catch {
        return u;
    }
}
async function captureHttp(url) {
    const res = await fetch(url, { redirect: 'follow' });
    if (!res.ok)
        throw new Error(`HTTP ${res.status}`);
    const raw = await res.text();
    const $ = cheerio.load(raw);
    const node = $('main').first();
    let outer = node.length ? $.html(node.get(0)) : $('body').length ? $.html($('body').get(0)) : raw;
    // absolutize links
    const $$ = cheerio.load(outer);
    $$('a[href]').each((_, a) => { const href = $$(a).attr('href') || ''; $$(a).attr('href', absolutize(href, url)); });
    $$('img[src]').each((_, img) => { const src = $$(img).attr('src') || ''; $$(img).attr('src', absolutize(src, url)); });
    outer = $$.root().html() || outer;
    // metrics
    const anchors = $$('a').toArray().map((a) => $$(a).attr('href') || '');
    const staff = anchors.filter((h) => h.includes('/staff/')).length;
    const rlab = anchors.filter((h) => h.includes('/r/lab/')).length;
    const fish = anchors.filter((h) => h.includes('/faculty-member/')).length;
    const names = $$('.name, .m-name, dt.name').toArray().filter((el) => ($$(el).text() || '').trim().length >= 2).length;
    return { html: outer, metrics: { staff, rlab, fish, names } };
}
async function capturePlaywright(url) {
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
        navigator.plugins = [1, 2, 3];
        // @ts-ignore
        navigator.languages = ['ja-JP', 'ja', 'en-US'];
    });
    await page.route('**/*', (route) => {
        const u = route.request().url();
        if (/\.(ttf|woff2?|mp4|webm|gif)$/i.test(u))
            return route.abort();
        return route.continue();
    });
    let html = '';
    try {
        await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
        await page.waitForFunction(() => !!document.body, { timeout: 30000 });
        // generic clicks
        await page.evaluate(() => {
            const sels = ['[aria-controls]', '[aria-expanded="false"]', '.accordion button', '.tab a', '.tab button', '.more a', '.more button'];
            sels.forEach(sel => document.querySelectorAll(sel).forEach(el => { try {
                el.click();
            }
            catch { } }));
        });
        // scroll waves
        await page.evaluate(async () => {
            const sleep = (ms) => new Promise(r => setTimeout(r, ms));
            const getDoc = () => (document.scrollingElement || document.documentElement || document.body);
            let last = 0;
            for (let i = 0; i < 10; i++) {
                const d = getDoc();
                if (!d)
                    break;
                const h = d.scrollHeight || 0;
                window.scrollTo(0, Math.max(0, h - (i * 50)));
                await sleep(250 + Math.random() * 200);
                const d2 = getDoc();
                const h2 = d2 ? d2.scrollHeight || 0 : 0;
                if (h2 === last)
                    break;
                last = h2;
            }
        });
        try {
            await page.waitForLoadState('networkidle', { timeout: 15000 });
        }
        catch { }
        // extract
        html = await page.evaluate(() => {
            const node = (document.querySelector('main') ?? document.body);
            return node ? node.outerHTML : '<body></body>';
        });
    }
    finally {
        await browser.close();
    }
    // metrics via cheerio
    const $ = cheerio.load(html);
    const anchors = $('a').toArray().map((a) => $(a).attr('href') || '');
    const staff = anchors.filter((h) => h.includes('/staff/')).length;
    const rlab = anchors.filter((h) => h.includes('/r/lab/')).length;
    const fish = anchors.filter((h) => h.includes('/faculty-member/')).length;
    const names = $('.name, .m-name, dt.name').toArray().filter((el) => ($(el).text() || '').trim().length >= 2).length;
    return { html, metrics: { staff, rlab, fish, names } };
}
function selfCheck(site, method, metrics) {
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
    if (!OAUTH_CLIENT_ID || !OAUTH_CLIENT_SECRET || !OAUTH_REFRESH_TOKEN)
        return null;
    const oauth2 = new google.auth.OAuth2({
        clientId: OAUTH_CLIENT_ID,
        clientSecret: OAUTH_CLIENT_SECRET,
        redirectUri: 'urn:ietf:wg:oauth:2.0:oob',
    });
    oauth2.setCredentials({ refresh_token: OAUTH_REFRESH_TOKEN });
    return oauth2;
}
async function ensureLinkSharing(drive, fileId) {
    try {
        await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role: 'reader' }, supportsAllDrives: true });
    }
    catch { }
}
function toSlug(u) {
    const host = u.host.replace(/[:.]/g, '-');
    const parts = u.pathname.replace(/\/+/g, '/').split('/').filter(Boolean);
    const tail = parts.slice(-1)[0] || 'root';
    return `${host}-${tail}`;
}
async function main() {
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
    const sheets = google.sheets({ version: 'v4', auth: oauth });
    let drive = google.drive({ version: 'v3', auth: oauth });
    try {
        const about = await drive.about.get({ fields: 'user' });
        const email = about.data?.user?.emailAddress || '(unknown)';
        console.log(`Using OAuth for Drive uploads as ${email}`);
    }
    catch (e) {
        console.error('OAuth initialization failed:', e?.message || e);
        process.exit(2);
    }
    // Resolve sheet name case-insensitively
    let sheetName = SHEET_NAME;
    try {
        const meta = await sheets.spreadsheets.get({ spreadsheetId: SHEET_ID });
        const titles = (meta.data.sheets || []).map(s => s.properties?.title || '').filter(Boolean);
        const found = titles.find(t => t.toLowerCase() === SHEET_NAME.toLowerCase());
        if (found)
            sheetName = found;
        else
            throw new Error(`Sheet tab '${SHEET_NAME}' not found. Available: ${titles.join(', ')}`);
    }
    catch (e) {
        console.error('Sheet metadata error:', e?.message || e);
        throw e;
    }
    // 共有ドライブ/マイドライブのログ（診断用）
    try {
        const folderMeta = await drive.files.get({ fileId: DRIVE_FOLDER_ID, fields: 'id,name,driveId', supportsAllDrives: true });
        const dId = folderMeta.data.driveId;
        const dName = folderMeta.data.name;
        console.log(`Drive folder preflight: name="${dName}" sharedDrive=${dId ? 'yes' : 'no'}`);
    }
    catch (e) {
        console.warn('Folder preflight failed:', e?.message || e);
    }
    const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range: `${sheetName}!A:K` });
    const rows = (resp.data.values || []);
    if (!rows.length) {
        console.error('Sheet empty');
        process.exit(1);
    }
    const header = rows[0];
    // Fixed columns per spec: C=url, J=有効, K=HTML
    const urlCol = 2, enabledCol = 9, htmlCol = 10;
    await fs.ensureDir('captures');
    let okCnt = 0, skipCnt = 0, failCnt = 0;
    const updates = [];
    for (let i = 1; i < rows.length; i++) {
        const r = rows[i] || [];
        const url = (r[urlCol] || '').toString().trim();
        const enabled = truthy(r[enabledCol]);
        if (!url)
            continue;
        const rowNo = i + 1;
        if (!enabled) {
            console.log(`SKIP row=${rowNo} url=${url} (有効=false)`);
            skipCnt++;
            continue;
        }
        const site = detectSite(url);
        let method = chooseMethod(site);
        try {
            let html = '';
            let metrics = {};
            if (method === 'http') {
                ({ html, metrics } = await captureHttp(url));
                if (!selfCheck(site, method, metrics) && site === 'other') {
                    // fallback to Playwright for others if http insufficient
                    ({ html, metrics } = await capturePlaywright(url));
                    method = 'playwright';
                }
            }
            else {
                ({ html, metrics } = await capturePlaywright(url));
            }
            if (!selfCheck(site, method, metrics)) {
                console.log(`FAIL row=${rowNo} url=${url} reason=self-check`);
                failCnt++;
                continue;
            }
            // Save local (artifact)
            const u = new URL(url);
            const slug = toSlug(u);
            const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15);
            const fname = `${slug}-${stamp}.html`;
            const fpath = path.join('captures', fname);
            await fs.writeFile(fpath, html, 'utf8');
            // Upload to Drive（OAuthを優先）
            let created;
            try {
                created = await drive.files.create({
                    requestBody: { name: fname, parents: [DRIVE_FOLDER_ID], mimeType: 'text/html' },
                    media: { mimeType: 'text/html', body: fs.createReadStream(fpath) }, fields: 'id, webViewLink', supportsAllDrives: true,
                });
            }
            catch (e) {
                const msg = e?.message || String(e);
                console.error('Drive upload error:', msg);
                throw e;
            }
            const id = created.data.id;
            await ensureLinkSharing(drive, id);
            const meta = await drive.files.get({ fileId: id, fields: 'id, webViewLink', supportsAllDrives: true });
            const link = meta.data.webViewLink || `https://drive.google.com/file/d/${id}/view`;
            updates.push({ r: rowNo, link });
            okCnt++;
            console.log(`OK row=${rowNo} url=${url} method=${method} site=${site} out=${fname} drive=${link}`);
        }
        catch (e) {
            console.log(`FAIL row=${rowNo} url=${url} reason=${e?.message || String(e)}`);
            failCnt++;
        }
    }
    if (updates.length) {
        const data = updates.map(u => ({ range: `${sheetName}!K${u.r}`, values: [[u.link]] }));
        await sheets.spreadsheets.values.batchUpdate({ spreadsheetId: SHEET_ID, requestBody: { valueInputOption: 'RAW', data } });
    }
    console.log(`Summary: ok=${okCnt} skip=${skipCnt} fail=${failCnt}`);
    if (okCnt === 0)
        process.exit(1);
}
main().catch(err => { console.error(err); process.exit(1); });
