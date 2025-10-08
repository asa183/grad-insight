import { cleanFacultyHtml } from '../src/index';

const BASE = 'https://example.com/base/dir/';

describe('cleanFacultyHtml (enhanced)', () => {
  test('keeps_anchors_and_imgs', () => {
    const input = `
      <main>
        <div>
          <a href="/people/taro">Taro</a>
          <img src="/img/taro.jpg" alt="taro" />
        </div>
      </main>`;
    const out = cleanFacultyHtml(input, BASE);
    expect(out).toMatch(/<a /);
    expect(out).toMatch(/<img /);
  });

  test('resolves_relative_urls', () => {
    const input = `
      <main>
        <a href="./person/a">A</a>
        <img src="../imgs/p.jpg">
        <a href="#same-card">frag</a>
        <a href="mailto:a@b">mail</a>
      </main>`;
    const out = cleanFacultyHtml(input, BASE);
    expect(out).toContain(`href="https://example.com/base/dir/person/a"`);
    expect(out).toContain(`src="https://example.com/base/imgs/p.jpg"`);
    expect(out).toContain(`href="#same-card"`);
    expect(out).toContain(`href="mailto:a@b"`);
  });

  test('lazy_image_promoted', () => {
    const input = `
      <main>
        <img data-src="/x.jpg">
      </main>`;
    const out = cleanFacultyHtml(input, BASE);
    expect(out).toContain(`src="https://example.com/x.jpg"`);
  });

  test('removes_noise_with_guard', () => {
    const input = `
      <main>
        <header>Global Header</header>
        <div class="cookie">
          <div class="wrap"><a href="/profile/abc">Profile</a></div>
        </div>
        <nav class="global-nav">n</nav>
        <div class="sidenav">side</div>
        <p>氏名: 山田 太郎</p>
        <footer>Footer</footer>
      </main>`;
    const out = cleanFacultyHtml(input, BASE);
    // wrapper removed but inner link preserved
    expect(out).toContain('href="https://example.com/profile/abc"');
    expect(out).not.toMatch(/<header|<footer|global-nav|sidenav/);
  });

  test('normalize_whitespace_and_breaks', () => {
    const input = `
      <main>
        <p>氏名：  山田\u3000太郎<br><br><br>専門:  AI\u00A0 Research</p>
      </main>`;
    const out = cleanFacultyHtml(input, BASE);
    // keep full-width space \u3000 and compress ASCII
    expect(out).toContain('氏名：\n 山田\u3000太郎');
    // single <br>
    expect((out.match(/<br\b/gi)?.length || 0)).toBe(1);
    // nbsp collapsed
    expect(out).toContain('AI Research');
  });

  test('idempotent', () => {
    const input = `
      <main>
        <div class="banner"><a href="/people/a"> A </a></div>
        <div><img data-src="/a.jpg"></div>
        <p>研究分野:  Systems</p>
      </main>`;
    const once = cleanFacultyHtml(input, BASE);
    const twice = cleanFacultyHtml(once, BASE);
    expect(twice).toBe(once);
  });

  test('site_hints_preserved', () => {
    const input = `
      <main>
        <h2>教員一覧</h2>
        <div class="side">noise</div>
        <ul>
          <li><a href="/r/lab/xyz">Lab</a></li>
        </ul>
      </main>`;
    const out = cleanFacultyHtml(input, 'https://www.agr.hokudai.ac.jp/r/faculty');
    expect(out).toMatch(/<h2[^>]*>教員一覧<\/h2>/);
    expect(out).toMatch(/href="https?:\/\/[^\"]*\/r\/lab\//);
  });
});

