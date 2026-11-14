// Builds the visual material from HTML with Playwright.
//   node presentation/build.js            deck → presentation/paragraf.pdf, figures → docs/assets/*.jpg
//   node presentation/build.js --png      also slide previews in presentation/preview/ (ignored by git)
//   node presentation/build.js --shots    also screenshots of the running web chat (http://localhost:8000,
//                                         start it on the mock API, see CONTRIBUTING.md)
//   node presentation/build.js --video    also a one-minute demo video, docs/media/demo.mp4 (needs ffmpeg)
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

async function ask(page, question, typing = 0) {
  if (typing) await page.type('#input', question, { delay: typing });
  else await page.fill('#input', question);
  await page.evaluate(() => document.querySelector('#composer').requestSubmit());
  await page.waitForSelector('.reply:last-child .tools', { timeout: 60000 });
  await page.waitForTimeout(2600);            // let the words, footnotes and the document reader finish their entrance
}

const click = (page, selector) => page.evaluate((s) => document.querySelector(s).click(), selector);

async function shots(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1240, height: 800 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const save = async (name, dir = IMG) => {
    const file = path.join(dir, name);
    await page.screenshot({ path: file, type: 'jpeg', quality: 88 });
    console.log(path.relative(ROOT, file));
  };
  await page.goto(APP, { waitUntil: 'networkidle' });
  await page.mouse.move(820, 260);
  await page.waitForTimeout(1500);
  await ask(page, 'Почему по договору с ООО «Северный Склад» арендная плата 500 000 рублей в месяц?');
  await save('answer.jpg');
  await save('interface.jpg', ASSETS);
  await click(page, '#readerClose');
  await click(page, '.source');
  await page.waitForTimeout(1200);
  const proof = path.join(IMG, 'proof.jpg');                        // the sheet itself, large enough to read on a slide
  await page.locator('#sheet').screenshot({ path: proof, type: 'jpeg', quality: 90 });
  console.log(path.relative(ROOT, proof));
  await page.keyboard.press('Escape');
  await click(page, '[data-action=new]');
  await page.waitForTimeout(600);
  await ask(page, 'Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?');
  await save('temporal.jpg');
  await ctx.close();
}

/** A one-minute walk through the main scenario, recorded from the running web chat. */
async function video(browser) {
  const out = path.join(ROOT, 'docs', 'media');
  fs.mkdirSync(out, { recursive: true });
  const raw = fs.mkdtempSync(path.join(require('os').tmpdir(), 'paragraf-video-'));
  const size = { width: 1280, height: 800 };
  const ctx = await browser.newContext({ viewport: size, recordVideo: { dir: raw, size } });
  const page = await ctx.newPage();
  const glide = async (x, y, steps = 30) => page.mouse.move(x, y, { steps });
  await page.goto(APP, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1200);
  await glide(900, 200); await glide(380, 260); await glide(640, 240);     // the mascot follows the pointer
  await page.mouse.move(640, 190); await page.waitForTimeout(900);         // smiles on hover
  await page.mouse.down(); await page.mouse.up(); await page.waitForTimeout(1200);   // dots scatter and come back
  await glide(640, 560);
  await page.click('#input');
  await ask(page, 'Почему по договору с ООО «Северный Склад» арендная плата 500 000 рублей в месяц?', 45);
  await page.waitForTimeout(1500);
  const fn = await page.$('.fn');
  if (fn) { const b = await fn.boundingBox(); await glide(b.x + b.width / 2, b.y + b.height / 2, 25); await page.waitForTimeout(1800); }
  await glide(300, 700, 15);
  await click(page, '.source'); await page.waitForTimeout(3200);
  await page.keyboard.press('Escape'); await page.waitForTimeout(500);
  await click(page, '.seal'); await page.waitForTimeout(3000);
  await page.keyboard.press('Escape'); await page.waitForTimeout(500);
  await click(page, '[data-action=new]'); await page.waitForTimeout(900);
  await page.click('#input');
  await ask(page, 'Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?', 30);
  await page.waitForTimeout(3500);
  await click(page, '#readerClose');
  await click(page, '[data-action=new]'); await page.waitForTimeout(700);
  await page.click('#input');
  await ask(page, 'Какой размер пособия по беременности и родам?', 40);
  await page.waitForTimeout(3000);
  await click(page, '#navButton'); await page.waitForTimeout(1200);
  await click(page, '[data-action=docs]'); await page.waitForTimeout(2600);
  const video = page.video();
  await ctx.close();
  const webm = await video.path();
  const mp4 = path.join(out, 'demo.mp4');
  const run = require('child_process').spawnSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-preset', 'slow', '-crf', '26', '-movflags', '+faststart', '-an', mp4]);
  if (run.status !== 0) throw new Error('ffmpeg: ' + String(run.stderr));
  fs.rmSync(raw, { recursive: true, force: true });
  console.log(path.relative(ROOT, mp4));
}

(async () => {
  const browser = await chromium.launch();
  if (process.argv.includes('--shots')) await shots(browser);   // first: the deck embeds these screenshots
  if (process.argv.includes('--video')) await video(browser);
  await figures(browser);
  await deck(browser);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
