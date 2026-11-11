// Renders presentation/paragraf.pdf from presentation/deck.html.
//   node presentation/build.js [--png]   (--png also writes slide previews to presentation/preview/)
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const dir = __dirname;
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('file://' + path.join(dir, 'deck.html'));
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({ path: path.join(dir, 'paragraf.pdf'), width: '1600px', height: '900px', printBackground: true });
  const count = await page.locator('.slide').count();
  if (process.argv.includes('--png')) {
    const out = path.join(dir, 'preview');
    fs.mkdirSync(out, { recursive: true });
    for (let i = 0; i < count; i++) {
      await page.locator('.slide').nth(i).screenshot({ path: path.join(out, `${String(i + 1).padStart(2, '0')}.png`) });
    }
  }
  await browser.close();
  console.log(`paragraf.pdf: ${count} slides`);
})().catch((e) => { console.error(e); process.exit(1); });
