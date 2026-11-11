"""Knowledge base: loading, sanitising, structural parsing and the document registry.

No LLM calls here. The module turns raw files into documents made of structural
units (contract clauses, law points / sub-points / parts) with human-readable
locators such as "п. 4.2" or "п. 1, подп. 2.1", plus per-document metadata used
for date and edition reasoning.
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from la_text import TextIndex, find_dates, fmt_date, norm, stem, words

INJECTION_PLACEHOLDER = ("[Строка исключена системой: содержала встроенную инструкцию для ИИ-ассистента "
                         "и не является положением документа.]")

# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class Unit:
    doc_id: str
    idx: int
    kind: str                 # head | section | clause | point | subpoint | part | para | remark
    locator: str              # "п. 4.2", "п. 1, подп. 2.1", "ч. 3", "" ...
    keys: tuple               # numeric keys for matching citations: ("4.2",) / ("1", "2.1")
    heading: str              # section title or the lead-in of the parent point
    text: str
    notes: list = field(default_factory=list)   # editorial notes attached to the unit
    start: int = 0            # character offsets inside Doc.text
    end: int = 0


@dataclass
class Chunk:
    doc_id: str
    unit_idx: int
    seq: int                  # global position, keeps document order
    text: str                 # text sent to the model
    search_text: str          # text used for lexical / vector search (with context header)


@dataclass
class Doc:
    doc_id: str
    filename: str
    category: str             # company | law
    title: str
    text: str                 # sanitised text
    units: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    injections: list = field(default_factory=list)   # removed lines
    index: TextIndex = None   # type: ignore[assignment]

    @property
    def size(self) -> int:
        return len(self.text)

    def unit_by_keys(self, keys: tuple):
        """Find the unit a citation such as ("4.2",) or ("1", "2.1") points to."""
        keys = tuple(k for k in keys if k)
        if not keys:
            return None
        for u in self.units:
            if u.keys == keys:
                return u
        for u in self.units:                      # last key only, e.g. sub-point cited without its point
            if u.keys and u.keys[-1] == keys[-1] and len(keys) == 1:
                return u
        for u in self.units:                      # point cited where only sub-points exist
            if u.keys and u.keys[0] == keys[0]:
                return u
        return None


@dataclass
class Entity:
    name: str                 # ООО «Альфа-Трейд»
    short: str                # Альфа-Трейд
    stems: tuple
    doc_ids: list = field(default_factory=list)
    own: bool = False         # the company whose documents these are


class KnowledgeBase:
    def __init__(self, docs: list):
        self.docs = docs
        self.by_id = {d.doc_id: d for d in docs}
        self.chunks: list = []
        self.entities: list = []
        self.doc_numbers: dict = {}       # normalised number -> [doc_id]
        self.fingerprint = hashlib.sha256(
            "\x00".join(f"{d.filename}\x01{d.text}" for d in docs).encode("utf-8")).hexdigest()[:16]
        _build_chunks(self)
        _build_registry(self)

    @property
    def company_docs(self) -> list:
        return [d for d in self.docs if d.category == "company"]

    @property
    def law_docs(self) -> list:
        return [d for d in self.docs if d.category == "law"]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

KB_NAMES = ("knowledge-base.zip", "kb.zip", "knowledge_base.zip", "knowledge-base", "kb", "knowledge_base")


def _read_docx(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as doc:
        xml = doc.read("word/document.xml")
    root = ET.fromstring(xml)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    return "\n".join("".join(x.text or "" for x in p.iter(ns + "t")) for p in root.iter(ns + "p"))


def _decode(name: str, raw: bytes):
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".docx":
            return _read_docx(raw)
        if suffix in (".txt", ".md", ""):
            for enc in ("utf-8-sig", "cp1251"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
    except Exception as exc:  # unreadable file must not stop the run
        print(f"[kb] skipped unreadable file {name!r}: {type(exc).__name__}", file=sys.stderr)
    return None


def _fix_zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    for enc in ("utf-8", "cp866"):
        try:
            return info.filename.encode("cp437").decode(enc)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return info.filename


def read_raw_files(source: Path) -> list:
    """Return [(name, text)] from a zip archive or a directory."""
    files = []
    if source.is_dir():
        for path in sorted(source.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".txt", ".md", ".docx"):
                text = _decode(path.name, path.read_bytes())
                if text and text.strip():
                    files.append((path.name, text))
        return files
    with zipfile.ZipFile(source) as archive:
        for info in sorted(archive.infolist(), key=lambda i: _fix_zip_name(i)):
            name = _fix_zip_name(info)
            if name.endswith("/") or name.startswith("__MACOSX/") or Path(name).name.startswith("."):
                continue
            if Path(name).suffix.lower() not in (".txt", ".md", ".docx"):
                continue
            text = _decode(name, archive.read(info))
            if text and text.strip():
                files.append((Path(name).name, text))
    return files


def find_kb_source(data_dir: Path, extra_roots: tuple = ()) -> Path:
    roots = [data_dir, Path.cwd() / data_dir, Path.cwd(), *extra_roots]
    for root in roots:
        for name in KB_NAMES:
            p = root / name
            if p.is_file() or (p.is_dir() and any(p.iterdir())):
                return p
    for root in roots:                       # any zip that is not obviously something else
        if root.is_dir():
            zips = sorted(root.glob("*.zip"))
            if zips:
                return zips[0]
    raise FileNotFoundError("knowledge base archive not found in " + ", ".join(str(r) for r in roots))


def load_kb(source: Path) -> KnowledgeBase:
    files = read_raw_files(source)
    if not files:
        raise ValueError(f"no readable documents in {source}")
    docs = []
    counters = {"company": 0, "law": 0}
    for name, raw_text in files:
        text = raw_text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        category = _category(name, text)
        counters[category] += 1
        doc_id = ("D" if category == "company" else "L") + str(counters[category])
        clean, removed = sanitize(text)
        doc = Doc(doc_id=doc_id, filename=name, category=category, title="", text=clean, injections=removed)
        if category == "law":
            _parse_law(doc)
        else:
            _parse_company(doc)
        doc.index = TextIndex(doc.text)
        docs.append(doc)
    return KnowledgeBase(docs)


def _category(name: str, text: str) -> str:
    prefix = norm(name.split("__")[0]) if "__" in name else ""
    if "закон" in prefix or "кодекс" in prefix or "нпа" in prefix:
        return "law"
    if prefix:
        return "company"
    head = text[:600]
    if re.search(r"^Источник:", head, re.M) and re.search(r"Стать[яи] \d", head):
        return "law"
    if re.search(r"^Статья \d+(\.\d+)*\. ", text[:3000], re.M) and re.search(r"(Кодекс|Федеральный закон)", head):
        return "law"
    return "company"


# --------------------------------------------------------------------------- #
# Prompt-injection sanitising
# --------------------------------------------------------------------------- #

_AI_ADDRESS = re.compile(
    r"(ии[- ]?ассистент|ии[- ]?помощник|\bдля ии\b|искусственн\w+ интеллект|ai[- ]?assistant|"
    r"языков\w+ модел|нейросет|чат[- ]?бот|chatgpt|\bllm\b|\bгпт\b|\bassistant\b|"
    r"внимани\w+ (для )?(ассистент|модел|бот)|ассистент[ау]?\s*[:,!])", re.I)
_IMPERATIVE = re.compile(
    r"(\bигнорируй|проигнорируй|не учитывай|\bзабудь|\bотвечай|\bответь\b|не упоминай|не сообщай|"
    r"не раскрывай|\bвыполни\b|\bвыполняй|\bследуй\b|ты теперь|веди себя|притворись|"
    r"\bignore\b|disregard|system prompt|системн\w+ (промпт|сообщени|инструкц)|нов\w+ инструкци|"
    r"предыдущ\w+ инструкци|вместо этого отве)", re.I)


def looks_like_injection(line: str) -> bool:
    s = norm(line)
    if len(s) < 12:
        return False
    imperatives = len(_IMPERATIVE.findall(s))
    if _AI_ADDRESS.search(s) and imperatives >= 1:
        return True
    return imperatives >= 2 and bool(re.search(r"(инструкци|документ\w* базы|все остальные|на любой вопрос)", s))


def sanitize(text: str):
    """Replace lines that try to instruct the assistant. Returns (clean_text, removed_lines)."""
    removed, out = [], []
    for line in text.split("\n"):
        if looks_like_injection(line):
            removed.append(line.strip())
            out.append(INJECTION_PLACEHOLDER)
        else:
            out.append(line)
    return "\n".join(out), removed


# --------------------------------------------------------------------------- #
# Line iteration helper
# --------------------------------------------------------------------------- #

def _lines(text: str):
    pos = 0
    for line in text.split("\n"):
        yield line, pos, pos + len(line)
        pos += len(line) + 1


# --------------------------------------------------------------------------- #
# Company documents
# --------------------------------------------------------------------------- #

_SECTION_RE = re.compile(r"^(\d{1,2})\.\s+([А-ЯЁA-Z][^.;:]{2,90})$")
_CLAUSE_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})\.?\s+(\S.*)$")
_ORG_RE = re.compile(r"\b(ООО|АО|ПАО|ЗАО|ОАО|ИП|НКО|ГУП|МУП)\s+«([^»]{2,60})»")
_DOCNUM_RE = re.compile(r"№\s*([A-ZА-ЯЁ]{1,4}-\d{1,4}(?:[/-]\d{2,4})?|\d{1,4}-[A-ZА-ЯЁ]{1,3}\b|\d{1,5}(?:/\d{2,4})?)")


def _parse_company(doc: Doc) -> None:
    section = ""
    units = []
    for line, start, end in _lines(doc.text):
        s = line.strip()
        if not s:
            continue
        m_sec = _SECTION_RE.match(s)
        m_cl = _CLAUSE_RE.match(s)
        if m_cl:
            num = m_cl.group(1)
            units.append(Unit(doc.doc_id, len(units), "clause", f"п. {num}", (num,), section, s, [], start, end))
        elif m_sec and not s.endswith((".", ";")):
            section = f"{m_sec.group(1)}. {m_sec.group(2)}"
            units.append(Unit(doc.doc_id, len(units), "section", f"раздел {m_sec.group(1)}", (), section, s, [], start, end))
        else:
            kind = "head" if (not section and len(units) < 4 and not any(u.kind == "clause" for u in units)) else "para"
            units.append(Unit(doc.doc_id, len(units), kind, "", (), section, s, [], start, end))
    doc.units = units
    _company_meta(doc)


def _first_date(text: str):
    dates = find_dates(text)
    return dates[0][:3] if dates else None


def _company_meta(doc: Doc) -> None:
    stem_name = Path(doc.filename).stem
    title = stem_name.split("__", 1)[1] if "__" in stem_name else stem_name
    head = doc.text[:1500]
    lines = [ln.strip() for ln in doc.text.split("\n") if ln.strip()]
    meta: dict = {}

    for m in _DOCNUM_RE.finditer(head[:700]):
        if re.search(r"(приказ\w*|закон\w*|инн|кпп|огрн)\s*$", head[max(0, m.start() - 12):m.start()], re.I):
            continue                       # number of an order / tax id, not of the document itself
        if re.search(r"(склад|кабинет|офис|дом|д\.|стр\.|кв\.)\s*$", head[max(0, m.start() - 10):m.start()], re.I):
            continue
        meta["number"] = m.group(1)
        break

    # validity markers of an edition
    m = re.search(r"утратил\w*\s+силу\s+с\s+([^\n]{0,40})", head, re.I)
    if m:
        d = _first_date(m.group(1))
        if d:
            meta["valid_to"] = d
    m = re.search(r"Редакция от\s+([^\n]{0,30})", head)
    if m:
        d = _first_date(m.group(1))
        if d:
            meta["edition"] = d
    m = re.search(r"Действует с\s+([^\n]{0,30})", head)
    if m:
        d = _first_date(m.group(1))
        if d:
            meta["valid_from"] = d
    if "edition" in meta and "valid_from" not in meta:
        meta["valid_from"] = meta["edition"]

    # relation to a parent document (additional agreements)
    m = re.search(r"\bк\s+договору[^\n№]{0,60}№\s*([A-ZА-ЯЁ]{1,4}-\d{1,4}(?:[/-]\d{2,4})?|\d{1,5}(?:/\d{2,4})?)",
                  head, re.I)
    if m and re.search(r"(дополнительн\w+ соглашени|доп\.? ?соглашени|приложени|спецификаци)", norm(lines[0] + " " + title)):
        meta["parent_number"] = m.group(1)
        m_own = re.search(r"соглашени\w*\s+№\s*(\d+)", head, re.I)
        meta["number"] = ("ДС № " + m_own.group(1)) if m_own else None

    # document date: prefer the "city — date" line, then "№ ... от <date>", then the first date
    doc_date = meta.get("edition")
    for ln in ([] if doc_date else lines[:8]):
        if re.search(r"[—–-]\s*\d{1,2}\s+\S+\s+\d{4}\s*г?\.?$", ln) or re.search(r"[—–-]\s*\d{1,2}\.\d{1,2}\.\d{4}\s*г?\.?$", ln):
            dd = find_dates(ln)
            if dd:
                doc_date = dd[-1][:3]
                break
    if not doc_date and "parent_number" not in meta:
        for ln in lines[:8]:
            m2 = re.search(r"№\s*\S+\s+от\s+(.{0,25})", ln)
            if m2 and _first_date(m2.group(1)):
                doc_date = _first_date(m2.group(1))
                break
    if not doc_date:
        doc_date = meta.get("edition") or meta.get("valid_from")
    if not doc_date:
        skip = norm(head[:300])
        if "утратил" not in skip:
            doc_date = _first_date(head[:500])
    if doc_date:
        meta["date"] = doc_date

    orgs = []
    for m in _ORG_RE.finditer(doc.text[:2500]):
        name = f"{m.group(1)} «{m.group(2)}»"
        if name not in orgs:
            orgs.append(name)
    meta["orgs"] = orgs
    meta["first_line"] = lines[0] if lines else title
    doc.meta = meta
    doc.title = title


def company_status(doc: Doc) -> str:
    """One-line human-readable status used in prompts and in the UI."""
    m = doc.meta
    parts = []
    if m.get("valid_to"):
        parts.append(f"УТРАТИЛ СИЛУ с {fmt_date(*m['valid_to'])}")
        if m.get("valid_from"):
            parts.append(f"действовал с {fmt_date(*m['valid_from'])} по день, предшествующий {fmt_date(*m['valid_to'])}")
    elif m.get("valid_from"):
        parts.append(f"действующая редакция, действует с {fmt_date(*m['valid_from'])}")
    if m.get("parent_number"):
        parts.append(f"изменяет/дополняет договор № {m['parent_number']}")
    if m.get("date") and not m.get("valid_from"):
        parts.append(f"дата документа {fmt_date(*m['date'])}")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Laws
# --------------------------------------------------------------------------- #

_ARTICLE_RE = re.compile(r"^Статья\s+(\d+(?:\.\d+)*)\.?\s*(.*)$")
_POINT_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,2})*(?:-\d+)?)\.\s+(\S.*)$")
_SUBPOINT_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,2})*(?:-\d+)?)\)\s+(\S.*)$")
_LETTER_RE = re.compile(r"^([а-я])\)\s+(\S.*)$")
_REMARK_RE = re.compile(r"^Примечани[ея]\s*[:.]?\s*(.*)$")

_NOTE_RES = [re.compile(p, re.I) for p in (
    r"^(?:Стать[яюи]|Пункт[ыа]?|Подпункт[ыа]?|Част[ьи]|Абзац[ыа]?|Примечани[ея]|Наименование|Глава)\b"
    r".{0,160}?\b(?:изменен[аоы]?|дополнен[аоы]?|изложен[аоы]?)\b.{0,200}(?:Федеральн|закон)",
    r"^Федеральн(?:ым|ыми) закон(?:ом|ами) от ",
    r"^(?:Действие положений|Положения) (?:настоящ\w+ )?(?:стат\w+|пункт\w*|подпункт\w*|част\w*|абзац\w*)\b"
    r"[^()]{0,90}\(в редакции Федеральн",
    r"^(?:Пункт\w*|Подпункт\w*|Част\w*|Абзац\w*|Стать\w*)\s[^.:;]{0,80}?\b"
    r"(?:применя[ею]тся (?:с|в отношении)|распространя[ею]тся на правоотношения|вступа[ею]т в силу)",
    r"^Об? \S.{0,240}\bсм\. ",
    r"^См\.",
    r"^[CС]рок давности привлечения",
    r"^Информация об изменениях",
    r"^В соответствии с Федеральным законом от \d+ \w+ \d{4} г\.? N \S+ \(в редакции",
    r"^Действие (?:пункт\w*|подпункт\w*|част\w*|стать\w*|абзац\w*)\b.{0,160}(?:приостановлен|продлен)",
    r"^(?:ГАРАНТ|КонсультантПлюс)\s*:",
)]
# service lines of the legal database that carry no legal content at all
_JUNK_RES = [re.compile(p, re.I) for p in (
    r"воспользуйтесь калькулятором",
    r"^См\. (?:комментари|схем|справк|энциклопеди)",
)]
_NOTE_DATE_RE = re.compile(r"\bс\s+(\d{1,2}\s+\S+\s+\d{4}|\d{1,2}\.\d{1,2}\.\d{4})")

_CODES = (
    (r"Налогов\w+ кодекс", "НК РФ", "п.", "подп."),
    (r"Трудов\w+ кодекс", "ТК РФ", "ч.", "п."),
    (r"Гражданск\w+ кодекс", "ГК РФ", "п.", "подп."),
    (r"Кодекс\w* Российской Федерации об административных правонарушениях", "КоАП РФ", "ч.", "п."),
    (r"Уголовн\w+ кодекс", "УК РФ", "ч.", "п."),
    (r"Бюджетн\w+ кодекс", "БК РФ", "п.", "подп."),
    (r"Земельн\w+ кодекс", "ЗК РФ", "п.", "подп."),
    (r"Жилищн\w+ кодекс", "ЖК РФ", "ч.", "п."),
    (r"Арбитражн\w+ процессуальн\w+ кодекс", "АПК РФ", "ч.", "п."),
)


def is_editorial_note(line: str) -> bool:
    return any(r.search(line) for r in _NOTE_RES)


def is_junk(line: str) -> bool:
    return any(r.search(line) for r in _JUNK_RES)


def _law_identity(doc: Doc, header: dict) -> None:
    src = header.get("Источник", "")
    probe = src + " " + doc.filename
    code, point_word, sub_word = "", "ч.", "п."
    for pattern, abbr, pw, sw in _CODES:
        if re.search(pattern, probe, re.I) or re.search(r"\b" + re.escape(abbr.split()[0]) + r"\b", probe):
            code, point_word, sub_word = abbr, pw, sw
            break
    law_title = ""
    if not code:
        m = re.search(r"N\s*(\d+-ФЗ)\s*\"([^\"]+)\"", src) or re.search(r"№\s*(\d+-ФЗ)\s*[«\"]([^»\"]+)[»\"]", src)
        if m:
            code, law_title = m.group(1), m.group(2)
        else:
            m = re.search(r"(\d+)\s*-?\s*ФЗ", doc.filename)
            code = (m.group(1) + "-ФЗ") if m else "закон"
    doc.meta.update({"code": code, "law_title": law_title, "point_word": point_word, "sub_word": sub_word,
                     "source": src, "url": header.get("URL", ""), "export_date": _first_date(header.get("Дата выгрузки", ""))})


def _parse_law(doc: Doc) -> None:
    header: dict = {}
    body_started = False
    article_num, article_title = "", ""
    pending_notes: list = []
    doc_notes: list = []
    units: list = []
    rows = []
    for line, start, end in _lines(doc.text):
        s = line.strip()
        if s and not is_junk(s):
            rows.append((s, start, end))

    numbered = sum(1 for s, _, _ in rows if _POINT_RE.match(s)) >= 2
    i = 0
    # header block
    while i < len(rows):
        m = re.match(r"^(Источник|URL|Дата выгрузки)\s*:\s*(.*)$", rows[i][0])
        if not m:
            break
        header[m.group(1)] = m.group(2).strip()
        i += 1
    _law_identity(doc, header)
    pw, sw = doc.meta["point_word"], doc.meta["sub_word"]

    cur_point = None       # Unit
    cur_unit = None        # Unit currently receiving paragraphs
    in_remarks = False
    in_list = False        # for unnumbered laws: inside "…:" list
    part_no = 0

    def new_unit(kind, locator, keys, heading, text, start, end):
        nonlocal pending_notes
        u = Unit(doc.doc_id, len(units), kind, locator, keys, heading, text, pending_notes, start, end)
        pending_notes = []
        units.append(u)
        return u

    while i < len(rows):
        s, start, end = rows[i]
        i += 1
        if not body_started:
            m = _ARTICLE_RE.match(s)
            if m and not is_editorial_note(s):
                body_started = True
                article_num, article_title = m.group(1), m.group(2).strip()
                doc_notes, pending_notes = pending_notes, []
                # an article "title" that is itself a norm (e.g. the minimum wage law)
                if article_title and article_title.endswith(".") and len(article_title) > 40:
                    part_no += 1
                    cur_unit = new_unit("part", f"{pw} {part_no}" if not numbered else "", (str(part_no),),
                                        "", article_title, start, end)
                    if article_title.rstrip().endswith(":"):
                        in_list = True
                    article_title = ""
            else:
                pending_notes.append(s)
            continue

        if is_editorial_note(s) or s == INJECTION_PLACEHOLDER:
            pending_notes.append(s)
            continue

        m_rem = _REMARK_RE.match(s)
        if m_rem and len(s) < 400 and (numbered or s.rstrip(":. ").lower() in ("примечания", "примечание")):
            in_remarks = True
            cur_point = None
            cur_unit = new_unit("remark", "примечания", ("прим",), "", s, start, end)
            continue

        m_pt = _POINT_RE.match(s) if numbered else None
        m_sp = _SUBPOINT_RE.match(s)
        if m_pt:
            num = m_pt.group(1)
            if in_remarks:
                cur_unit = new_unit("remark", f"примечание {num}", ("прим", num), "", s, start, end)
                cur_point = cur_unit
            else:
                cur_point = cur_unit = new_unit("point", f"{pw} {num}", (num,), "", s, start, end)
            continue
        if m_sp and numbered and cur_point is not None:
            num = m_sp.group(1)
            lead = cur_point.text.split("\n")[0]
            loc = f"{cur_point.locator}, {sw} {num}"
            cur_unit = new_unit("subpoint", loc, cur_point.keys + (num,), lead[:300], s, start, end)
            continue

        if numbered:
            if cur_unit is None:
                cur_unit = new_unit("para", "", (), "", s, start, end)
            else:
                if pending_notes:
                    cur_unit.notes.extend(pending_notes)
                    pending_notes = []
                cur_unit.text += "\n" + s
                cur_unit.end = end
            continue

        # unnumbered law: paragraphs are parts, "…:" opens a list that belongs to the same part
        if in_list and cur_unit is not None:
            if pending_notes:
                cur_unit.notes.extend(pending_notes)
                pending_notes = []
            cur_unit.text += "\n" + s
            cur_unit.end = end
            if not s.rstrip().endswith((";", ":", ",")):
                in_list = False
            continue
        part_no += 1
        cur_unit = new_unit("part", f"{pw} {part_no}", (str(part_no),), "", s, start, end)
        in_list = s.rstrip().endswith(":")

    if pending_notes:
        if units:
            units[-1].notes.extend(pending_notes)
        else:
            doc_notes.extend(pending_notes)

    for u in units:
        dates = []
        for note in u.notes:
            if re.search(r"(изменен|дополнен|изложен)", note, re.I):
                for m in _NOTE_DATE_RE.finditer(note):
                    d = _first_date(m.group(1))
                    if d:
                        dates.append(d)
                        break
        if dates:
            u.notes_dates = dates  # type: ignore[attr-defined]

    code = doc.meta["code"]
    cite = f"{code}, ст. {article_num}" if article_num else code
    law_title = doc.meta.get("law_title") or ""
    doc.meta.update({"article": article_num, "article_title": article_title, "cite": cite,
                     "doc_notes": doc_notes, "numbered": numbered})
    doc.title = cite + (f" «{article_title}»" if article_title else "") + \
        (f" (Федеральный закон «{law_title}»)" if law_title else "")
    doc.units = units


def law_status(doc: Doc) -> str:
    exp = doc.meta.get("export_date")
    return "действующая редакция" + (f", выгрузка от {fmt_date(*exp)}" if exp else "") + \
        "; прежних редакций в базе нет"


# --------------------------------------------------------------------------- #
# Chunks and rendering
# --------------------------------------------------------------------------- #

MAX_CHUNK_CHARS = 1500


def unit_label(doc: Doc, u: Unit) -> str:
    return f"[{u.locator}]" if u.locator else ""


def render_unit(doc: Doc, u: Unit, with_lead: bool = True, text: str = "") -> str:
    """How a law unit is shown to the model: locator, editorial notes, parent lead-in, text."""
    body = text or u.text
    head = unit_label(doc, u)
    lines = []
    if u.notes:
        lines.append("(примечания редакции: " + " | ".join(n[:260] for n in u.notes[:4]) + ")")
    if with_lead and u.kind == "subpoint" and u.heading:
        lines.append(f"(вводная часть: «{u.heading}»)")
    lines.append(body)
    return (head + " " if head else "") + "\n".join(lines)


def _split_long(text: str, limit: int) -> list:
    if len(text) <= limit:
        return [text]
    paras = text.split("\n")
    lead = paras[0][:300]
    pieces, cur = [], ""
    for p in paras:
        while len(p) > limit:                      # a single huge paragraph: cut on sentence borders
            cut = p.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 3 else limit
            head, p = p[:cut], p[cut:].lstrip()
            if cur:
                pieces.append(cur)
                cur = ""
            pieces.append(head)
        if cur and len(cur) + len(p) + 1 > limit:
            pieces.append(cur)
            cur = ""
        cur = (cur + "\n" + p) if cur else p
    if cur:
        pieces.append(cur)
    out = [pieces[0]]
    for piece in pieces[1:]:
        out.append(f"{lead} […]\n{piece}" if not piece.startswith(lead[:60]) else piece)
    return out


_TOPIC_RE = re.compile(
    r"(налог\w* на добавленную стоимость|налог\w* на прибыль(?: организаций)?|налог\w* на доходы физических лиц|"
    r"персональн\w+ данн\w+|минимальн\w+ размер\w* оплаты труда|сверхурочн\w+ работ\w+|срочн\w+ трудов\w+ договор\w*|"
    r"испытани\w+|административн\w+ штраф\w*|налогов\w+ санкци\w+|налогов\w+ правонарушени\w+|"
    r"страхов\w+ взнос\w+|налогов\w+ вычет\w*|трудов\w+ договор\w*)", re.I)


def _topic_terms(doc: Doc) -> str:
    """Frequent subject phrases of a document: helps route questions to articles with generic titles."""
    from collections import Counter
    counts = Counter(norm(m.group(1)) for m in _TOPIC_RE.finditer(doc.text))
    return ", ".join(t for t, _ in counts.most_common(3))


def _build_chunks(kb: KnowledgeBase) -> None:
    seq = 0
    for doc in kb.docs:
        if doc.category == "law":
            file_words = re.sub(r"[_\-]+", " ", Path(doc.filename).stem.split("__")[-1])
            prefix = f"{doc.title} ({file_words}; {_topic_terms(doc)})"
            for u in doc.units:
                for piece in _split_long(u.text, MAX_CHUNK_CHARS):
                    shown = render_unit(doc, u, text=piece)
                    search = f"{prefix}. {u.heading}\n{piece}"
                    kb.chunks.append(Chunk(doc.doc_id, u.idx, seq, shown, search))
                    seq += 1
        else:
            prefix = doc.title
            for u in doc.units:
                if u.kind == "section":
                    continue
                search = f"{prefix}. {u.heading}\n{u.text}"
                kb.chunks.append(Chunk(doc.doc_id, u.idx, seq, u.text, search))
                seq += 1


def render_company_doc(doc: Doc) -> str:
    status = company_status(doc)
    head = f"=== [{doc.doc_id}] {doc.title}" + (f" | {status}" if status else "") + " ==="
    return head + "\n" + doc.text


def render_law_full(doc: Doc) -> str:
    head = f"=== [{doc.doc_id}] {doc.title} | {law_status(doc)} | документ приведён полностью ==="
    lines = [head]
    notes = doc.meta.get("doc_notes") or []
    if notes:
        lines.append("(примечания редакции к статье: " + " | ".join(n[:260] for n in notes[:4]) + ")")
    for u in doc.units:
        lines.append(render_unit(doc, u, with_lead=False))
    return "\n".join(lines)


def render_law_fragments(doc: Doc, chunks: list) -> str:
    head = (f"=== [{doc.doc_id}] {doc.title} | {law_status(doc)} | документ большой, "
            f"показаны только релевантные фрагменты ===")
    lines = [head]
    notes = doc.meta.get("doc_notes") or []
    if notes:
        lines.append("(примечания редакции к статье: " + " | ".join(n[:260] for n in notes[:3]) + ")")
    last_unit = -1
    for ch in sorted(chunks, key=lambda c: c.seq):
        if last_unit != -1 and ch.unit_idx - last_unit > 1:
            lines.append("[…]")
        lines.append(ch.text)
        last_unit = ch.unit_idx
    return "\n".join(lines)


def catalog_line(doc: Doc) -> str:
    if doc.category == "law":
        return f"[{doc.doc_id}] {doc.title} — {law_status(doc)}"
    status = company_status(doc)
    return f"[{doc.doc_id}] {doc.title}" + (f" — {status}" if status else "")


# --------------------------------------------------------------------------- #
# Registry: counterparties, document numbers, twins
# --------------------------------------------------------------------------- #

def normalize_docnum(value: str) -> str:
    return re.sub(r"[^0-9a-zа-я]+", "", norm(value))


def _entity_stems(short: str) -> tuple:
    return tuple(stem(w) for w in words(short) if not w[0].isdigit())


def _build_registry(kb: KnowledgeBase) -> None:
    company = kb.company_docs
    seen: dict = {}
    for doc in company:
        for name in doc.meta.get("orgs", []):
            seen.setdefault(name, []).append(doc.doc_id)
        num = doc.meta.get("number")
        if num and not str(num).startswith("ДС"):
            kb.doc_numbers.setdefault(normalize_docnum(num), []).append(doc.doc_id)
        m = re.search(r"([A-ZА-ЯЁ]{1,4}-\d{1,4}-\d{4})", doc.filename)
        if m:
            ids = kb.doc_numbers.setdefault(normalize_docnum(m.group(1)), [])
            if doc.doc_id not in ids:
                ids.append(doc.doc_id)
    own_threshold = max(2, int(len(company) * 0.6))
    merged: dict = {}
    for name, ids in seen.items():
        short = re.search(r"«([^»]+)»", name).group(1)  # type: ignore[union-attr]
        key = _entity_stems(short)
        if key in merged:
            ent = merged[key]
            ent.doc_ids = sorted(set(ent.doc_ids) | set(ids), key=lambda x: (len(x), x))
            if short != short.upper():                      # prefer the mixed-case spelling
                ent.name, ent.short = name, short
        else:
            merged[key] = Entity(name, short, key, list(ids))
    for ent in merged.values():
        ent.own = len(ent.doc_ids) >= own_threshold
        kb.entities.append(ent)
    kb.entities.sort(key=lambda e: -len("".join(e.stems)))


def twins_of(kb: KnowledgeBase, entity: Entity) -> list:
    """Counterparties whose names are easily confused with `entity`."""
    from difflib import SequenceMatcher
    out = []
    a = "".join(entity.stems)
    for other in kb.entities:
        if other is entity or other.own:
            continue
        b = "".join(other.stems)
        if a.startswith(b) or b.startswith(a) or SequenceMatcher(None, a, b).ratio() >= 0.75:
            out.append(other)
    return out
