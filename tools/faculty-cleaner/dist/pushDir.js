import { google } from 'googleapis';
import fs from 'fs-extra';
import path from 'path';
const SHEET_ID = process.env.SHEET_ID || '';
const SHEET_NAME = process.env.SHEET_NAME || 'Examples';
const DRIVE_FOLDER_ID = process.env.DRIVE_FOLDER_ID || '';
const GOOGLE_CREDENTIALS_JSON = process.env.GOOGLE_CREDENTIALS_JSON;
if (!SHEET_ID || !DRIVE_FOLDER_ID) {
    console.error('SHEET_ID and DRIVE_FOLDER_ID are required');
    process.exit(2);
}
async function getAuth() {
    if (GOOGLE_CREDENTIALS_JSON) {
        const creds = JSON.parse(GOOGLE_CREDENTIALS_JSON);
        const auth = new google.auth.GoogleAuth({ credentials: creds, scopes: ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'] });
        return await auth.getClient();
    }
    return await google.auth.getClient({ scopes: ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive'] });
}
async function ensureAnyoneReader(drive, fileId) {
    try {
        await drive.permissions.create({ fileId, requestBody: { type: 'anyone', role: 'reader' }, supportsAllDrives: true });
    }
    catch { /* ignore */ }
}
async function main() {
    const INPUT_DIR = process.env.INPUT_DIR || 'cleaned';
    const CAP_DIR = process.env.CAP_DIR || 'captures';
    const files = (await fs.readdir(INPUT_DIR).catch(() => [])).filter(f => f.endsWith('.clean.html'));
    if (!files.length) {
        console.warn(`no clean html in ${INPUT_DIR} — nothing to push.`);
        return;
    }
    const auth = await getAuth();
    const drive = google.drive({ version: 'v3', auth: auth });
    const sheets = google.sheets({ version: 'v4', auth: auth });
    const range = `${SHEET_NAME}!A:O`;
    const resp = await sheets.spreadsheets.values.get({ spreadsheetId: SHEET_ID, range });
    const rows = (resp.data.values || []);
    const header = rows[0] || [];
    const urlToRow = new Map();
    for (let i = 1; i < rows.length; i++) {
        const r = rows[i] || [];
        const url = (r[2] || '').toString().trim(); // C列=研究科URL
        if (url)
            urlToRow.set(url, i);
    }
    const updates = [];
    let uploaded = 0;
    for (const f of files) {
        const base = f.replace(/\.clean\.html$/, '');
        const metaPath = path.join(CAP_DIR, `${base}.meta.json`);
        let url = '';
        try {
            const meta = await fs.readJson(metaPath);
            url = String(meta.url || '');
        }
        catch { }
        if (!url) {
            console.warn(`skip ${f}: url not found in meta`);
            continue;
        }
        const rowIndex = urlToRow.get(url);
        if (rowIndex == null) {
            console.warn(`skip ${f}: url not found in sheet`);
            continue;
        }
        // upload to Drive
        const filePath = path.join(INPUT_DIR, f);
        const name = f.replace(/\.clean\.html$/i, '') + '.html';
        const created = await drive.files.create({
            requestBody: { name, parents: [DRIVE_FOLDER_ID], mimeType: 'text/html' },
            media: { mimeType: 'text/html', body: await fs.readFile(filePath, 'utf8') },
            fields: 'id, webViewLink', supportsAllDrives: true,
        });
        const id = created.data.id;
        await ensureAnyoneReader(drive, id);
        const meta = await drive.files.get({ fileId: id, fields: 'id, webViewLink', supportsAllDrives: true });
        const link = meta.data.webViewLink || `https://drive.google.com/file/d/${id}/view`;
        const r1 = rowIndex + 1;
        updates.push({ range: `${SHEET_NAME}!K${r1}:O${r1}`, values: [[link, id, 'success', '', new Date().toISOString()]] });
        uploaded++;
    }
    if (updates.length) {
        await sheets.spreadsheets.values.batchUpdate({ spreadsheetId: SHEET_ID, requestBody: { valueInputOption: 'RAW', data: updates } });
    }
    console.log(`Push summary: uploaded=${uploaded}`);
    // アップロード 0 件でも非エラー終了（セル更新なし）
}
main().catch(err => { console.error(err); process.exit(1); });
