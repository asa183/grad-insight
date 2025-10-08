import { chromium } from 'playwright';
import fs from 'fs-extra';
import path from 'path';
import { parseInput, InputRow } from './input';
import { hideBin } from 'yargs/helpers';
import yargs from 'yargs';

const argv = yargs(hideBin(process.argv))
  .option('input', { type: 'string', demandOption: true, describe: 'CSV/TSV/NDJSON file path or single URL string' })
  .option('out', { type: 'string', default: './captures', describe: 'Output directory for HTML/meta (created if missing)' })
  .option('concurrency', { type: 'number', default: 2, describe: 'Concurrent browser workers' })
  .option('timeout', { type: 'number', default: 45000, describe: 'Per-page timeout (ms)' })
  .option('slowmo', { type: 'number', default: 0, describe: 'Slow down operations for debugging (ms)' })
  .option('headful', { type: 'boolean', default: false, describe: 'Run with visible browser GUI' })
  .option('site', { type: 'string', default: 'auto', choices: ['auto','law','agr','edu'] as const, describe: 'Site-specific click/wait preset' })
  .option('screenshot', { type: 'boolean', default: false, describe: 'Save full-page screenshot' })
  .strict()
  .parseSync();

const HINTS: Record<'law'|'agr'|'edu', { extraClicks: string[]; extraWait: string[] }> = {
  law: { extraClicks: [], extraWait: [] },
  agr: { extraClicks: ['a[href*="/r/lab/"]'], extraWait: [] },
  edu: { extraClicks: ['[aria-controls]', '.tab a', '.tab button', '.accordion button', '.more a', '.more button'], extraWait: ['.intro-section'] },
};

function sitePreset(url: string, pref: 'auto' | 'law' | 'agr' | 'edu'): 'law'|'agr'|'edu' {
  if (pref !== 'auto') return pref;
  if (/let\.hokudai\.ac\.jp\/research\/staff-g/.test(url)) return 'law';
  if (/agr\.hokudai\.ac\.jp\/r\/faculty/.test(url)) return 'agr';
  if (/edu\.hokudai\.ac\.jp\/graduate_school\/department\/academic/.test(url)) return 'edu';
  return 'law';
}

function toSlug(u: URL) {
  const host = u.host.replace(/[:.]/g,'-');
  const parts = u.pathname.replace(/\/+\/g,'/').split('/').filter(Boolean);
  const tail = parts.slice(-1)[0] || 'root';
  return `${host}-${tail}`;
}

async function processOne(row: InputRow, outDir: string, timeout: number, slowmo: number, headful: boolean, sitePref: 'auto'|'law'|'agr'|'edu', screenshot: boolean) {
  const url = row.url.trim();
  const site = sitePreset(url, sitePref);
  const u = new URL(url);
  const slug = toSlug(u);
  const ts = new Date();
  const stamp = ts.toISOString().replace(/[-:]/g,'').replace('T','_').slice(0,15); // YYYYMMDD_HHMM
  await fs.ensureDir(outDir);

  const browser = await chromium.launch({ headless: !headful, slowMo: slowmo || 0 });
  const ctx = await browser.newContext();
  const page = await ctx.newPage();

  let ok = false; let attempts = 0; let lastErr: any = null;
  let html = ''; let metrics: any = {}; let screenshotPath: string | undefined;

  while (!ok && attempts < 3) {
    attempts++;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout });
      await page.waitForSelector('main', { timeout });

      // Generic tab/accordion click
      await page.evaluate(() => {
        const sels = ['[aria-controls]','[aria-expanded="false"]','.accordion button','.tab a','.tab button','.more a','.more button'];
        sels.forEach(sel => document.querySelectorAll<HTMLElement>(sel).forEach(el => { try { el.click(); } catch {} }));
      });

      // Site-specific extra clicks
      for (const sel of HINTS[site].extraClicks) {
        try { await page.$$eval(sel, (els: any[]) => els.forEach((el: any) => { try { el.click(); } catch {} })); } catch {}
      }

      // Gentle bottom scroll (promote lazy loads)
      await page.evaluate(async () => {
        const sleep = (ms:number)=>new Promise(r=>setTimeout(r,ms));
        let last = 0;
        for (let i=0; i<8; i++) {
          window.scrollTo(0, document.body.scrollHeight);
          await sleep(350);
          const h = document.body.scrollHeight;
          if (h === last) break; last = h;
        }
      });

      // Extra waits
      for (const sel of HINTS[site].extraWait) {
        try { await page.waitForSelector(sel, { timeout: Math.min(10000, Math.max(2000, Math.floor(timeout/3))) }); } catch {}
      }
      try { await page.waitForLoadState('networkidle', { timeout: Math.min(15000, Math.max(2000, Math.floor(timeout/3))) }); } catch {}

      // Screenshot (optional)
      if (screenshot) {
        const shotDir = path.join(outDir, 'screenshots');
        await fs.ensureDir(shotDir);
        screenshotPath = path.join(shotDir, `${slug}-${stamp}.png`);
        await page.screenshot({ path: screenshotPath, fullPage: true });
      }

      // Extract main.outerHTML
      html = await page.evaluate(() => {
        const node = document.querySelector('main') ?? document.body;
        return (node as HTMLElement).outerHTML;
      });

      // Quick metrics
      metrics = await page.evaluate(() => {
        const pick = (substr: string) => Array.from(document.querySelectorAll<HTMLAnchorElement>('a')).filter(a => a.href.includes(substr)).length;
        const textCandidates = Array.from((document.querySelector('main') ?? document.body).querySelectorAll<HTMLElement>('*'))
          .filter(el => {
            const rect = el.getBoundingClientRect();
            const vis = (rect.width*rect.height) > 0;
            const t = (el.innerText || '').replace(/\s+/g,' ').trim();
            return vis && t.length >= 20;
          }).length;
        return {
          anchors_total: document.querySelectorAll('a').length,
          staff: pick('/staff/'),
          rlab: pick('/r/lab/'),
          people: pick('/people/'),
          profile: pick('/profile'),
          researcher: pick('/researcher'),
          imgs: document.querySelectorAll('img').length,
          textCandidates,
        };
      });

      ok = true;
    } catch (e: any) {
      lastErr = e;
      await new Promise(r => setTimeout(r, 500 + attempts*300));
    }
  }

  await browser.close();

  const base = path.join(outDir, `${slug}-${stamp}`);
  if (ok) {
    const htmlPath = `${base}.html`;
    const metaPath = `${base}.meta.json`;
    await fs.writeFile(htmlPath, html, 'utf8');
    const meta = {
      url,
      university: row.university ?? null,
      graduate_school: row.graduate_school ?? null,
      site_preset: site,
      saved_at_iso: new Date().toISOString(),
      output: path.resolve(htmlPath),
      screenshot: screenshotPath ?? null,
      metrics,
    };
    await fs.writeJson(metaPath, meta, { spaces: 2 });
    console.log(`OK  ${url} staff=${metrics.staff} rlab=${metrics.rlab} textCandidates=${metrics.textCandidates} imgs=${metrics.imgs} out=${path.basename(htmlPath)}`);
  } else {
    console.error(`FAIL ${url} attempts=${attempts} err=${(lastErr && (lastErr.message || String(lastErr)))}`);
    process.exitCode = 1;
  }
}

(async () => {
  const rows = await parseInput(argv.input);
  await fs.ensureDir(argv.out);

  // concurrency pool
  const pool = Array.from({ length: Math.max(1, argv.concurrency) }, () => Promise.resolve());
  let i = 0;
  const next = async () => {
    const row = rows[i++];
    if (!row) return;
    await processOne(row, argv.out, argv.timeout, argv.slowmo, argv.headful, argv.site as any, argv.screenshot);
    return next();
  };
  await Promise.all(pool.map(() => next()));
})();

