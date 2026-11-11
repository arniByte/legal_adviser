/* Параграф — web client of the legal assistant. Plain JavaScript, no build step. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const icon = (paths) => `<svg viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  const ICONS = {
    check: icon('<path d="M4 12.5l5 5L20 6.5"/>'),
    warn: icon('<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/>'),
    chev: icon('<path d="M9 6l6 6-6 6"/>'),
    copy: icon('<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>'),
  };
  const STAGES = [
    ["retrieval", "Поиск по базе"], ["generation", "Формулировка ответа"], ["second_pass", "Расширенный поиск"],
    ["verification", "Сверка с документами"], ["repair", "Исправление"],
  ];
  const TIMING_LABELS = { retrieval: "Поиск по базе", generation: "Ответ модели", second_pass: "Расширенный поиск",
    verification: "Сверка и исправление", total: "Всего" };

  const state = { messages: [], docs: [], docsById: {}, busy: false, viewer: null, lastSources: [], apiReady: false };

  // ---------------------------------------------------------------- theme
  const savedTheme = (() => { try { return localStorage.getItem("theme"); } catch (e) { return null; } })();
  document.documentElement.dataset.theme = savedTheme || "dark";
  $("themeBtn").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) { /* private mode */ }
  });

  // ---------------------------------------------------------------- cells: the knowledge base as a cluster
  const SVGNS = "http://www.w3.org/2000/svg";
  const svgEl = (tag, attrs) => {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  };
  const plural = (n, one, few, many) => {
    const m10 = n % 10, m100 = n % 100;
    const w = m10 === 1 && m100 !== 11 ? one : (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many);
    return `${n} ${w}`;
  };
  const mix = (a, b, t) => "#" + [1, 3, 5].map((i) => {
    const x = parseInt(a.slice(i, i + 2), 16), y = parseInt(b.slice(i, i + 2), 16);
    return Math.round(x + (y - x) * t).toString(16).padStart(2, "0");
  }).join("");

  function buildCells(docs, opts) {
    const cells = docs.filter((d) => d.cell && d.cell.path);
    const pad = 40;
    const x0 = Math.min(...cells.map((d) => d.cell.cx - d.cell.r)) - pad;
    const y0 = Math.min(...cells.map((d) => d.cell.cy - d.cell.r)) - pad;
    const x1 = Math.max(...cells.map((d) => d.cell.cx + d.cell.r)) + pad;
    const y1 = Math.max(...cells.map((d) => d.cell.cy + d.cell.r)) + pad;
    const svg = svgEl("svg", { viewBox: `${x0} ${y0} ${x1 - x0} ${y1 - y0}`, class: "cells" });
    const defs = svgEl("defs", {});
    const uid = opts.uid;
    defs.innerHTML = `
      <radialGradient id="cellLit" cx=".42" cy=".62" r=".75" fx=".45" fy=".7"><stop offset="0" stop-color="#f3cbbf"/><stop offset=".55" stop-color="#b17f74"/><stop offset="1" stop-color="#3f2622"/></radialGradient>
      <radialGradient id="cellRest" cx=".42" cy=".62" r=".75"><stop offset="0" stop-color="#3a3333"/><stop offset=".6" stop-color="#1f1a1a"/><stop offset="1" stop-color="#0c0a0a"/></radialGradient>
      <filter id="${uid}-soft" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="12"/></filter>
      <filter id="${uid}-glow" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="20"/></filter>`;
    const ys = cells.map((d) => d.cell.cy), lo = Math.min(...ys), hi = Math.max(...ys);
    cells.forEach((d) => {
      const t = (d.cell.cy - lo) / ((hi - lo) || 1);
      const g = svgEl("radialGradient", { id: `${uid}-g${d.id}`, cx: ".42", cy: ".62", r: ".75", fx: ".45", fy: ".7" });
      g.innerHTML = `<stop offset="0" stop-color="${mix("#4a3534", "#b58a80", t)}"/><stop offset=".62" stop-color="${mix("#2c2020", "#6e4b46", t)}"/><stop offset="1" stop-color="#140e0e"/>`;
      defs.append(g);
    });
    svg.append(defs);
    const circles = cells.map((d) => [d.cell.cx, d.cell.cy, d.cell.r * 1.06 + 16]);
    const shadow = svgEl("g", { filter: `url(#${uid}-soft)` });
    circles.forEach(([x, y, r]) => shadow.append(svgEl("circle", { cx: x, cy: y + 14, r: r + 6, class: "shadow" })));
    const membrane = svgEl("g", {});
    circles.forEach(([x, y, r]) => membrane.append(svgEl("circle", { cx: x, cy: y, r, class: "membrane-rim" })));
    circles.forEach(([x, y, r]) => membrane.append(svgEl("circle", { cx: x, cy: y, r: r - 1.2, class: "membrane-fill" })));
    svg.append(shadow, membrane);
    const nodes = {};
    cells.forEach((d, i) => {
      const g = svgEl("g", { class: "cell" + (d.state === "lapsed" ? " rest" : ""), "data-id": d.id });
      if (opts.animate) g.style.animationDelay = (0.15 + ((d.cell.cy - lo) / ((hi - lo) || 1)) * 0.9 + (i % 3) * 0.05).toFixed(2) + "s";
      g.append(svgEl("path", { d: d.cell.path, class: "halo", filter: `url(#${uid}-glow)` }));
      g.append(svgEl("path", { d: d.cell.path, class: "body", fill: `url(#${uid}-g${d.id})` }));
      if (opts.labels) {
        const lines = (d.cell.label || "").split("\n");
        const size = d.cell.r > 70 ? 15 : 13;
        lines.forEach((line, k) => {
          const tx = svgEl("text", { x: d.cell.cx, y: d.cell.cy + (k - (lines.length - 1) / 2) * size * 1.2 + size * 0.35, class: "cap", "font-size": size });
          tx.textContent = line;
          g.append(tx);
        });
      }
      svg.append(g);
      nodes[d.id] = g;
    });
    return { svg, nodes };
  }

  let cluster = null;
  function renderCluster() {
    const box = $("cluster");
    box.innerHTML = "";
    cluster = buildCells(state.docs, { uid: "kb", labels: false });
    box.append(cluster.svg);
    const tip = $("tip");
    Object.entries(cluster.nodes).forEach(([id, node]) => {
      const d = state.docsById[id];
      node.addEventListener("click", () => openDoc(id));
      node.addEventListener("mousemove", (e) => {
        const r = $("rail").getBoundingClientRect();
        tip.innerHTML = "";
        tip.append(el("b", "", d.title), el("span", "", d.category === "law" ? (d.subtitle || "закон") : (d.status || d.subtitle)));
        tip.hidden = false;
        tip.style.left = Math.min(e.clientX - r.left + 14, r.width - 250) + "px";
        tip.style.top = (e.clientY - r.top + 16) + "px";
      });
      node.addEventListener("mouseleave", () => { tip.hidden = true; });
    });
    updateCluster();
  }

  function updateCluster(context) {
    if (!cluster) return;
    const cited = new Set(state.lastSources.filter((x) => x.ok !== false).map((x) => x.doc_id));
    const seen = new Set(context || []);
    Object.entries(cluster.nodes).forEach(([id, node]) => {
      node.classList.toggle("lit", cited.has(id));
      node.classList.toggle("dim", seen.size > 0 && !seen.has(id) && !cited.has(id));
    });
  }

  // ---------------------------------------------------------------- intro
  function showIntro() {
    const intro = $("intro");
    const { svg } = buildCells(state.docs, { uid: "intro", labels: false, animate: true });
    $("introCluster").append(svg);
    const n = state.docs.length, company = state.docs.filter((d) => d.category === "company").length;
    $("introMeta").textContent = `${plural(company, "документ", "документа", "документов")} компании · ${plural(n - company, "статья", "статьи", "статей")} законов · каждый факт сверяется с текстом`;
    const close = () => { intro.classList.add("gone"); setTimeout(() => intro.remove(), 800); input.focus(); };
    $("introGo").addEventListener("click", close);
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Enter" || e.key === "Escape") { document.removeEventListener("keydown", onKey); close(); }
    });
    if (/[?&]skip-intro/.test(location.search)) { intro.remove(); }
  }

  // ---------------------------------------------------------------- data
  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.json();
  }

  async function boot() {
    try {
      const [health, docs, examples] = await Promise.all([
        getJSON("/api/health"), getJSON("/api/documents"), getJSON("/api/examples")]);
      state.docs = docs.documents;
      state.docs.forEach((d) => { state.docsById[d.id] = d; });
      renderHealth(health);
      renderCluster();
      renderDocList();
      renderExamples(examples.groups);
      showIntro();
    } catch (e) {
      const intro = $("intro"); if (intro) intro.remove();
      $("apiDot").className = "pulse bad";
      $("apiText").textContent = "Сервис недоступен";
    }
  }

  function renderHealth(h) {
    state.apiReady = h.api_configured;
    $("kbCount").textContent = `${plural(h.documents, "документ", "документа", "документов")} · ${plural(h.fragments, "фрагмент", "фрагмента", "фрагментов")}`;
    $("apiDot").className = "pulse " + (h.api_configured ? "ok" : "bad");
    $("apiText").textContent = h.api_configured
      ? `Модель ${h.model} · поиск: ${h.dense_search ? "гибридный" : "полнотекстовый"}${h.reranker ? " + реранкер" : ""}`
      : "Модель не подключена";
    if (!h.api_configured) {
      $("banner").hidden = false;
      $("banner").textContent = "Языковая модель не подключена: задайте LLM_BASE_URL и LLM_API_KEY в файле .env и перезапустите сервис. Документы базы знаний доступны для просмотра.";
    }
    $("railFoot").textContent = `${Math.round(h.chars / 1000)} тыс. знаков · отсеяно встроенных инструкций: ${h.removed_instructions} · отпечаток ${h.fingerprint}`;
    if (h.api_configured && !h.dense_ready) setTimeout(() => getJSON("/api/health").then(renderHealth).catch(() => {}), 4000);
  }

  // ---------------------------------------------------------------- documents list
  function renderDocList() {
    const list = $("docList");
    const filter = $("docFilter").value.trim().toLowerCase();
    const cited = new Set(state.lastSources.map((s) => s.doc_id));
    list.innerHTML = "";
    const groups = [["company", "Документы компании"], ["law", "Законы"]];
    for (const [cat, title] of groups) {
      const items = state.docs.filter((d) => d.category === cat &&
        (!filter || (d.title + " " + d.subtitle + " " + d.filename).toLowerCase().includes(filter)));
      if (!items.length) continue;
      const head = el("div", "doc-group");
      head.append(el("span", "", title), el("span", "", String(items.length)));
      list.append(head);
      for (const d of items) {
        const b = el("button", "doc-item" + (state.viewer === d.id ? " active" : "") + (cited.has(d.id) ? " cited" : "") + (d.state === "lapsed" ? " lapsed" : ""));
        b.type = "button";
        b.append(el("span", "bullet"), el("span", "doc-title", d.title));
        const sub = el("span", "doc-sub");
        const bits = [];
        if (cat === "law") bits.push(d.subtitle); else if (d.date) bits.push("от " + d.date);
        sub.append(document.createTextNode(bits.filter(Boolean).join(" · ")));
        if (d.state === "lapsed") sub.append(el("span", "off", " · утратил силу"));
        if (d.injections) sub.append(el("span", "flag", " · инструкция для ИИ отсеяна"));
        b.append(sub);
        b.addEventListener("click", () => openDoc(d.id));
        list.append(b);
      }
    }
  }
  $("docFilter").addEventListener("input", renderDocList);

  // ---------------------------------------------------------------- examples
  function renderExamples(groups) {
    const box = $("examples");
    box.innerHTML = "";
    for (const g of groups) {
      const card = el("div", "ex-group");
      card.append(el("span", "label", g.group));
      for (const q of g.items) {
        const b = el("button", "ex-item", q);
        b.type = "button";
        b.addEventListener("click", () => send(q));
        card.append(b);
      }
      box.append(card);
    }
  }

  // ---------------------------------------------------------------- chat
  const input = $("input");
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  $("composer").addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  $("newChat").addEventListener("click", () => {
    if (state.busy) return;
    state.messages = []; state.lastSources = [];
    $("thread").innerHTML = ""; $("thread").append(heroNode);
    renderDocList(); updateCluster(); input.focus();
  });
  const heroNode = $("hero");

  function scrollDown() { const t = $("thread"); t.scrollTop = t.scrollHeight; }

  function addUser(text) {
    const m = el("div", "msg user");
    m.append(el("div", "bubble", text));
    $("thread").append(m);
  }

  function progressCard() {
    const m = el("div", "msg assistant");
    const card = el("div", "card");
    const p = el("div", "progress");
    const steps = el("div", "steps");
    const nodes = {};
    for (const [key, label] of STAGES) {
      const s = el("span", "step", label);
      if (key === "second_pass" || key === "repair") s.hidden = true;
      nodes[key] = s; steps.append(s);
    }
    p.append(steps, el("div", "shimmer"), el("div", "shimmer"), el("div", "shimmer"));
    card.append(p); m.append(card);
    $("thread").append(m);
    let current = null;
    return {
      node: m, card,
      stage(name) {
        if (!nodes[name]) return;
        if (current && nodes[current]) nodes[current].className = "step done";
        nodes[name].hidden = false; nodes[name].className = "step active"; current = name;
        scrollDown();
      },
    };
  }

  async function send(raw) {
    const text = (raw || "").trim();
    if (!text || state.busy) return;
    if (!state.apiReady) { $("banner").hidden = false; return; }
    state.busy = true; $("send").disabled = true;
    if (heroNode.isConnected) heroNode.remove();
    input.value = ""; autosize();
    state.messages.push({ role: "user", content: text });
    addUser(text);
    const prog = progressCard();
    prog.stage("retrieval");
    scrollDown();
    try {
      const result = await streamChat(state.messages, (name) => prog.stage(name));
      state.messages.push({ role: "assistant", content: result.answer });
      renderAnswer(prog, result);
    } catch (err) {
      state.messages.pop();
      prog.card.innerHTML = "";
      const head = el("div", "card-head"); head.append(el("span", "status ERROR", "Техническая ошибка"));
      prog.card.append(head, el("div", "answer", "Не удалось получить ответ: " + (err.message || err) + ". Вопрос не потерян — отправьте его ещё раз."));
      input.value = text; autosize();
    } finally {
      state.busy = false; $("send").disabled = false; scrollDown(); input.focus();
    }
  }

  async function streamChat(messages, onStage) {
    const resp = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ messages }) });
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

  function renderAnswer(prog, r) {
    const card = prog.card;
    card.innerHTML = "";
    const head = el("div", "card-head");
    head.append(el("span", "status " + r.status, r.status_label));
    const total = r.trace.timings && r.trace.timings.total;
    if (total) head.append(el("span", "card-time", total.toFixed(1).replace(".", ",") + " с"));
    card.append(head, el("div", "answer", r.answer));
    if (r.extra) {
      const more = el("details", "extra");
      const ms = el("summary"); ms.innerHTML = ICONS.chev + "<span>Смежные положения, о которых не спрашивали</span>";
      more.append(ms, el("div", "extra-body", r.extra));
      card.append(more);
    }

    if (r.sources.length) {
      const box = el("div", "sources");
      r.sources.forEach((s, i) => {
        const b = el("button", "src" + (s.ok ? "" : " bad"));
        b.type = "button";
        b.title = s.quote ? "«" + s.quote + "»" : "Открыть документ";
        b.append(el("span", "src-loc", (s.locator || "документ") + (s.ok ? "" : " · не найдено")), el("span", "src-title", s.title));
        b.addEventListener("click", () => openDoc(s.doc_id, r.sources, i));
        box.append(b);
      });
      card.append(box);
    }

    const c = r.checks;
    const v = el("div", "verify " + (c.verified ? "ok" : "warn"));
    v.innerHTML = c.verified ? ICONS.check : ICONS.warn;
    const body = el("div");
    if (c.verified) {
      const parts = [];
      if (c.sources_total) parts.push(`ссылок найдено в документах: <b>${c.sources_ok} из ${c.sources_total}</b>`);
      if (c.numbers_checked) parts.push(`чисел и дат сверено с текстом: <b>${c.numbers_checked}</b>`);
      if (c.repaired) parts.push("ответ был исправлен после проверки");
      body.innerHTML = "Проверка кодом пройдена" + (parts.length ? " · " + parts.join(" · ") : " · неподтверждённых фактов не найдено");
    } else {
      body.innerHTML = "<b>Проверка нашла неподтверждённые элементы — перепроверьте ответ по документам:</b>";
      const ul = el("ul"); c.hard_issues.forEach((t) => ul.append(el("li", "", t))); body.append(ul);
    }
    v.append(body); card.append(v);

    const d = el("details", "trace");
    const sum = el("summary"); sum.innerHTML = ICONS.chev + "<span>Как получен ответ</span>";
    const tb = el("div", "trace-body");
    if (r.trace.context.length) {
      const sec = el("div"); sec.append(el("h4", "", "Что видела модель"));
      const ctx = el("div", "ctx");
      r.trace.context.forEach((x) => {
        const b = el("button", x.full ? "" : "frag", `${x.id} · ${x.title}${x.full ? "" : " (фрагменты)"}`);
        b.type = "button"; b.addEventListener("click", () => openDoc(x.id, r.sources));
        ctx.append(b);
      });
      sec.append(ctx); tb.append(sec);
    }
    if (r.trace.hints.length) {
      const sec = el("div"); sec.append(el("h4", "", "Подсказки реестра документов (вычислены кодом)"));
      const ul = el("ul"); r.trace.hints.forEach((h) => ul.append(el("li", "", h))); sec.append(ul); tb.append(sec);
    }
    if (r.trace.analysis) {
      const sec = el("div"); sec.append(el("h4", "", "Разбор модели"), el("p", "", r.trace.analysis)); tb.append(sec);
    }
    if (c.notes && c.notes.length) {
      const sec = el("div"); sec.append(el("h4", "", "Замечания проверки"));
      const ul = el("ul"); c.notes.forEach((h) => ul.append(el("li", "", h))); sec.append(ul); tb.append(sec);
    }
    const t = r.trace.timings || {};
    const keys = ["retrieval", "generation", "second_pass", "verification"].filter((k) => t[k] !== undefined);
    if (keys.length) {
      const sec = el("div"); sec.append(el("h4", "", `Время по этапам · обращений к модели: ${r.trace.passes + (c.repaired ? 1 : 0)}`));
      const bars = el("div", "bars");
      const max = Math.max(...keys.map((k) => t[k]), 0.01);
      keys.forEach((k) => {
        const row = el("div", "bar");
        const track = el("span"); const fill = el("i"); fill.style.width = Math.max(2, (t[k] / max) * 100) + "%"; track.append(fill);
        row.append(el("span", "", TIMING_LABELS[k]), track, el("span", "", t[k].toFixed(2).replace(".", ",") + " с"));
        bars.append(row);
      });
      sec.append(bars); tb.append(sec);
    }
    d.append(sum, tb); card.append(d);

    const actions = el("div", "card-actions");
    const copy = el("button", "link-btn"); copy.type = "button"; copy.innerHTML = ICONS.copy + "<span>Скопировать ответ</span>";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(r.answer); copy.lastChild.textContent = "Скопировано"; } catch (e) { copy.lastChild.textContent = "Не удалось скопировать"; }
      setTimeout(() => { copy.lastChild.textContent = "Скопировать ответ"; }, 1600);
    });
    actions.append(copy); card.append(actions);

    state.lastSources = r.sources;
    renderDocList();
    updateCluster(r.trace.context.map((x) => x.id));
    const first = r.sources.find((s) => s.ok && s.start !== null);
    if (first && window.matchMedia("(min-width: 1201px)").matches) openDoc(first.doc_id, r.sources, r.sources.indexOf(first), true);
  }

  // ---------------------------------------------------------------- document viewer
  const docCache = {};
  async function openDoc(id, sources, focusIndex, quiet) {
    let doc = docCache[id];
    if (!doc) {
      try { doc = docCache[id] = await getJSON("/api/documents/" + id); } catch (e) { return; }
    }
    state.viewer = id;
    const cites = (sources || state.lastSources || []).filter((s) => s.doc_id === id && s.start !== null && s.end !== null);
    const focus = sources && focusIndex !== undefined && sources[focusIndex] && sources[focusIndex].doc_id === id ? sources[focusIndex] : cites[0];
    $("viewerKicker").textContent = doc.category === "law" ? "Закон" : "Документ компании";
    $("viewerTitle").textContent = doc.full_title;
    const meta = $("viewerMeta"); meta.innerHTML = "";
    if (doc.state === "lapsed") meta.append(el("span", "tag lapsed", "утратил силу"));
    if (doc.state === "current" && doc.category !== "law") meta.append(el("span", "tag current", "действует"));
    if (doc.status) meta.append(el("span", "tag" + (doc.category === "law" ? " current" : ""), doc.status));
    if (doc.removed.length) meta.append(el("span", "tag guard", "встроенная инструкция отсеяна"));
    meta.append(el("span", "tag", `${doc.units.length} положений · ${String(Math.round(doc.chars / 100) / 10).replace(".", ",")} тыс. знаков`));

    const tools = $("viewerTools"); tools.innerHTML = "";
    if (cites.length) {
      tools.append(el("span", "", "Фрагменты из ответа:"));
      cites.forEach((s, i) => {
        const b = el("button", "jump" + (s === focus ? " on" : ""), s.locator || "фрагмент " + (i + 1));
        b.type = "button";
        b.addEventListener("click", () => { openDoc(id, sources || state.lastSources, (sources || state.lastSources).indexOf(s)); });
        tools.append(b);
      });
    }
    renderDocBody(doc, cites, focus);
    $("viewer").hidden = false; $("app").classList.add("with-viewer");
    if (!window.matchMedia("(min-width: 1201px)").matches && !quiet) $("scrim").hidden = false;
    $("rail").classList.remove("open");
    renderDocList();
    requestAnimationFrame(() => {
      const target = $("docBody").querySelector("mark.focus") || $("docBody").querySelector("mark");
      if (target) target.scrollIntoView({ block: "center", behavior: quiet ? "auto" : "smooth" });
      else $("docBody").scrollTop = 0;
    });
  }

  function renderDocBody(doc, cites, focus) {
    const body = $("docBody"); body.innerHTML = "";
    const noteSet = new Set();
    doc.units.forEach((u) => u.notes.forEach((n) => noteSet.add(n.trim())));
    (doc.doc_notes || []).forEach((n) => noteSet.add(n.trim()));
    const unitAt = {};
    doc.units.forEach((u) => { unitAt[u.start] = u; });
    const ranges = cites.map((s) => ({ a: s.start, b: s.end, focus: s === focus })).sort((x, y) => x.a - y.a);
    let pos = 0;
    for (const line of doc.text.split("\n")) {
      const start = pos, end = pos + line.length; pos = end + 1;
      if (!line.trim()) continue;
      const unit = unitAt[start + (line.length - line.trimStart().length)] || unitAt[start];
      const row = el("div", "ln");
      const trimmed = line.trim();
      if (trimmed.startsWith("[Строка исключена системой")) row.classList.add("removed");
      else if (noteSet.has(trimmed) || /^(Источник|URL|Дата выгрузки):/.test(trimmed)) row.classList.add("note");
      else if (unit && (unit.kind === "section" || unit.kind === "head")) row.classList.add(unit.kind);
      const loc = el("span", "loc", unit && !row.classList.contains("note") && unit.kind !== "section" ? unit.locator : "");
      const tx = el("span", "tx");
      const hits = ranges.filter((r) => r.a < end && r.b > start);
      if (!hits.length) tx.textContent = line;
      else {
        row.classList.add("cited");
        let cur = start;
        for (const h of hits) {
          const a = Math.max(h.a, start), b = Math.min(h.b, end);
          if (a > cur) tx.append(document.createTextNode(doc.text.slice(cur, a)));
          if (b > Math.max(a, cur)) { const m = el("mark", h.focus ? "focus" : "", doc.text.slice(Math.max(a, cur), b)); tx.append(m); }
          cur = Math.max(cur, b);
        }
        if (cur < end) tx.append(document.createTextNode(doc.text.slice(cur, end)));
      }
      row.append(loc, tx); body.append(row);
    }
  }

  function closeViewer() {
    $("viewer").hidden = true; $("app").classList.remove("with-viewer"); $("scrim").hidden = true;
    state.viewer = null; renderDocList();
  }
  $("viewerClose").addEventListener("click", closeViewer);
  $("railOpen").addEventListener("click", () => { $("rail").classList.add("open"); $("scrim").hidden = false; });
  $("railClose").addEventListener("click", () => { $("rail").classList.remove("open"); $("scrim").hidden = true; });
  $("scrim").addEventListener("click", () => {
    $("rail").classList.remove("open");
    if (!window.matchMedia("(min-width: 1201px)").matches) closeViewer();
    $("scrim").hidden = true;
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("viewer").hidden) closeViewer(); });

  boot();
})();
