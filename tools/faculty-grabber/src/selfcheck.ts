import fs from 'fs-extra';
import path from 'path';

function isEdu(url: string) { return /edu\.hokudai\.ac\.jp/.test(url); }
function isAgr(url: string) { return /agr\.hokudai\.ac\.jp/.test(url); }

async function main() {
  const capDir = path.resolve(process.cwd(), 'captures');
  if (!await fs.pathExists(capDir)) {
    console.error('captures/ missing');
    process.exit(1);
  }
  const htmlFiles = (await fs.readdir(capDir)).filter(f => f.endsWith('.html'));
  if (htmlFiles.length === 0) {
    console.error('no html in captures/');
    process.exit(1);
  }

  let eduSeen = false, agrSeen = false;
  let eduOk = false, agrOk = false;

  const summary: any[] = [];
  for (const f of htmlFiles) {
    const base = f.replace(/\.html$/i, '');
    const metaPath = path.join(capDir, base + '.meta.json');
    let url = '';
    let metrics: any = {};
    try { const meta = await fs.readJson(metaPath); url = String(meta.url || ''); metrics = meta.metrics || {}; } catch {}

    const html = await fs.readFile(path.join(capDir, f), 'utf8');
    const hasHan = /[\p{sc=Han}]{2,}/u.test(html);
    const hasKana = /[\p{sc=Katakana}・ー]{2,}/u.test(html);
    const namesDetected = metrics.namesDetected || hasHan || hasKana;
    const textOk = (metrics.textCandidates || 0) >= 6;
    const imgsOk = (metrics.imgs || 0) >= 1;
    const rlabOk = (metrics.rlab || 0) >= 1 || /href="[^"]*\/r\/lab\//.test(html);

    let ok = false;
    if (isEdu(url)) {
      eduSeen = true;
      ok = !!(namesDetected || textOk || imgsOk);
      eduOk = eduOk || ok;
    } else if (isAgr(url)) {
      agrSeen = true;
      ok = !!(rlabOk && (namesDetected || textOk));
      agrOk = agrOk || ok;
    } else {
      // other sites: accept minimal signal
      ok = !!(namesDetected || textOk || imgsOk);
    }
    summary.push({ file: f, url, ok, metrics });
  }

  await fs.writeJson(path.join(capDir, 'captured-summary.json'), summary, { spaces: 2 });

  // Require per-site pass only if that site appears in inputs
  if (eduSeen && !eduOk) {
    console.error('EDU check failed: names/text not visible');
    process.exit(1);
  }
  if (agrSeen && !agrOk) {
    console.error('AGR check failed: names or /r/lab missing');
    process.exit(1);
  }
  console.log('Self-check passed:', JSON.stringify({ eduSeen, eduOk, agrSeen, agrOk }));
}

main().catch(err => { console.error(err); process.exit(1); });

