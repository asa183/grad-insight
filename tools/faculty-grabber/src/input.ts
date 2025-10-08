import fs from 'fs-extra';
import Papa from 'papaparse';

export type InputRow = { url: string; university?: string; graduate_school?: string };

export async function parseInput(input: string): Promise<InputRow[]> {
  // If looks like a URL and not a local file path, treat as single URL
  if (/^https?:\/\//i.test(input) && !(await fs.pathExists(input))) {
    return [{ url: input }];
  }

  const text = await fs.readFile(input, 'utf8');
  const ext = (input.split('.').pop() || '').toLowerCase();

  if (ext === 'jsonl' || ext === 'ndjson') {
    return text
      .split(/\r?\n/)
      .map(l => l.trim())
      .filter(Boolean)
      .map(l => JSON.parse(l))
      .map((obj: any) => ({
        url: String(obj.url || obj.URL || obj.link || '').trim(),
        university: obj.university || obj.University || obj.uni || undefined,
        graduate_school: obj.graduate_school || obj.grad || obj.school || undefined,
      }))
      .filter(r => r.url);
  }

  // CSV / TSV via Papa
  const isTsv = ext === 'tsv';
  const res = Papa.parse(text, { header: true, delimiter: isTsv ? '\t' : ',' });
  const rows = (res.data || []) as any[];
  const out: InputRow[] = [];
  for (const r of rows) {
    if (!r) continue;
    const url = String(r.url || r.URL || r.link || '').trim();
    if (!url) continue;
    out.push({
      url,
      university: r.university || r.University || r.uni || undefined,
      graduate_school: r.graduate_school || r.grad || r.school || undefined,
    });
  }
  return out;
}

