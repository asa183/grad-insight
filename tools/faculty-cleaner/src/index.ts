import { load, Cheerio, CheerioAPI } from 'cheerio';

const NOISE_SELECTOR_LIST = [
  'header','nav','footer','aside','form','form[role="search"]',
  '.global-nav','.site-header','.site-footer','.breadcrumb','.breadcrumbs','.pager','.pagination',
  '.sns','.share','.social','.skip-link','.cookie','.consent','.gdpr','.banner','.ads','.advertisement',
  '.newsletter','.modal','.popup','.drawer','.offcanvas',
  '.side','.sidenav','[class*="side-nav"]','[id*="side-nav"]',
  // common search wrappers
  '.search','.detailSearch','.detail-search','.global-search','.site-search','.quick-search',
  '[id*="search"]','[class*="search"]',
];

const NOISE_CLASS_SUBSTR = [
  'global-nav','site-header','site-footer','breadcrumb','breadcrumbs','pager','pagination','sns','share','social',
  'skip-link','cookie','consent','gdpr','banner','ads','advert','newsletter','modal','popup','drawer','offcanvas',
  'side','sidenav','side-nav',
  // search/control related
  'search','detailsearch','quick-search','global-search','selectbox','select-box','filter','filters','facet','refine','toolbar','controls','autocomplete','suggest'
];

const FORCE_REMOVE_TAGS = new Set(['script','style','noscript','template','iframe']);

const ROLE_WORDS_JA = ['教授','准教授','助教','講師','特任教授','客員教授','名誉教授','非常勤講師','招聘教授','招へい教員'];
const ROLE_WORDS_EN = ['Professor','Associate Professor','Assistant Professor','Adjunct Professor','Visiting Professor','Professor Emeritus','Lecturer','Research Fellow','Researcher','Senior Researcher','Postdoctoral'];
const PERSON_LINK_HINTS = ['/faculty-member/','/faculty/','/people/','/person/','/profile','/profiles','/researcher','/researchers','/staff/','/r/lab/'];
const LABEL_WORDS = ['氏名','名前','専門','研究分野','Research field','Field(s)','Fields'];

const BLOCK_TAGS = new Set(['section','article','ul','ol','li','table','thead','tbody','tr','td','th','div','p','h1','h2','h3','h4','h5','h6','dl','dt','dd','hr']);

function hasDescendantAnchorWithProfile($: CheerioAPI, $node: Cheerio<any>): boolean {
  let ok = false;
  $node.find('a').each((_, a) => {
    const href = ($(a).attr('href') || '').toLowerCase();
    const txt = $(a).text().trim().toLowerCase();
    if (href) {
      if (PERSON_LINK_HINTS.some(h => href.includes(h))) { ok = true; return false; }
    }
    if (/(^|\s)(hp|ホームページ|研究者総覧|総覧|profile|profiles?|people|researchers?|staff)(\s|$)/i.test(txt)) { ok = true; return false; }
    return;
  });
  return ok;
}

function hasNameOrRoleClues($: CheerioAPI, $node: Cheerio<any>): boolean {
  const text = $node.text();
  const kanjiName = /[\p{sc=Han}]{2,}(?:[\s\u3000]+)[\p{sc=Han}]{1,}/u;
  const kataName = /[\p{sc=Katakana}・ー]{2,}(?:[\s\u3000]+[\p{sc=Katakana}・ー]{2,})?/u;
  const roles = new RegExp('(' + [...ROLE_WORDS_JA, ...ROLE_WORDS_EN].map(escapeRegExp).join('|') + ')', 'i');
  return kanjiName.test(text) || kataName.test(text) || roles.test(text);
}

function escapeRegExp(s: string): string { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function isFragmentOnly(u: string): boolean { return /^#/.test(u); }
function isSpecialScheme(u: string): boolean { return /^(mailto:|tel:|javascript:)/i.test(u); }
function absolutizeUrl(url: string, base: string): string {
  if (!url) return url;
  if (isFragmentOnly(url) || isSpecialScheme(url)) return url;
  try { return new URL(url, base).toString(); } catch { return url; }
}
function takeFirstFromSrcset(srcset?: string): string | undefined {
  if (!srcset) return undefined;
  const first = srcset.split(',')[0]?.trim();
  if (!first) return undefined;
  return first.split(/\s+/)[0];
}
function unwrapKeepChildren($: CheerioAPI, el: any) { const $el = $(el); const children = $el.contents(); $el.replaceWith(children); }
function removeCommentsDeep($: CheerioAPI, $root: Cheerio<any>) {
  const walk = (node: any) => {
    const any = node as any;
    if (any.type === 'comment' && any.parent) { $(any).remove(); return; }
    if (any.children) { for (const child of any.children) walk(child); }
  };
  $root.each((_, el) => walk(el as any));
}
function cleanAttributes($: CheerioAPI, $root: Cheerio<any>) {
  $root.find('*').each((_, el) => {
    const attribs = (el as any).attribs || {};
    for (const name of Object.keys(attribs)) {
      const lower = name.toLowerCase();
      if (lower === 'style' || lower.startsWith('on')) $(el).removeAttr(name);
    }
  });
}
function compressBrRuns($: CheerioAPI, $root: Cheerio<any>) {
  $root.find('br').each((_, el) => {
    const prev = (el as any).prev; if (prev && prev.tagName === 'br') $(el).remove();
  });
}
function normalizeTextWhitespace($: CheerioAPI, $root: Cheerio<any>) {
  const walker = (node: any) => {
    const any = node as any;
    if (any.type === 'text') {
      let data: string = any.data ?? '';
      data = data.replace(/\u00A0|&nbsp;/g, ' ');
      data = data.replace(/[\t ]{2,}/g, ' ');
      data = data.replace(/\n{2,}/g, '\n');
      for (const label of LABEL_WORDS) {
        const safe = escapeRegExp(label);
        const reWithColon = new RegExp(`(${safe})([：:])(?!\n)`, 'g'); data = data.replace(reWithColon, `$1$2\n`);
        const reNoColon = new RegExp(`(${safe})(?![\n：:])`, 'g'); data = data.replace(reNoColon, `$1\n`);
      }
      any.data = data; return;
    }
    if (any.children) { for (const child of any.children) walker(child as any); }
  };
  $root.each((_, el) => walker(el as any));
}
function ensureNewlinesBetweenBlocks(html: string): string {
  const blockNames = Array.from(BLOCK_TAGS).join('|');
  const closeOpen = new RegExp(`</(?:${blockNames})>\s*(?=<(?:${blockNames})(?:\s|>))`, 'gi');
  return html.replace(closeOpen, m => m.replace(/>\s*$/, '>' + '\n'));
}
function clipToLimit($: CheerioAPI, $root: Cheerio<any>, limit: number) {
  let out = $.html($root[0]);
  if (out.length <= limit) return;
  const texts: any[] = [];
  const collect = (node: any) => { const any = node as any; if (any.type === 'text') texts.push(any); if (any.children) for (const c of any.children) collect(c as any); };
  collect($root[0] as any);
  let iterations = 0;
  while (out.length > limit && iterations < texts.length + 5) {
    let need = out.length - limit;
    for (let i = texts.length - 1; i >= 0 && need > 0; i--) {
      const tn = texts[i]; const s: string = tn.data || ''; if (!s) continue;
      const cut = Math.min(s.length, Math.max(16, Math.ceil(need / 2)));
      tn.data = s.slice(0, s.length - cut).trimEnd(); need -= cut;
    }
    out = $.html($root[0]); iterations++;
  }
}
function selectScope($: CheerioAPI): Cheerio<any> {
  const $main = $('main').first(); if ($main.length) return $main;
  const $body = $('body').first(); if ($body.length) return $body;
  const $wrapper = $('<main></main>'); $wrapper.append($.root().children()); $.root().append($wrapper); return $wrapper;
}
function removeForcedTags($: CheerioAPI, $scope: Cheerio<any>) { $scope.find(Array.from(FORCE_REMOVE_TAGS).join(',')).remove(); }
function removeOrUnwrapNoise($: CheerioAPI, $scope: Cheerio<any>) {
  const $candidates = $scope.find(NOISE_SELECTOR_LIST.join(','));
  $candidates.each((_, el) => { const $el = $(el); const keep = hasDescendantAnchorWithProfile($, $el) || hasNameOrRoleClues($, $el); if (keep) { unwrapKeepChildren($, el); } else { $el.remove(); } });
  $scope.find('[class]').each((_, el) => {
    const $el = $(el); const cls = ($el.attr('class') || '').toLowerCase(); if (!cls) return; if (!NOISE_CLASS_SUBSTR.some(key => cls.includes(key))) return;
    const keep = hasDescendantAnchorWithProfile($, $el) || hasNameOrRoleClues($, $el); if (keep) { unwrapKeepChildren($, el); } else { $el.remove(); }
  });
  // id-based hints as well
  $scope.find('[id]').each((_, el) => {
    const $el = $(el); const idv = ($el.attr('id') || '').toLowerCase(); if (!idv) return;
    if (!NOISE_CLASS_SUBSTR.some(key => idv.includes(key))) return;
    const keep = hasDescendantAnchorWithProfile($, $el) || hasNameOrRoleClues($, $el); if (keep) { unwrapKeepChildren($, el); } else { $el.remove(); }
  });
}
function absolutizeLinksAndImages($: CheerioAPI, $scope: Cheerio<any>, baseUrl: string) {
  $scope.find('a[href]').each((_, a) => { const href = $(a).attr('href'); if (!href) return; $(a).attr('href', absolutizeUrl(href, baseUrl)); });
  $scope.find('img').each((_, img) => {
    const $img = $(img); let src = $img.attr('src') || '';
    if (!src || src.trim() === '') {
      const cand = $img.attr('data-src') || $img.attr('data-original') || $img.attr('data-lazy') || takeFirstFromSrcset($img.attr('data-srcset')) || takeFirstFromSrcset($img.attr('srcset')) || '';
      if (cand) { $img.attr('src', cand); src = cand; }
    }
    if (src) { $img.attr('src', absolutizeUrl(src, baseUrl)); }
  });
}
function pruneEventAndStyle($: CheerioAPI, $scope: Cheerio<any>) { cleanAttributes($, $scope); }
function simplifyFormControls($: CheerioAPI, $scope: Cheerio<any>) {
  // Remove very large select/datalist blocks entirely (option spam)
  $scope.find('select, datalist').each((_, el) => {
    const $el = $(el);
    const options = $el.find('option');
    const optCount = options.length;
    const totalTextLen = options.toArray().reduce((acc, o: any) => acc + (($(o).text() || '').trim().length), 0);
    if (optCount >= 20 || totalTextLen >= 200) { $el.remove(); return; }
    // For smaller ones, keep only visible text
    options.each((__, o) => {
      const txt = ($(o).text() || '').trim();
      if (txt) $(o).replaceWith(txt); else $(o).remove();
    });
    unwrapKeepChildren($, el);
  });
  // Generic inputs/buttons are removed as noise unless containing meaningful text (rare)
  $scope.find('input, textarea, button, label').each((_, el) => {
    const $el = $(el);
    const txt = ($el.text() || $el.attr('value') || '').trim();
    if (txt.length === 0) $el.remove();
  });
}
function finalizeFormatting($: CheerioAPI, $scope: Cheerio<any>): string {
  compressBrRuns($, $scope); normalizeTextWhitespace($, $scope); removeCommentsDeep($, $scope); let html = $.html($scope[0]); html = ensureNewlinesBetweenBlocks(html); return html;
}

function removeEmptyBlocks($: CheerioAPI, $scope: Cheerio<any>) {
  const removable = new Set(['div','section','article','p','span','li']);
  const hasUsefulDesc = ($el: Cheerio<any>) => $el.find('a,img,table,thead,tbody,tr,td,th,dl,dt,dd,ul,ol,h1,h2,h3,h4,h5,h6').length > 0;
  $scope.find(Array.from(removable).join(',')).each((_, el) => {
    const $el = $(el);
    if (hasUsefulDesc($el)) return;
    const txt = ($el.text() || '').replace(/\u00A0|&nbsp;/g, ' ').replace(/[\t ]+/g, ' ').trim();
    if (txt.length === 0) $el.remove();
  });
}

export function cleanFacultyHtml(html: string, sourceUrl: string): string {
  const $ = load(html);
  const $scope = selectScope($);
  removeOrUnwrapNoise($, $scope); removeForcedTags($, $scope); absolutizeLinksAndImages($, $scope, sourceUrl); simplifyFormControls($, $scope); pruneEventAndStyle($, $scope); removeEmptyBlocks($, $scope);
  let out = finalizeFormatting($, $scope);
  if (out.length > 30000) { const $_ = load(out); const $scope2 = selectScope($_); clipToLimit($_, $scope2, 30000); out = ensureNewlinesBetweenBlocks($_.html($scope2[0])); }
  return out;
}

function decodeEntities(text: string): string {
  return text
    .replace(/&nbsp;/g,' ')
    .replace(/&amp;/g,'&')
    .replace(/&lt;/g,'<')
    .replace(/&gt;/g,'>')
    .replace(/&quot;/g,'"')
    .replace(/&#39;/g,"'")
    .replace(/&#(\d+);/g,(m,n)=>String.fromCharCode(parseInt(n,10)))
    .replace(/&#x([0-9a-fA-F]+);/g,(m,n)=>String.fromCharCode(parseInt(n,16)));
}

export function cleanFacultyText(html: string, sourceUrl: string): string {
  const $ = load(html);
  const $scope = selectScope($);
  // Same noise pruning as HTML mode
  removeOrUnwrapNoise($, $scope); removeForcedTags($, $scope); simplifyFormControls($, $scope);
  // Convert to intermediate HTML string
  let h = $.html($scope[0]) || '';
  // line breaks for br and common block closings
  h = h.replace(/<br\s*\/?/gi,'\n');
  const blockNames = Array.from(BLOCK_TAGS).join('|');
  const closeOpen = new RegExp(`</(?:${blockNames})>\\s*`, 'gi');
  h = h.replace(closeOpen, (m)=> '\n');
  // strip remaining tags preserving inner text
  h = h.replace(/<[^>]+>/g,' ');
  h = decodeEntities(h);
  // normalize whitespace
  h = h.replace(/\r\n?/g,'\n');
  h = h.replace(/[\t\v\f\u00A0]+/g,' ');
  h = h.replace(/ *\n+ */g,'\n');
  h = h.replace(/\n{3,}/g,'\n\n');
  h = h.replace(/ {2,}/g,' ');
  return h.trim();
}
