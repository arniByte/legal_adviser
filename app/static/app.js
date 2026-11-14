/* Параграф — web client. Plain JavaScript, no build step. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const icon = (d) => `<svg viewBox="0 0 20 20" aria-hidden="true">${d}</svg>`;
  const ICON = {
    check: icon('<path d="M5 10.5l3.2 3.2L15 7"/>'),
    warn: icon('<path d="M10 4.5l6.5 11h-13z"/><path d="M10 9v3M10 14v.01"/>'),
    info: icon('<circle cx="10" cy="10" r="6.5"/><path d="M10 9.2v4M10 6.6v.01"/>'),
    chev: icon('<path d="M8 5.5l4.5 4.5L8 14.5"/>'),
    arrow: icon('<path d="M4.5 10h11M11 5.5l4.5 4.5-4.5 4.5"/>'),
    route: icon('<circle cx="5.5" cy="14.5" r="1.8"/><circle cx="14.5" cy="5.5" r="1.8"/><path d="M7.3 14.5h4.2a3 3 0 0 0 0-6h-3a3 3 0 0 1 0-6h4.2"/>'),
    copy: icon('<rect x="7" y="7" width="9" height="9" rx="2.2"/><path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4h-6A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7"/>'),
    open: icon('<path d="M11 4.5h4.5V9M15.5 4.5 9 11M13.5 11.5v3a1 1 0 0 1-1 1h-7a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3"/>'),
  };
  const STAGES = { retrieval: "Ищу в документах", generation: "Формулирую ответ", second_pass: "Ищу шире", verification: "Сверяю с текстом", repair: "Уточняю ответ", edit: "Убираю лишнее" };
  const STEP_NAMES = { retrieval: "Поиск по базе", generation: "Ответ модели", second_pass: "Расширенный поиск", verification: "Сверка с текстом", repair: "Исправление", edit: "Редактура" };
  const STATE_TEXT = { PARTIAL: "Подтверждено частично", NOT_IN_KB: "В базе нет сведений", OFF_TOPIC: "Вне юридической темы", ILLEGAL: "С этим не помогу", ROLE: "Правила не меняются", ERROR: "Техническая ошибка" };
  const HINTS = ["про аренду", "про срок оплаты", "про испытательный срок", "про ставку НДС", "про согласование договора", "про персональные данные"];

  const P = window.Paragraf;
  const state = { messages: [], docs: [], byId: {}, busy: false, apiReady: false, starters: [], startersAt: 0, cat: "" };
  const input = $("input");
  input.placeholder = " ";                      // lets CSS hide the animated hint once something is typed
  const fmt = (x) => x.toFixed(1).replace(".", ",");
  const plural = (n, one, few, many) => {
    const a = n % 10, b = n % 100;
    return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
  };

  // ---------------------------------------------------------------- living parts
  const hero = P.auraMark($("heroMark"), window.innerWidth < 640 ? 132 : 168, { palette: "soft" });
  const navDots = el("canvas", "mark");
  $("navMark").append(navDots);
  P.mark(navDots, { size: 26, eyes: false });
  P.aura($("navGlow"), { palette: "psb", round: true, bands: 0, fps: 12, seed: 3 });

  /** Words leave in a blur and new ones come out of it. */
  function cycle(node, text) {
    node.classList.add("out");
    setTimeout(() => {
      node.classList.remove("out");
      node.innerHTML = "";
      text.split(" ").forEach((w, i) => {
        const s = el("span", "w", w);
        s.style.animationDelay = i * 35 + "ms";
        node.append(s, document.createTextNode(" "));
      });
    }, node.childNodes.length ? 400 : 0);
  }
  let hintIndex = 0;
  cycle($("hintCycle"), HINTS[0]);
  setInterval(() => {
    if (document.hidden || input.value) return;
    hintIndex = (hintIndex + 1) % HINTS.length;
    cycle($("hintCycle"), HINTS[hintIndex]);
  }, 3200);

  // ---------------------------------------------------------------- boot
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }

  async function boot() {
    try {
      const [health, docs, examples] = await Promise.all([getJSON("/api/health"), getJSON("/api/documents"), getJSON("/api/examples")]);
      state.apiReady = health.api_configured;
      state.docs = docs.documents;
      state.docs.forEach((d) => { state.byId[d.id] = d; });
      if (!state.apiReady) notice("Модель не подключена: укажи LLM_BASE_URL и LLM_API_KEY в файле .env и перезапусти сервис. Документы базы доступны в меню слева от поля.");
      state.starters = examples.suggestions || [];
      renderStarters();
      renderLibrary();
    } catch (e) {
      notice("Сервис недоступен. Проверь, что контейнер запущен.");
    }
    input.focus();
  }

  function notice(text) { const n = $("notice"); n.textContent = text; n.hidden = false; }

  function renderStarters() {
    const box = $("starters");
    const items = state.starters;
    if (!items.length) { $("shuffle").hidden = true; return; }
    const pick = [];
    for (let k = 0; k < Math.min(4, items.length); k++) pick.push(items[(state.startersAt + k) % items.length]);
    box.innerHTML = "";
    pick.forEach((s, j) => {
      const b = el("button", "chip", s.label);
      b.type = "button";
      b.style.setProperty("--j", j);
      b.addEventListener("click", () => send(s.question));
      box.append(b);
    });
    $("shuffle").hidden = items.length <= 4;
  }
  $("shuffle").addEventListener("click", () => {
    const box = $("starters");
    box.classList.add("out");
    setTimeout(() => {
      box.classList.remove("out");
      state.startersAt = (state.startersAt + 4) % state.starters.length;
      renderStarters();
    }, 300);
  });

  // ---------------------------------------------------------------- composer
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 176) + "px"; }
  const syncSend = () => { $("send").disabled = !input.value.trim() || state.busy; };
  input.addEventListener("input", () => { autosize(); syncSend(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  syncSend();

  function reset() {
    if (state.busy) return;
    state.messages = [];
    $("thread").innerHTML = "";
    document.body.classList.remove("chatting");
    closeReader();
    input.value = ""; autosize(); syncSend(); input.focus();
    window.scrollTo({ top: 0 });
  }

  // ---------------------------------------------------------------- menu
  const menu = $("menu"), navButton = $("navButton");
  function toggleMenu(open, byKeyboard) {
    const show = open === undefined ? menu.hidden : open;
    menu.hidden = !show;
    navButton.setAttribute("aria-expanded", String(show));
    if (show && byKeyboard) menu.querySelector(".menu__item").focus();      // a mouse click does not light up the first item
  }
  navButton.addEventListener("click", (e) => toggleMenu(undefined, e.detail === 0));
  document.addEventListener("pointerdown", (e) => { if (!menu.hidden && !menu.contains(e.target) && !navButton.contains(e.target)) toggleMenu(false); });
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll(".menu__item")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); items[(i + 1) % items.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
    if (e.key === "Home") { e.preventDefault(); items[0].focus(); }
    if (e.key === "End") { e.preventDefault(); items[items.length - 1].focus(); }
  });
  menu.addEventListener("click", (e) => {
    const item = e.target.closest(".menu__item");
    if (!item) return;
    toggleMenu(false);
    if (item.dataset.action === "new") reset();
    if (item.dataset.action === "docs") openLibrary();
    if (item.dataset.action === "about") openAbout();
  });

  // ---------------------------------------------------------------- conversation
  const thread = $("thread");
  const scrollDown = () => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: "smooth" });

  function addUser(text) { thread.append(el("div", "msg-user", text)); }

  function addReply() {
    const m = el("div", "reply");
    const think = el("div", "thinking");
    const dot = el("span");
    const mk = P.auraMark(dot, 40, { palette: "psb", eyes: false, seed: thread.children.length });
    mk.mood("thinking");
    const line = el("span", "cycle");
    think.append(dot, line);
    m.append(think);
    thread.append(m);
    cycle(line, STAGES.retrieval);
    return { m, mk, stage: (s) => { if (STAGES[s]) cycle(line, STAGES[s]); } };
  }

  async function send(raw) {
    const text = (raw || "").trim();
    if (!text || state.busy) return;
    if (!state.apiReady) { notice("Модель не подключена: укажи LLM_BASE_URL и LLM_API_KEY в файле .env и перезапусти сервис."); return; }
    state.busy = true;
    document.body.classList.add("chatting", "busy");
    hero.mood("thinking");
    syncSend();
    input.value = ""; autosize();
    state.messages.push({ role: "user", content: text });
    addUser(text);
    const reply = addReply();
    scrollDown();
    try {
      const result = await stream(state.messages, reply.stage);
      state.messages.push({ role: "assistant", content: result.answer });
      render(reply, result);
    } catch (err) {
      state.messages.pop();
      reply.mk.stop();
      reply.m.innerHTML = "";
      reply.m.append(el("div", "state ERROR", STATE_TEXT.ERROR),
        el("div", "rich", "Не получилось ответить: " + (err.message || err) + ". Отправь вопрос ещё раз."));
      input.value = text; autosize();
    } finally {
      state.busy = false;
      hero.mood("idle");
      document.body.classList.remove("busy");
      syncSend();
      scrollDown(); input.focus();
    }
  }

  async function stream(messages, onStage) {
    const resp = await fetch("/api/chat/stream", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });
    if (!resp.ok) {
      let detail = "ошибка " + resp.status;
      try { detail = (await resp.json()).detail || detail; } catch (e) { /* not json */ }
      throw new Error(detail);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "", result = null;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, cut); buffer = buffer.slice(cut + 2);
        const ev = (block.match(/^event: (.*)$/m) || [])[1];
        const data = (block.match(/^data: (.*)$/m) || [])[1];
        if (!ev || !data) continue;
        const payload = JSON.parse(data);
        if (ev === "stage") onStage(payload.stage);
        else if (ev === "result") result = payload;
        else if (ev === "error") throw new Error(payload.detail);
      }
    }
    if (!result) throw new Error("соединение прервано");
    return result;
  }

  /** Where each source is mentioned in the answer: footnote numbers go right after the mention. */
  function footnotes(answer, sources) {
    const marks = [];
    sources.forEach((s, i) => {
      if (!s.locator) return;
      let at = answer.indexOf(s.locator);
      while (at >= 0 && marks.some((m) => m.at === at + s.locator.length)) at = answer.indexOf(s.locator, at + 1);
      if (at >= 0) marks.push({ at: at + s.locator.length, n: i + 1, s });
    });
    return marks;
  }

  function render(reply, r) {
    reply.mk.stop();
    const box = reply.m;
    box.innerHTML = "";
    if (STATE_TEXT[r.status]) box.append(el("div", "state " + r.status, STATE_TEXT[r.status]));

    // the answer comes out of a blur word by word, footnotes pop in after their mentions
    const text = el("div", "rich");
    const marks = footnotes(r.answer, r.sources);
    const re = /\S+|\s+/g;
    let m, k = 0;
    while ((m = re.exec(r.answer))) {
      const token = m[0];
      if (!token.trim()) { text.append(document.createTextNode(token)); continue; }
      const w = el("span", "w", token);
      const delay = Math.min(k * 0.022, 1.6);
      w.style.animationDelay = delay + "s";
      k += 1;
      text.append(w);
      marks.filter((x) => x.at > m.index && x.at <= m.index + token.length).forEach((x) => {
        const fn = el("button", "fn", String(x.n));
        fn.type = "button";
        fn.setAttribute("aria-label", "Источник " + x.n);
        fn.style.animationDelay = delay + 0.15 + "s";
        fn.addEventListener("click", () => openProof(r, x.s, x.n));
        hoverQuote(fn, x.s);
        text.append(fn);
      });
    }
    box.append(text);
    const settle = Math.min(k * 0.022, 1.6);

    if (r.extra) {
      const more = el("details", "more");
      const sum = el("summary"); sum.innerHTML = ICON.chev + "<span>Ещё по теме</span>";
      more.append(sum, el("div", "more__body", r.extra));
      more.style.animation = `rise .4s var(--ease) ${settle}s backwards`;
      box.append(more);
    }

    if (r.sources.length) {
      const list = el("div", "sources");
      r.sources.forEach((s, i) => {
        const b = el("button", "source" + (s.ok ? "" : " bad"));
        b.type = "button";
        b.style.animationDelay = settle + 0.1 + i * 0.06 + "s";
        const fn = el("span", "fn", String(i + 1));
        const label = el("span");
        label.append(el("b", "", s.locator || "документ"), document.createTextNode(" · " + s.title));
        b.append(fn, label);
        b.insertAdjacentHTML("beforeend", ICON.arrow.replace("<svg", '<svg class="arrow"'));
        b.addEventListener("click", () => openProof(r, s, i + 1));
        hoverQuote(b, s);
        list.append(b);
      });
      box.append(list);
    }

    const tools = el("div", "tools");
    tools.style.animationDelay = settle + 0.2 + "s";
    const c = r.checks;
    if (r.status !== "OFF_TOPIC" && r.status !== "ROLE") {
      const seal = el("button", "btn btn--glass btn--small seal" + (c.verified ? "" : " warn"));
      seal.type = "button";
      seal.innerHTML = (c.verified ? ICON.check : ICON.warn) + `<span>${c.verified ? "Сверено с документами" : "Проверь ответ"}</span>`;
      seal.addEventListener("click", () => openCheck(r));
      tools.append(seal);
    }
    const how = el("button", "btn btn--glass btn--icon"); how.type = "button"; how.innerHTML = ICON.route; how.setAttribute("aria-label", "Как получен ответ");
    tip(how, "Как получен ответ");
    how.addEventListener("click", () => openTrace(r));
    const copy = el("button", "btn btn--glass btn--icon"); copy.type = "button"; copy.innerHTML = ICON.copy; copy.setAttribute("aria-label", "Скопировать ответ");
    tip(copy, "Скопировать ответ");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(r.answer); copy.innerHTML = ICON.check; setTimeout(() => { copy.innerHTML = ICON.copy; }, 1400); } catch (e) { /* clipboard blocked */ }
    });
    tools.append(how, copy);
    box.append(tools);

    const first = r.sources.find((s) => s.ok && s.start !== null);
    if (first && window.innerWidth >= 1200) setTimeout(() => openDoc(first.doc_id, r.sources, r.sources.indexOf(first)), 900);
  }

  // ---------------------------------------------------------------- popovers
  const pop = $("pop");
  let popTimer = null;
  function showPop(anchor, content) {
    clearTimeout(popTimer);
    pop.innerHTML = ""; pop.append(content); pop.hidden = false;
    const a = anchor.getBoundingClientRect();
    const w = pop.offsetWidth, h = pop.offsetHeight;
    const left = Math.min(Math.max(12, a.left + a.width / 2 - w / 2), window.innerWidth - w - 12);
    let top = a.top - h - 10;
    if (top < 12) top = a.bottom + 10;
    pop.style.left = left + "px"; pop.style.top = top + "px";
    requestAnimationFrame(() => pop.classList.add("on"));
  }
  function hidePop() { pop.classList.remove("on"); popTimer = setTimeout(() => { pop.hidden = true; }, 260); }
  function hover(node, build) {
    if (!window.matchMedia("(hover: hover)").matches) return;
    node.addEventListener("mouseenter", () => showPop(node, build()));
    node.addEventListener("mouseleave", hidePop);
    node.addEventListener("click", hidePop);
  }
  function tip(node, text) { hover(node, () => el("div", "", text)); }
  function hoverQuote(node, s) {
    hover(node, () => {
      const b = el("div");
      b.append(el("div", "t", s.title + (s.locator ? " · " + s.locator : "")));
      if (s.quote) b.append(el("div", "q", "«" + s.quote + "»"));
      return b;
    });
  }

  // ---------------------------------------------------------------- sheet: proof, check, trace, about
  const backdrop = $("sheetBackdrop");
  let lastFocus = null;
  function openSheet(kind, title, body) {
    lastFocus = document.activeElement;
    $("sheetKind").textContent = kind;
    $("sheetTitle").textContent = title;
    const host = $("sheetBody"); host.innerHTML = ""; host.append(body);
    backdrop.hidden = false;
    document.documentElement.style.overflow = "hidden";
    const sheet = $("sheet");
    sheet.style.animation = "none"; void sheet.offsetHeight; sheet.style.animation = "";
    $("sheetClose").focus({ focusVisible: false });
  }
  function closeSheet() {
    if (backdrop.hidden) return;
    backdrop.hidden = true;
    document.documentElement.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }
  $("sheetClose").addEventListener("click", closeSheet);
  backdrop.addEventListener("pointerdown", (e) => { if (e.target === backdrop) closeSheet(); });
  [backdrop, $("library")].forEach((b) => b.addEventListener("keydown", (e) => {       // keep the focus inside
    if (e.key !== "Tab") return;
    const f = [...b.querySelectorAll("button, input, [tabindex]")].filter((n) => !n.hidden && n.offsetParent);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  }));

  function step(ok, title, sub) {
    const s = el("div", "sheet__step");
    s.dataset.state = ok === true ? "ok" : ok === false ? "warn" : "info";
    const tick = el("span", "sheet__tick"); tick.innerHTML = ok === true ? ICON.check : ok === false ? ICON.warn : ICON.info;
    const t = el("span"); t.append(el("span", "sheet__step-title", title));
    if (sub) t.append(el("span", "caption", sub));
    s.append(tick, t);
    return s;
  }

  function openProof(r, s, n) {
    const doc = state.byId[s.doc_id] || {};
    const body = document.createDocumentFragment();
    if (s.quote) body.append(el("blockquote", "sheet__quote", "«" + s.quote + "»"));
    const link = el("button", "sheet__link"); link.type = "button";
    link.innerHTML = `<span>${(doc.full_title || s.title)}</span>` + ICON.open;
    link.addEventListener("click", () => { closeSheet(); openDoc(s.doc_id, r.sources, n - 1); });
    body.append(link);
    const steps = el("div", "sheet__steps");
    steps.append(s.ok ? step(true, "Положение найдено в документе", (s.locator ? s.locator + " · " : "") + s.title)
      : step(false, "Ссылка не подтверждена", "Такого положения в тексте документа нет — ответ стоит перепроверить"));
    if (doc.state === "lapsed") steps.append(step(null, "Редакция утратила силу", "Применена, потому что вопрос о событии в прошлом"));
    else if (doc.status) steps.append(step(true, doc.category === "law" ? "Норма из действующей редакции" : "Документ действует", doc.status.charAt(0).toUpperCase() + doc.status.slice(1)));
    const c = r.checks;
    if (c.numbers_checked) steps.append(step(c.verified, c.verified ? "Числа и даты ответа сверены" : "Не всё в ответе подтверждено",
      `${c.numbers_checked} ${plural(c.numbers_checked, "значение найдено", "значения найдены", "значений найдено")} в тексте документов`));
    body.append(steps);
    body.append(el("p", "sheet__verdict", "Сверку делает код, а не вторая модель: число, дата или пункт либо есть в тексте документа, либо ответ не проходит проверку."));
    openSheet("Пруф источника " + n, (s.locator ? s.locator + " · " : "") + s.title, body);
  }

  function openCheck(r) {
    const c = r.checks;
    const body = document.createDocumentFragment();
    const steps = el("div", "sheet__steps");
    steps.style.borderTop = "0"; steps.style.paddingTop = "0";
    if (c.sources_total) steps.append(step(c.sources_ok === c.sources_total, "Ссылки на положения", `${c.sources_ok} из ${c.sources_total} найдены в документах`));
    steps.append(step(c.verified, c.verified ? "Числа, даты и названия сверены" : "Есть неподтверждённое",
      c.numbers_checked ? `${c.numbers_checked} ${plural(c.numbers_checked, "значение", "значения", "значений")} проверено по тексту` : "Чисел в ответе нет"));
    if (c.repaired) steps.append(step(null, "Ответ исправлен после проверки", "Модель получила замечания и убрала неподтверждённое"));
    const t = (r.trace && r.trace.timings) || {};
    if (t.verification !== undefined) steps.append(step(null, "Проверка заняла " + Math.max(1, Math.round(t.verification * 1000)) + " мс", "Без второй модели — только код и текст документов"));
    body.append(steps);
    if (!c.verified && c.hard_issues.length) {
      body.append(el("h3", "", "Что не подтвердилось"));
      const ul = el("ul", "list"); c.hard_issues.forEach((x) => ul.append(el("li", "", x))); body.append(ul);
    }
    body.append(el("p", "sheet__verdict", "Сверка ловит неподтверждённые числа, даты, названия и ссылки. Логику вывода она не проверяет — решение остаётся за юристом."));
    openSheet("Сверка ответа", c.verified ? "Каждый факт найден в документах" : "Не всё подтверждено документами", body);
  }

  function openTrace(r) {
    const body = document.createDocumentFragment();
    if (r.trace.context.length) {
      body.append(el("h3", "", "Что видела модель"));
      const ctx = el("div", "ctx");
      r.trace.context.forEach((x) => {
        const b = el("button", "chip", x.title + (x.full ? "" : " · фрагменты"));
        b.type = "button"; b.addEventListener("click", () => { closeSheet(); openDoc(x.id, r.sources); });
        ctx.append(b);
      });
      body.append(ctx);
    }
    if (r.trace.hints.length) {
      body.append(el("h3", "", "Что подсказал реестр документов"));
      const ul = el("ul", "list"); r.trace.hints.forEach((h) => ul.append(el("li", "", h))); body.append(ul);
    }
    const t = r.trace.timings || {};
    const keys = ["retrieval", "generation", "second_pass", "verification", "repair", "edit"].filter((k) => t[k] !== undefined);
    if (keys.length) {
      body.append(el("h3", "", "Время"));
      const max = Math.max(...keys.map((k) => t[k]), 0.01);
      keys.forEach((k, i) => {
        const line = el("div", "timing");
        const bar = el("i"); bar.style.width = Math.max(3, (t[k] / max) * 100) + "%"; bar.style.animationDelay = i * 0.08 + "s";
        const track = el("span"); track.append(bar);
        line.append(el("span", "", STEP_NAMES[k] || k), track, el("b", "", fmt(t[k]) + " с"));
        body.append(line);
      });
    }
    openSheet("Как получен ответ", "Путь от вопроса к ответу", body);
  }

  function openAbout() {
    const body = document.createDocumentFragment();
    const ol = el("ol", "steps-about");
    [["Вопрос", "Код определяет контрагента, договор, дату события и редакцию, которая тогда действовала."],
     ["Поиск", "Нужные положения в базе: по словам, по смыслу и с перепроверкой порядка."],
     ["Ответ", "Модель отвечает только по найденному тексту и называет пункт. Нет в базе — так и скажет."],
     ["Сверка", "Каждое число, дату, название и цитату программа ищет в документах. Лишнее вычёркивается."]]
      .forEach(([b, s]) => { const li = el("li"); const d = el("div"); d.append(el("b", "", b), el("span", "", s)); li.append(d); ol.append(li); });
    body.append(ol);
    body.append(el("p", "sheet__verdict", "Параграф даёт справку со ссылками, а не юридическое заключение: решение принимает юрист."));
    openSheet("Параграф", "Как получается ответ", body);
  }

  // ---------------------------------------------------------------- reader: full text of a document
  const docCache = {};
  async function openDoc(id, sources, focusIndex) {
    let doc = docCache[id];
    if (!doc) { try { doc = docCache[id] = await getJSON("/api/documents/" + id); } catch (e) { return; } }
    const list = sources || [];
    const cites = list.filter((s) => s.doc_id === id && s.start !== null && s.end !== null);
    const focus = focusIndex !== undefined && list[focusIndex] && list[focusIndex].doc_id === id ? list[focusIndex] : cites[0];
    $("readerKind").textContent = doc.category === "law" ? "Закон" : "Документ компании";
    $("readerTitle").textContent = doc.full_title;
    const st = $("readerStatus"); st.innerHTML = "";
    if (doc.state === "lapsed") st.append(el("span", "lapsed", "Утратил силу. "));
    if (doc.status) st.append(document.createTextNode(doc.status.charAt(0).toUpperCase() + doc.status.slice(1) + ". "));
    if (doc.removed.length) st.append(el("span", "guard", "Встроенная инструкция для ИИ исключена."));
    const jumps = $("readerJumps"); jumps.innerHTML = "";
    cites.forEach((s) => {
      const b = el("button", "chip" + (s === focus ? " on" : ""), s.locator || "фрагмент");
      b.type = "button";
      b.addEventListener("click", () => openDoc(id, list, list.indexOf(s)));
      jumps.append(b);
    });
    renderDoc(doc, cites, focus);
    const reader = $("reader");
    const wasOpen = !reader.hidden;
    reader.hidden = false;
    if (!wasOpen) { reader.style.animation = "none"; void reader.offsetHeight; reader.style.animation = ""; }
    document.body.classList.add("reading");
    if (!$("library").hidden) closeLibrary();
    requestAnimationFrame(() => {
      const target = $("readerDoc").querySelector("mark.focus") || $("readerDoc").querySelector("mark");
      if (target) target.scrollIntoView({ block: "center", behavior: wasOpen ? "smooth" : "auto" });
      else $("readerDoc").scrollTop = 0;
    });
  }

  function renderDoc(doc, cites, focus) {
    const body = $("readerDoc"); body.innerHTML = "";
    const notes = new Set();
    doc.units.forEach((u) => u.notes.forEach((n) => notes.add(n.trim())));
    (doc.doc_notes || []).forEach((n) => notes.add(n.trim()));
    const unitAt = {};
    doc.units.forEach((u) => { unitAt[u.start] = u; });
    const ranges = cites.map((s) => ({ a: s.start, b: s.end, focus: s === focus }));
    let pos = 0;
    for (const line of doc.text.split("\n")) {
      const start = pos, end = pos + line.length; pos = end + 1;
      const trimmed = line.trim();
      if (!trimmed || /^(Источник|URL|Дата выгрузки):/.test(trimmed)) continue;
      const unit = unitAt[start + (line.length - line.trimStart().length)] || unitAt[start];
      const p = el("div", "ln");
      if (trimmed.startsWith("[Строка исключена системой")) {
        p.classList.add("removed");
        p.textContent = "Здесь была строка с инструкцией для ИИ-ассистента. Это не положение документа, она исключена при загрузке базы.";
        body.append(p);
        continue;
      }
      if (notes.has(trimmed)) p.classList.add("note");
      else if (unit && (unit.kind === "section" || unit.kind === "head")) p.classList.add(unit.kind);
      const hits = ranges.filter((r) => r.a < end && r.b > start);
      if (!hits.length) p.append(document.createTextNode(line));
      else {
        let cur = start;
        for (const h of hits) {
          const a = Math.max(h.a, start), b = Math.min(h.b, end);
          if (a > cur) p.append(document.createTextNode(doc.text.slice(cur, a)));
          if (b > Math.max(a, cur)) p.append(el("mark", h.focus ? "focus" : "", doc.text.slice(Math.max(a, cur), b)));
          cur = Math.max(cur, b);
        }
        if (cur < end) p.append(document.createTextNode(doc.text.slice(cur, end)));
      }
      body.append(p);
    }
  }

  function closeReader() { $("reader").hidden = true; document.body.classList.remove("reading"); }
  $("readerClose").addEventListener("click", closeReader);

  // ---------------------------------------------------------------- library
  const covers = {};
  function renderLibrary() {
    const host = $("docList");
    const q = $("docFilter").value.trim().toLowerCase();
    host.innerHTML = "";
    let k = 0;
    [["company", "Документы компании"], ["law", "Законы"]].forEach(([cat, title]) => {
      if (state.cat && state.cat !== cat) return;
      const items = state.docs.filter((d) => d.category === cat && (!q || (d.title + " " + d.subtitle).toLowerCase().includes(q)));
      if (!items.length) return;
      host.append(el("div", "caption group", title));
      items.forEach((d, j) => {
        const row = el("button", "row");
        row.type = "button";
        row.style.animationDelay = Math.min(k * 0.025, 0.4) + "s";
        k += 1;
        const cover = el("span", "row__cover");
        const key = cat + j;
        covers[key] = covers[key] || P.auraImage(cat === "law" ? "night" : "soft", j + (cat === "law" ? 11 : 0), true);
        cover.style.backgroundImage = `url(${covers[key]})`;
        const text = el("span", "row__text");
        text.append(el("span", "row__title", d.title));
        const meta = el("span", "row__meta");
        meta.append(document.createTextNode(cat === "law" ? (d.subtitle || "") : (d.date ? "от " + d.date : "")));
        if (d.state === "lapsed") meta.append(el("span", "lapsed", " · утратил силу"));
        if (d.injections) meta.append(el("span", "guard", " · инструкция для ИИ исключена"));
        text.append(meta);
        row.append(cover, text);
        row.insertAdjacentHTML("beforeend", ICON.chev);
        row.addEventListener("click", () => openDoc(d.id));
        host.append(row);
      });
    });
    if (!k) host.append(el("p", "caption", "Ничего не нашлось."));
  }
  function openLibrary() { closeSheet(); $("library").hidden = false; document.documentElement.style.overflow = "hidden"; $("docFilter").focus(); }
  function closeLibrary() { $("library").hidden = true; document.documentElement.style.overflow = ""; }
  $("docFilter").addEventListener("input", renderLibrary);
  $("filters").addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    state.cat = b.dataset.cat;
    $("filters").querySelectorAll(".chip").forEach((x) => x.classList.toggle("on", x === b));
    renderLibrary();
  });
  $("libraryClose").addEventListener("click", closeLibrary);
  $("library").addEventListener("pointerdown", (e) => { if (e.target === $("library")) closeLibrary(); });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!menu.hidden) { toggleMenu(false); navButton.focus(); }
    else if (!backdrop.hidden) closeSheet();
    else if (!$("library").hidden) closeLibrary();
    else if (!$("reader").hidden) closeReader();
  });

  boot();
})();
