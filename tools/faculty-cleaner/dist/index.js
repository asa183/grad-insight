import { load } from 'cheerio';
const NOISE_SELECTOR_LIST = [
    'header', 'nav', 'footer', 'aside', 'form[role="search"]',
    '.global-nav', '.site-header', '.site-footer', '.breadcrumb', '.breadcrumbs', '.pager', '.pagination',
    '.sns', '.share', '.social', '.skip-link', '.cookie', '.consent', '.gdpr', '.banner', '.ads', '.advertisement',
    '.newsletter', '.modal', '.popup', '.drawer', '.offcanvas',
];
const NOISE_CLASS_SUBSTR = [
    'global-nav', 'site-header', 'site-footer', 'breadcrumb', 'breadcrumbs', 'pager', 'pagination', 'sns', 'share', 'social',
    'skip-link', 'cookie', 'consent', 'gdpr', 'banner', 'ads', 'advert', 'newsletter', 'modal', 'popup', 'drawer', 'offcanvas'
];
const FORCE_REMOVE_TAGS = new Set(['script', 'style', 'noscript', 'template', 'iframe']);
const ROLE_WORDS_JA = ['教授', '准教授', '助教', '講師', '特任教授', '客員教授', '名誉教授', '非常勤講師', '招聘教授', '招へい教員'];
const ROLE_WORDS_EN = ['Professor', 'Associate Professor', 'Assistant Professor', 'Adjunct Professor', 'Visiting Professor', 'Professor Emeritus', 'Lecturer', 'Research Fellow', 'Researcher', 'Senior Researcher', 'Postdoctoral'];
const PERSON_LINK_HINTS = ['/faculty-member/', '/faculty/', '/people/', '/person/', '/profile', '/profiles', '/researcher', '/researchers', '/staff/', '/r/lab/'];
const LABEL_WORDS = ['氏名', '専門', '研究分野', 'Research field', 'Field(s)'];
const BLOCK_TAGS = new Set(['section', 'article', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td', 'th', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'dl', 'dt', 'dd', 'hr']);
function hasDescendantAnchorWithProfile($, $node) {
    let ok = false;
    $node.find('a').each((_, a) => {
        const href = ($(a).attr('href') || '').toLowerCase();
        const txt = $(a).text().trim().toLowerCase();
        if (href) {
            if (PERSON_LINK_HINTS.some(h => href.includes(h))) {
                ok = true;
                return false;
            }
        }
        if (/(^|\s)(hp|ホームページ|研究者総覧|総覧|profile|profiles?|people|researchers?|staff)(\s|$)/i.test(txt)) {
            ok = true;
            return false;
        }
        return;
    });
    return ok;
}
function hasNameOrRoleClues($, $node) {
    const text = $node.text();
    const kanjiName = /[\p{sc=Han}]{2,}\s+[\p{sc=Han}]{1,}/u;
    const kataName = /[\p{sc=Katakana}・ー]{2,}(\s+[\p{sc=Katakana}・ー]{2,})?/u;
    const roles = new RegExp('(' + [...ROLE_WORDS_JA, ...ROLE_WORDS_EN].map(escapeRegExp).join('|') + ')', 'i');
    return kanjiName.test(text) || kataName.test(text) || roles.test(text);
}
function escapeRegExp(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
function isFragmentOnly(u) { return /^#/.test(u); }
function isSpecialScheme(u) { return /^(mailto:|tel:|javascript:)/i.test(u); }
function absolutizeUrl(url, base) {
    if (!url)
        return url;
    if (isFragmentOnly(url) || isSpecialScheme(url))
        return url;
    try {
        return new URL(url, base).toString();
    }
    catch {
        return url;
    }
}
function takeFirstFromSrcset(srcset) {
    if (!srcset)
        return undefined;
    const first = srcset.split(',')[0]?.trim();
    if (!first)
        return undefined;
    return first.split(/\s+/)[0];
}
function unwrapKeepChildren($, el) { const $el = $(el); const children = $el.contents(); $el.replaceWith(children); }
function removeCommentsDeep($, $root) {
    const walk = (node) => {
        const any = node;
        if (any.type === 'comment' && any.parent) {
            $(any).remove();
            return;
        }
        if (any.children) {
            for (const child of any.children)
                walk(child);
        }
    };
    $root.each((_, el) => walk(el));
}
function cleanAttributes($, $root) {
    $root.find('*').each((_, el) => {
        const attribs = el.attribs || {};
        for (const name of Object.keys(attribs)) {
            const lower = name.toLowerCase();
            if (lower === 'style' || lower.startsWith('on'))
                $(el).removeAttr(name);
        }
    });
}
function compressBrRuns($, $root) {
    $root.find('br').each((_, el) => {
        const prev = el.prev;
        if (prev && prev.tagName === 'br')
            $(el).remove();
    });
}
function normalizeTextWhitespace($, $root) {
    const walker = (node) => {
        const any = node;
        if (any.type === 'text') {
            let data = any.data ?? '';
            data = data.replace(/\u00A0|&nbsp;/g, ' ');
            data = data.replace(/[\t ]{2,}/g, ' ');
            data = data.replace(/\n{2,}/g, '\n');
            for (const label of LABEL_WORDS) {
                const safe = escapeRegExp(label);
                const reWithColon = new RegExp(`(${safe})([：:])(?!\n)`, 'g');
                data = data.replace(reWithColon, `$1$2\n`);
                const reNoColon = new RegExp(`(${safe})(?![\n：:])`, 'g');
                data = data.replace(reNoColon, `$1\n`);
            }
            any.data = data;
            return;
        }
        if (any.children) {
            for (const child of any.children)
                walker(child);
        }
    };
    $root.each((_, el) => walker(el));
}
function ensureNewlinesBetweenBlocks(html) {
    const blockNames = Array.from(BLOCK_TAGS).join('|');
    const closeOpen = new RegExp(`</(?:${blockNames})>\s*(?=<(?:${blockNames})(?:\s|>))`, 'gi');
    return html.replace(closeOpen, m => m.replace(/>\s*$/, '>' + '\n'));
}
function clipToLimit($, $root, limit) {
    let out = $.html($root[0]);
    if (out.length <= limit)
        return;
    const texts = [];
    const collect = (node) => { const any = node; if (any.type === 'text')
        texts.push(any); if (any.children)
        for (const c of any.children)
            collect(c); };
    collect($root[0]);
    let iterations = 0;
    while (out.length > limit && iterations < texts.length + 5) {
        let need = out.length - limit;
        for (let i = texts.length - 1; i >= 0 && need > 0; i--) {
            const tn = texts[i];
            const s = tn.data || '';
            if (!s)
                continue;
            const cut = Math.min(s.length, Math.max(16, Math.ceil(need / 2)));
            tn.data = s.slice(0, s.length - cut).trimEnd();
            need -= cut;
        }
        out = $.html($root[0]);
        iterations++;
    }
}
function selectScope($) {
    const $main = $('main').first();
    if ($main.length)
        return $main;
    const $body = $('body').first();
    if ($body.length)
        return $body;
    const $wrapper = $('<main></main>');
    $wrapper.append($.root().children());
    $.root().append($wrapper);
    return $wrapper;
}
function removeForcedTags($, $scope) { $scope.find(Array.from(FORCE_REMOVE_TAGS).join(',')).remove(); }
function removeOrUnwrapNoise($, $scope) {
    const $candidates = $scope.find(NOISE_SELECTOR_LIST.join(','));
    $candidates.each((_, el) => { const $el = $(el); const keep = hasDescendantAnchorWithProfile($, $el) || hasNameOrRoleClues($, $el); if (keep) {
        unwrapKeepChildren($, el);
    }
    else {
        $el.remove();
    } });
    $scope.find('[class]').each((_, el) => {
        const $el = $(el);
        const cls = ($el.attr('class') || '').toLowerCase();
        if (!cls)
            return;
        if (!NOISE_CLASS_SUBSTR.some(key => cls.includes(key)))
            return;
        const keep = hasDescendantAnchorWithProfile($, $el) || hasNameOrRoleClues($, $el);
        if (keep) {
            unwrapKeepChildren($, el);
        }
        else {
            $el.remove();
        }
    });
}
function absolutizeLinksAndImages($, $scope, baseUrl) {
    $scope.find('a[href]').each((_, a) => { const href = $(a).attr('href'); if (!href)
        return; $(a).attr('href', absolutizeUrl(href, baseUrl)); });
    $scope.find('img').each((_, img) => {
        const $img = $(img);
        let src = $img.attr('src') || '';
        if (!src || src.trim() === '') {
            const cand = $img.attr('data-src') || $img.attr('data-original') || $img.attr('data-lazy') || takeFirstFromSrcset($img.attr('data-srcset')) || takeFirstFromSrcset($img.attr('srcset')) || '';
            if (cand) {
                $img.attr('src', cand);
                src = cand;
            }
        }
        if (src) {
            $img.attr('src', absolutizeUrl(src, baseUrl));
        }
    });
}
function pruneEventAndStyle($, $scope) { cleanAttributes($, $scope); }
function finalizeFormatting($, $scope) {
    compressBrRuns($, $scope);
    normalizeTextWhitespace($, $scope);
    removeCommentsDeep($, $scope);
    let html = $.html($scope[0]);
    html = ensureNewlinesBetweenBlocks(html);
    return html;
}
export function cleanFacultyHtml(html, sourceUrl) {
    const $ = load(html);
    const $scope = selectScope($);
    removeOrUnwrapNoise($, $scope);
    removeForcedTags($, $scope);
    absolutizeLinksAndImages($, $scope, sourceUrl);
    pruneEventAndStyle($, $scope);
    let out = finalizeFormatting($, $scope);
    if (out.length > 30000) {
        const $_ = load(out);
        const $scope2 = selectScope($_);
        clipToLimit($_, $scope2, 30000);
        out = ensureNewlinesBetweenBlocks($_.html($scope2[0]));
    }
    return out;
}
