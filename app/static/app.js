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
  const svg = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
  const ICON = {
    check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
    warn: svg('<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/>'),
    chev: svg('<path d="M9 6l6 6-6 6"/>'),
    how: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5M12 7.6v.01"/>'),
    copy: svg('<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>'),
    done: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  };
  const STAGES = { retrieval: "Ищу в документах", generation: "Формулирую ответ", second_pass: "Ищу шире", verification: "Сверяю с текстом", repair: "Уточняю ответ", edit: "Убираю лишнее" };
  const STEP_NAMES = { retrieval: "Поиск по базе", generation: "Ответ модели", second_pass: "Расширенный поиск", verification: "Сверка с текстом", edit: "Редактура" };
  const STATE_TEXT = { PARTIAL: "Подтверждено частично", NOT_IN_KB: "В базе нет сведений", OFF_TOPIC: "Вне юридической темы", ILLEGAL: "Не могу помочь", ROLE: "Правила не меняются", ERROR: "Техническая ошибка" };
  const HUES = ["#b8a6ff", "#9cc6ff", "#9fe2c3", "#ffb38c", "#ff9ec4"];

  const state = { messages: [], docs: [], byId: {}, busy: false, apiReady: false };
  const input = $("input");
  const fmt = (x) => x.toFixed(1).replace(".", ",");

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
      if (!state.apiReady) notice("Модель не подключена: укажите LLM_BASE_URL и LLM_API_KEY в файле .env и перезапустите сервис. Документы можно открыть кнопкой «Документы».");
      renderSuggestions(examples.suggestions || []);
      renderLibrary();
    } catch (e) {
      notice("Сервис недоступен. Проверьте, что контейнер запущен.");
    }
    input.focus();
  }

  function notice(text) { const n = $("notice"); n.textContent = text; n.hidden = false; }

  function renderSuggestions(items) {
    const box = $("suggestions");
    box.innerHTML = "";
    items.forEach((s, i) => {
      const b = el("button", "chip", s.label);
      b.type = "button";
      b.style.animationDelay = (0.15 + i * 0.07) + "s";
      b.addEventListener("click", () => send(s.question));
      box.append(b);
    });
  }

  // ---------------------------------------------------------------- composer
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; }
  input.addEventListener("input", () => { autosize(); $("send").disabled = !input.value.trim() || state.busy; });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  $("send").disabled = true;

  function reset() {
    if (state.busy) return;
    state.messages = [];
    $("thread").innerHTML = "";
    document.body.classList.remove("chatting");
    closeSheet();
    input.value = ""; autosize(); input.focus();
  }
  $("newChat").addEventListener("click", reset);
  $("home").addEventListener("click", reset);

  // ---------------------------------------------------------------- conversation
  const thread = $("thread");
  const scrollDown = () => { thread.scrollTop = thread.scrollHeight; };

  function addUser(text) {
    const m = el("div", "msg user");
    m.append(el("div", "bubble", text));
    thread.append(m);
  }

  function addReply() {
    const m = el("div", "msg reply");
    const orb = el("span", "orb mid thinking");
    const body = el("div", "reply-body");
    const line = el("span", "thinking-line", STAGES.retrieval);
    body.append(line);
    m.append(orb, body);
    thread.append(m);
    return { m, orb, body, stage: (s) => { if (STAGES[s]) line.textContent = STAGES[s]; } };
  }

  async function send(raw) {
    const text = (raw || "").trim();
    if (!text || state.busy) return;
    if (!state.apiReady) { notice("Модель не подключена: укажите LLM_BASE_URL и LLM_API_KEY в файле .env и перезапустите сервис."); return; }
    state.busy = true;
    document.body.classList.add("chatting", "busy");
    $("send").disabled = true;
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
      reply.orb.classList.remove("thinking");
      reply.body.innerHTML = "";
      reply.body.append(el("div", "state ERROR", STATE_TEXT.ERROR),
        el("div", "answer", "Не удалось получить ответ: " + (err.message || err) + ". Вопрос можно отправить ещё раз."));
      input.value = text; autosize();
    } finally {
      state.busy = false;
      document.body.classList.remove("busy");
      $("send").disabled = !input.value.trim();
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

  function render(reply, r) {
    reply.orb.classList.remove("thinking");
    const body = reply.body;
    body.innerHTML = "";
    if (STATE_TEXT[r.status]) body.append(el("div", "state " + r.status, STATE_TEXT[r.status]));

    // the answer appears word by word
    const ans = el("div", "answer");
    r.answer.split(/(\s+)/).forEach((w, i) => {
      if (!w.trim()) { ans.append(document.createTextNode(w)); return; }
      const s = el("span", "w", w);
      s.style.animationDelay = Math.min(i * 0.012, 1.2) + "s";
      ans.append(s);
    });
    body.append(ans);

    if (r.extra) {
      const more = el("details", "more");
      const sum = el("summary"); sum.innerHTML = ICON.chev + "<span>Ещё по теме</span>";
      more.append(sum, el("div", "more-body", r.extra));
      body.append(more);
    }

    const facts = el("div", "facts");
    r.sources.forEach((s, i) => {
      const b = el("button", "src" + (s.ok ? "" : " bad"));
      b.type = "button";
      b.style.animationDelay = (0.25 + i * 0.06) + "s";
      b.append(el("b", "", s.locator || "документ"), el("span", "", s.title));
      b.addEventListener("click", () => openDoc(s.doc_id, r.sources, i));
      hover(b, () => {
        const box = el("div");
        box.append(el("div", "t", s.title + (s.locator ? " · " + s.locator : "")));
        if (s.quote) box.append(el("div", "q", s.quote));
        return box;
      });
      facts.append(b);
    });

    const c = r.checks;
    if (r.status !== "OFF_TOPIC") {
      const seal = el("button", "seal" + (c.verified ? "" : " warn"));
      seal.type = "button";
      seal.innerHTML = (c.verified ? ICON.check : ICON.warn) + `<span>${c.verified ? "Сверено с документами" : "Проверьте ответ"}</span>`;
      seal.style.animationDelay = (0.3 + r.sources.length * 0.06) + "s";
      hover(seal, () => {
        const box = el("div");
        if (c.verified) {
          box.append(el("div", "t", "Каждый факт найден в тексте"));
          if (c.sources_total) box.append(row("Ссылки найдены", `${c.sources_ok} из ${c.sources_total}`));
          if (c.numbers_checked) box.append(row("Числа и даты сверены", String(c.numbers_checked)));
          if (c.repaired) box.append(el("div", "q", "ответ был исправлен после проверки"));
        } else {
          box.append(el("div", "t", "Не подтверждено документами"));
          const ul = el("ul"); c.hard_issues.forEach((t) => ul.append(el("li", "", t))); box.append(ul);
        }
        return box;
      });
      facts.append(seal);
    }

    const how = el("button", "tool"); how.type = "button"; how.innerHTML = ICON.how; how.setAttribute("aria-label", "Как получен ответ");
    tip(how, "Как получен ответ");
    let trace = null;
    how.addEventListener("click", () => {
      if (trace) { trace.remove(); trace = null; return; }
      trace = traceBox(r); body.append(trace); trace.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    const copy = el("button", "tool"); copy.type = "button"; copy.innerHTML = ICON.copy; copy.setAttribute("aria-label", "Скопировать");
    tip(copy, "Скопировать ответ");
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(r.answer); copy.innerHTML = ICON.done; setTimeout(() => { copy.innerHTML = ICON.copy; }, 1400); } catch (e) { /* clipboard blocked */ }
    });
    facts.append(how, copy);
    body.append(facts);

    const first = r.sources.find((s) => s.ok && s.start !== null);
    if (first && window.innerWidth > 1180) setTimeout(() => openDoc(first.doc_id, r.sources, r.sources.indexOf(first), true), 650);
  }

  function row(label, value) { const d = el("div", "row"); d.append(el("span", "", label), el("b", "", value)); return d; }

  function traceBox(r) {
    const box = el("div", "trace");
    if (r.trace.context.length) {
      const sec = el("div"); sec.append(el("h4", "", "Что видела модель"));
      const docs = el("div", "docs");
      r.trace.context.forEach((x) => {
        const b = el("button", "", x.title + (x.full ? "" : " — фрагменты"));
        b.type = "button"; b.addEventListener("click", () => openDoc(x.id, r.sources));
        docs.append(b);
      });
      sec.append(docs); box.append(sec);
    }
    if (r.trace.hints.length) {
      const sec = el("div"); sec.append(el("h4", "", "Подсказки, вычисленные по реестру документов"));
      const ul = el("ul"); r.trace.hints.forEach((h) => ul.append(el("li", "", h))); sec.append(ul); box.append(sec);
    }
    const t = r.trace.timings || {};
    const keys = ["retrieval", "generation", "second_pass", "verification"].filter((k) => t[k] !== undefined);
    if (keys.length) {
      const sec = el("div"); sec.append(el("h4", "", "Время"));
      const steps = el("div", "steps");
      const max = Math.max(...keys.map((k) => t[k]), 0.01);
      keys.forEach((k, i) => {
        const line = el("div", "step");
        const bar = el("i"); bar.style.width = Math.max(3, (t[k] / max) * 100) + "%"; bar.style.animationDelay = (i * 0.08) + "s";
        const track = el("span"); track.append(bar);
        line.append(el("span", "", STEP_NAMES[k]), track, el("span", "", fmt(t[k]) + " с"));
        steps.append(line);
      });
      sec.append(steps); box.append(sec);
    }
    return box;
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
    let top = a.top - h - 12;
    if (top < 12) top = a.bottom + 12;
    pop.style.left = left + "px"; pop.style.top = top + "px";
    requestAnimationFrame(() => pop.classList.add("on"));
  }
  function hidePop() { pop.classList.remove("on"); popTimer = setTimeout(() => { pop.hidden = true; }, 260); }
  function hover(node, build) {
    node.addEventListener("mouseenter", () => showPop(node, build()));
    node.addEventListener("mouseleave", hidePop);
    node.addEventListener("focus", () => showPop(node, build()));
    node.addEventListener("blur", hidePop);
  }
  function tip(node, text) { hover(node, () => el("div", "", text)); }
  document.querySelectorAll("[data-tip]").forEach((n) => tip(n, n.dataset.tip));

  // ---------------------------------------------------------------- document sheet
  const docCache = {};
  async function openDoc(id, sources, focusIndex, quiet) {
    let doc = docCache[id];
    if (!doc) { try { doc = docCache[id] = await getJSON("/api/documents/" + id); } catch (e) { return; } }
    const list = sources || [];
    const cites = list.filter((s) => s.doc_id === id && s.start !== null && s.end !== null);
    const focus = focusIndex !== undefined && list[focusIndex] && list[focusIndex].doc_id === id ? list[focusIndex] : cites[0];
    $("sheetKind").textContent = doc.category === "law" ? "Закон" : "Документ компании";
    $("sheetTitle").textContent = doc.full_title;
    const st = $("sheetStatus"); st.innerHTML = "";
    if (doc.state === "lapsed") st.append(el("span", "lapsed", "Утратил силу. "));
    if (doc.status) st.append(document.createTextNode(doc.status.charAt(0).toUpperCase() + doc.status.slice(1) + ". "));
    if (doc.removed.length) st.append(el("span", "guard", "Встроенная инструкция для ИИ исключена."));
    const jumps = $("sheetJumps"); jumps.innerHTML = "";
    cites.forEach((s) => {
      const b = el("button", "jump" + (s === focus ? " on" : ""), s.locator || "фрагмент");
      b.type = "button";
      b.addEventListener("click", () => openDoc(id, list, list.indexOf(s)));
      jumps.append(b);
    });
    renderDoc(doc, cites, focus);
    const sheet = $("sheet");
    const wasOpen = !sheet.hidden;
    sheet.hidden = false;
    document.body.classList.toggle("reading", window.innerWidth > 1180);
    if (!wasOpen) { const inner = sheet.firstElementChild; inner.style.animation = "none"; void inner.offsetHeight; inner.style.animation = ""; }
    if (!quiet || window.innerWidth <= 1180) $("veil").hidden = false;
    $("library").hidden = true;
    requestAnimationFrame(() => {
      const target = $("docBody").querySelector("mark.focus") || $("docBody").querySelector("mark");
      if (target) target.scrollIntoView({ block: "center", behavior: wasOpen ? "smooth" : "auto" });
      else $("docBody").scrollTop = 0;
    });
  }

  function renderDoc(doc, cites, focus) {
    const body = $("docBody"); body.innerHTML = "";
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
        p.textContent = "Здесь была строка с инструкцией для ИИ-ассистента. Она не является положением документа и исключена.";
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

  function closeSheet() { $("sheet").hidden = true; $("veil").hidden = true; document.body.classList.remove("reading"); }
  $("sheetClose").addEventListener("click", closeSheet);
  $("veil").addEventListener("click", closeSheet);

  // ---------------------------------------------------------------- library
  function renderLibrary() {
    const grid = $("docList");
    const q = $("docFilter").value.trim().toLowerCase();
    grid.innerHTML = "";
    let k = 0;
    [["company", "Документы компании"], ["law", "Законы"]].forEach(([cat, title]) => {
      const items = state.docs.filter((d) => d.category === cat && (!q || (d.title + " " + d.subtitle).toLowerCase().includes(q)));
      if (!items.length) return;
      grid.append(el("div", "group-title", title));
      items.forEach((d) => {
        const card = el("button", "card");
        card.type = "button";
        card.style.setProperty("--c", HUES[k % HUES.length]);
        card.style.animationDelay = Math.min(k * 0.025, 0.5) + "s";
        k += 1;
        card.append(el("div", "n", d.title));
        const meta = el("div", "m");
        meta.append(document.createTextNode(cat === "law" ? (d.subtitle || "") : (d.date ? "от " + d.date : "")));
        if (d.state === "lapsed") meta.append(el("span", "lapsed", " · утратил силу"));
        if (d.injections) meta.append(el("span", "guard", " · инструкция для ИИ исключена"));
        card.append(meta);
        card.addEventListener("click", () => openDoc(d.id));
        grid.append(card);
      });
    });
  }
  $("docFilter").addEventListener("input", renderLibrary);
  $("docsOpen").addEventListener("click", () => { closeSheet(); $("library").hidden = false; $("docFilter").focus(); });
  $("docsClose").addEventListener("click", () => { $("library").hidden = true; });
  $("library").addEventListener("click", (e) => { if (e.target === $("library")) $("library").hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("library").hidden) $("library").hidden = true;
    else if (!$("sheet").hidden) closeSheet();
  });

  boot();
})();
