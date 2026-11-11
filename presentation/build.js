// Builds the visual material from HTML with Playwright.
//   node presentation/build.js            deck → presentation/paragraf.pdf, figures → docs/assets/*.png
//   node presentation/build.js --png      also slide previews in presentation/preview/ (ignored by git)
//   node presentation/build.js --shots    also screenshots of the running web chat (http://localhost:8000,
//                                         start it on the mock API, see CONTRIBUTING.md)
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

const DIR = __dirname;
const ROOT = path.join(DIR, '..');
const ASSETS = path.join(ROOT, 'docs', 'assets');
const IMG = path.join(DIR, 'img');
const APP = process.env.APP_URL || 'http://localhost:8000';

async function deck(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('file://' + path.join(DIR, 'deck.html'));
  await page.evaluate(() => document.fonts.ready);
  await page.pdf({ path: path.join(DIR, 'paragraf.pdf'), width: '1600px', height: '900px', printBackground: true });
  const count = await page.locator('.slide').count();
  if (process.argv.includes('--png')) {
    const out = path.join(DIR, 'preview');
    fs.mkdirSync(out, { recursive: true });
    for (let i = 0; i < count; i++) {
      await page.locator('.slide').nth(i).screenshot({ path: path.join(out, `${String(i + 1).padStart(2, '0')}.png`) });
    }
  }
  await page.close();
  console.log(`paragraf.pdf: ${count} slides`);
}

async function figures(browser) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(DIR, 'figures.html'));
  await page.evaluate(() => document.fonts.ready);
  for (const id of ['hero', 'how']) {
    const file = path.join(ASSETS, id === 'how' ? 'how-it-works.jpg' : 'hero.jpg');
    await page.locator('#' + id).screenshot({ path: file, type: 'jpeg', quality: 90 });
    console.log(path.relative(ROOT, file));
  }
  await page.close();
}

async function ask(page, question) {
  await page.fill('#input', question);
  await page.evaluate(() => document.querySelector('#composer').requestSubmit());
  await page.waitForSelector('.reply:last-child .facts', { timeout: 60000 });
  await page.waitForTimeout(2600);            // let the words and the document sheet finish their entrance
}

async function shots(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1240, height: 800 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const save = async (name, dir = IMG) => {
    const file = path.join(dir, name);
    await page.screenshot({ path: file, type: 'jpeg', quality: 88 });
    console.log(path.relative(ROOT, file));
  };
  await page.goto(APP, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1500);
  await ask(page, 'Почему по договору с ООО «Северный Склад» арендная плата 500 000 рублей в месяц?');
  await save('answer.jpg');
  await save('interface.jpg', ASSETS);
  await page.evaluate(() => document.querySelector('#newChat').click());
  await page.waitForTimeout(600);
  await ask(page, 'Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?');
  await save('temporal.jpg');
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch();
  if (process.argv.includes('--shots')) await shots(browser);   // first: the deck embeds these screenshots
  await figures(browser);
  await deck(browser);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
