import fs from 'fs-extra';
import path from 'path';
import { cleanFacultyHtml } from './index.js';
async function main() {
    const inputDir = process.env.INPUT_DIR || 'captures';
    const outDir = process.env.OUTPUT_DIR || 'cleaned';
    await fs.ensureDir(outDir);
    const files = (await fs.readdir(inputDir)).filter(f => f.endsWith('.html'));
    if (!files.length) {
        console.error(`No .html files in ${inputDir}`);
        process.exit(1);
    }
    console.log(`Cleaner processing ${files.length} files from ${inputDir} -> ${outDir}`);
    let ok = 0;
    let fail = 0;
    for (const f of files) {
        try {
            const htmlPath = path.join(inputDir, f);
            const base = f.replace(/\.html$/i, '');
            const metaPath = path.join(inputDir, `${base}.meta.json`);
            let sourceUrl = 'about:blank';
            if (await fs.pathExists(metaPath)) {
                try {
                    const meta = await fs.readJson(metaPath);
                    if (meta && meta.url)
                        sourceUrl = String(meta.url);
                }
                catch { }
            }
            const raw = await fs.readFile(htmlPath, 'utf8');
            const cleaned = cleanFacultyHtml(raw, sourceUrl);
            const outPath = path.join(outDir, `${base}.clean.html`);
            await fs.writeFile(outPath, cleaned, 'utf8');
            console.log(`CLEAN OK ${f} -> ${path.relative('.', outPath)}`);
            ok++;
        }
        catch (e) {
            console.error(`CLEAN FAIL ${f}:`, e?.message || e);
            fail++;
        }
    }
    console.log(`Cleaner summary: ok=${ok} fail=${fail}`);
    if (ok === 0)
        process.exit(1);
}
main().catch(err => { console.error(err); process.exit(1); });
