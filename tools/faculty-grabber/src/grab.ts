import { chromium, Page } from 'playwright';
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
  .option('stealth', { type: 'boolean', default: true, describe: 'Apply stealth-like page hardening' })
  .option('names-timeout', { type: 'number', default: 30000, describe: 'Max wait for names to appear (ms)' })
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
  const parts = u.pathname.replace(/\/+/g,'/').split('/').filter(Boolean);
  const tail = parts.slice(-1)[0] || 'root';
  return `${host}-${tail}`;
}

function rand(min:number, max:number) { return Math.floor(Math.random()*(max-min+1))+min; }
async function jitter(page: Page) { await page.waitForTimeout(rand(120, 380)); }
async function gentleBottomScroll(page: Page, rounds = 10) {
  await page.evaluate(async (n) => {
    const sleep = (ms:number)=>new Promise(r=>setTimeout(r,ms));
    let last = 0;
    for (let i=0; i<n; i++) {
      window.scrollTo(0, Math.max(0, document.body.scrollHeight - (i*50)));
      await sleep(250 + Math.random()*200);
      const h = document.body.scrollHeight;
      if (h === last) break; last = h;
    }
  }, rounds);
}

async function waitNamesVisible(page: Page, site: 'law'|'agr'|'edu', timeout:number) {
  if (site === 'edu') {
    await page.waitForFunction(() => {
      const nodes = Array.from(document.querySelectorAll('.intro-section .name, dt.name, .m-name'));
      return nodes.some(n => (n as HTMLElement).innerText.trim().length >= 2);
    }, { timeout: Math.max(15000, Math.min(timeout, 40000)) });
  }
  if (site === 'agr') {
    await page.waitForFunction(() => {
      const nodes = Array.from(document.querySelectorAll('.list-item-faculty .name, .item-faculty .name'));
      return nodes.some(n => (n as HTMLElement).innerText.trim().length >= 2);
    }, { timeout: Math.max(15000, Math.min(timeout, 40000)) });
  }
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
  const ctx = await browser.newContext(argv.stealth ? {
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
    viewport: { width: 1366, height: 900 },
    javaScriptEnabled: true,
  } : {});
  const page = await ctx.newPage();
  if (argv.stealth) {
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
      if (/\.(ttf|woff2?|mp4|webm|gif)$/i.test(u)) return route.abort();
      return route.continue();
    });
  }

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
      await jitter(page);

      // Site-specific extra clicks
      for (const sel of HINTS[site].extraClicks) {
        try { await page.$$eval(sel, (els: any[]) => els.forEach((el: any) => { try { el.click(); } catch {} })); } catch {}
        await jitter(page);
      }

      // AGR: sequentially scroll to initial section anchors to promote content
      if (site === 'agr') {
        try {
          const ids = await page.$$eval('h2[id^="initial-"]', els => els.map(e => (e as HTMLElement).id).filter(Boolean));
          for (const id of ids) {
            try { await page.locator(`#${id}`).scrollIntoViewIfNeeded(); } catch {}
            await page.waitForTimeout(250 + rand(0,250));
          }
        } catch {}
      }

      // Gentle bottom scroll (promote lazy loads)
      await gentleBottomScroll(page, 10);

      // Extra waits
      for (const sel of HINTS[site].extraWait) {
        try { await page.waitForSelector(sel, { timeout: Math.min(10000, Math.max(2000, Math.floor(timeout/3))) }); } catch {}
      }
      try { await page.waitForLoadState('networkidle', { timeout: Math.min(15000, Math.max(2000, Math.floor(timeout/3))) }); } catch {}

      // Stronger: wait for names to be visible on CSR-heavy sites
      await waitNamesVisible(page, site, argv['names-timeout'] || timeout);

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

      // Post-check minimum signals
      const namesOk = (site === 'law') ? true : true; // already waited above for CSR sites
      const anchorsOk = (metrics.staff >= 1 || metrics.rlab >= 1);
      const textOk = metrics.textCandidates >= 10;
      if (!(namesOk && anchorsOk && textOk)) {
        throw new Error('Post-check failed: insufficient signals');
      }

      ok = true;
    } catch (e: any) {
      lastErr = e;
      const backoff = attempts===1? 500 : attempts===2? 1200 : 2500;
      await page.waitForTimeout(backoff + rand(0,300));
      // Strengthen strategy on later attempts
      if (site === 'edu') {
        // broaden click targets
        const extra = ['[role="tab"]','button[aria-controls]','.js-tab a','.js-tab button'];
        for (const sel of extra) {
          try { await page.$$eval(sel, (els: any[]) => els.forEach((el: any) => { try { el.click(); } catch {} })); } catch {}
          await jitter(page);
        }
      }
      if (site === 'agr') {
        // deeper scroll passes
        await gentleBottomScroll(page, 16);
        await page.waitForTimeout(150 + rand(0,200));
      }
    }
  }

  // On failure, still capture a screenshot if requested
  if (!ok && screenshot && !screenshotPath) {
    try {
      const shotDir = path.join(outDir, 'screenshots');
      await fs.ensureDir(shotDir);
      screenshotPath = path.join(shotDir, `${slug}-${stamp}-fail.png`);
      await page.screenshot({ path: screenshotPath, fullPage: true });
    } catch {}
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
      attempts,
    };
    await fs.writeJson(metaPath, meta, { spaces: 2 });
    console.log(`OK  ${url} staff=${metrics.staff} rlab=${metrics.rlab} textCandidates=${metrics.textCandidates} imgs=${metrics.imgs} out=${path.basename(htmlPath)}`);
  } else {
    const metaPath = `${base}.meta.json`;
    const meta = {
      url,
      university: row.university ?? null,
      graduate_school: row.graduate_school ?? null,
      site_preset: site,
      saved_at_iso: new Date().toISOString(),
      output: null as any,
      screenshot: screenshotPath ?? null,
      metrics,
      attempts,
      reason: lastErr && (lastErr.message || String(lastErr)),
    };
    await fs.writeJson(metaPath, meta, { spaces: 2 });
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
