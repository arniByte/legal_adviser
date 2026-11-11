// Builds presentation/paragraf.pdf from presentation/deck.html (needs Playwright with Chromium).
//   node presentation/build.js [--png]     --png also writes slide previews to presentation/preview/
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const dir = __dirname;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(dir, 'deck.html'));
  await page.waitForLoadState('networkidle');
  await page.pdf({ path: path.join(dir, 'paragraf.pdf'), width: '1280px', height: '720px', printBackground: true,
                   margin: { top: 0, right: 0, bottom: 0, left: 0 } });
  const count = await page.locator('.slide').count();
  if (process.argv.includes('--png')) {
    const out = path.join(dir, 'preview');
    fs.mkdirSync(out, { recursive: true });
    for (let i = 0; i < count; i++) {
      await page.locator('.slide').nth(i).screenshot({ path: path.join(out, `slide-${String(i + 1).padStart(2, '0')}.png`) });
    }
  }
  await browser.close();
  const size = fs.statSync(path.join(dir, 'paragraf.pdf')).size;
  console.log(`paragraf.pdf: ${count} slides, ${(size / 1024 / 1024).toFixed(1)} MB`);
})().catch((e) => { console.error(String(e)); process.exit(1); });
