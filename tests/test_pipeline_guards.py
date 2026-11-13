"""Publication guards and calendar validity: no network or model credentials."""

from datetime import date
from types import SimpleNamespace
import time

import pytest

from la_llm import LLMError
from la_pipeline import Assistant, Dialog, _period_bounds, date_hints
from la_retrieval import Retriever


GOOD_ANSWER = "Арендная плата составляет 450 000 рублей в месяц (п. 3.1)."
BAD_ANSWER = "Арендная плата составляет 777 777 777 рублей в месяц (п. 3.1)."
RENT_SOURCE = ("D1 | п. 3.1 | Арендная плата составляет 450 000 "
               "(четыреста пятьдесят тысяч) рублей в месяц")


def completion(answer, sources=RENT_SOURCE, status="ANSWER", extra="", analysis=""):
    return (f"<analysis>{analysis}</analysis><status>{status}</status><answer>{answer}</answer>"
            f"<extra>{extra}</extra><sources>{sources}</sources>")


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def assistant(kb, replies, *, repair=True, edit=False):
    llm = ScriptedLLM(replies)
    settings = SimpleNamespace(second_pass=False, repair=repair, edit=edit)
    return Assistant(kb, Retriever(kb), llm, settings), llm


@pytest.mark.parametrize("mode,repair,seconds,calls", [
    ("full", True, None, 2),
    ("lite", True, None, 1),
    ("full", False, None, 1),
    ("full", True, 5, 1),
    ("full", True, -1, 1),
])
def test_unresolved_hard_error_never_ships_in_any_budget_mode(kb, mode, repair, seconds, calls):
    raw = completion(BAD_ANSWER, extra="Неподтверждённое пояснение.", analysis="Неверный вывод 777.")
    bot, llm = assistant(kb, [raw, raw], repair=repair, edit=True)
    deadline = None if seconds is None else time.monotonic() + seconds
    result = bot.answer("Какова арендная плата?", mode=mode, deadline=deadline)

    assert result.status == "ERROR" and result.status != "NOT_IN_KB"
    assert "777" not in result.answer and "подтвердить" in result.answer
    assert not result.extra and not result.analysis and not result.sources
    assert any(issue["hard"] for issue in result.issues)
    assert llm.calls == calls                         # a blocked answer never reaches the optional editor


def test_repair_failure_preserves_safe_result_and_diagnostics(kb):
    bot, llm = assistant(kb, [completion(BAD_ANSWER), LLMError("deadline reached before request")])
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ERROR" and "777" not in result.answer
    assert "deadline" in result.error
    assert llm.calls == 2 and any(i["hard"] for i in result.issues)


def test_fewer_errors_after_repair_are_not_equivalent_to_verified_answer(kb):
    first = completion(BAD_ANSWER + " Контрагент — «Вымышленная организация».")
    bot, llm = assistant(kb, [first, completion(BAD_ANSWER)])
    result = bot.answer("Какова арендная плата?")
    assert result.repaired and result.status == "ERROR"
    assert "777" not in result.answer and not result.sources
    assert llm.calls == 2


def test_successful_repair_publishes_corrected_answer_and_sources(kb):
    bot, llm = assistant(kb, [completion(BAD_ANSWER), completion(GOOD_ANSWER)])
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ANSWER" and result.answer == GOOD_ANSWER and result.repaired
    assert not any(i["hard"] for i in result.issues)
    assert result.sources and all(s["ok"] for s in result.sources)
    assert llm.calls == 2


@pytest.mark.parametrize("status", ["PARTIAL", "NOT_IN_KB", "ILLEGAL"])
def test_model_status_cannot_bypass_a_hard_error(kb, status):
    bot, _ = assistant(kb, [completion(BAD_ANSWER, status=status)], repair=False)
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ERROR" and "777" not in result.answer


def test_fact_answer_without_required_source_is_not_published(kb):
    bot, _ = assistant(kb, [completion(GOOD_ANSWER, sources="")], repair=False)
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ERROR"
    assert any(i["type"] == "missing_source" for i in result.issues)


def test_honest_not_in_kb_response_does_not_require_a_fabricated_source(kb):
    answer = "В базе нет сведений по этому вопросу."
    bot, llm = assistant(kb, [completion(answer, sources="", status="NOT_IN_KB")])
    result = bot.answer("Какой размер пособия по беременности и родам?")
    assert result.status == "NOT_IN_KB" and result.answer == answer and llm.calls == 1


@pytest.mark.parametrize("answer,question,sources", [
    ("Плата — 450 000 рублей, а не 500 000 рублей (п. 3.1).",
     "Почему арендная плата 500 000 рублей?", RENT_SOURCE),
    ("Пени за 10 дней: 450 000 × 0,1% × 10 = 4 500 рублей (п. 3.1, п. 5.1).",
     "Каковы пени за 10 дней просрочки арендной платы?",
     RENT_SOURCE + "\nD1 | п. 5.1 | пени в размере 0,1% от суммы задолженности за каждый день просрочки"),
])
def test_user_numbers_remain_usable_for_rebuttals_and_calculations(kb, answer, question, sources):
    bot, llm = assistant(kb, [completion(answer, sources=sources)])
    result = bot.answer(question)
    assert result.status == "ANSWER" and result.answer == answer and llm.calls == 1


def test_changed_final_text_is_verified_again_without_another_model_call(kb, monkeypatch):
    import la_pipeline

    monkeypatch.setattr(la_pipeline, "trim_answer", lambda text: (BAD_ANSWER, ""))
    bot, llm = assistant(kb, [completion(GOOD_ANSWER)], repair=False)
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ERROR" and "777" not in result.answer
    assert llm.calls == 1


def test_explicit_model_error_is_not_published_as_a_legal_answer(kb):
    bot, llm = assistant(kb, [completion("Спорный юридический вывод.", status="ERROR")], edit=False)
    result = bot.answer("Каковы условия договора?")
    assert result.status == "ERROR" and "Спорный юридический вывод" not in result.answer
    assert result.sources == [] and result.error and llm.calls == 1


def test_final_check_uses_repaired_then_edited_text(kb):
    longer = GOOD_ANSWER + " Она вносится не позднее 5-го числа (п. 3.2)."
    bot, llm = assistant(kb, [completion(BAD_ANSWER), completion(longer), '{"delete": [2]}'], edit=True)
    result = bot.answer("Какова арендная плата?")
    assert result.status == "ANSWER" and result.answer == GOOD_ANSWER
    assert result.repaired and result.edited and result.checked_numbers == 2
    assert "5-го" in result.extra and "777" not in result.extra
    assert llm.calls == 3 and not any(i["hard"] for i in result.issues)


@pytest.mark.parametrize("period,start,end,days", [
    ((2024, 2, 0), date(2024, 2, 1), date(2024, 3, 1), 29),
    ((2023, 2, 0), date(2023, 2, 1), date(2023, 3, 1), 28),
    ((2024, 2, 29), date(2024, 2, 29), date(2024, 3, 1), 1),
    ((2025, 12, 0), date(2025, 12, 1), date(2026, 1, 1), 31),
    ((2025, 12, 31), date(2025, 12, 31), date(2026, 1, 1), 1),
    ((2024, 0, 0), date(2024, 1, 1), date(2025, 1, 1), 366),
    ((2023, 0, 0), date(2023, 1, 1), date(2024, 1, 1), 365),
])
def test_periods_have_exact_calendar_boundaries(period, start, end, days):
    assert _period_bounds(period) == (start, end)
    assert (end - start).days == days


def edition_hint(question, valid_from, valid_to):
    doc = SimpleNamespace(doc_id="D1", title="Регламент", meta={"valid_from": valid_from, "valid_to": valid_to})
    kb = SimpleNamespace(company_docs=[doc], law_docs=[], by_id={"D1": doc})
    return date_hints(kb, Dialog(question))[0]


@pytest.mark.parametrize("question,start,end,state", [
    ("В 2023 году?", (2023, 1, 10), (2024, 1, 1), "действовал только часть этого периода"),
    ("В 2023 году?", (2023, 1, 1), (2024, 1, 1), "ДЕЙСТВОВАЛ"),
    ("В феврале 2024 года?", (2024, 2, 1), (2024, 3, 1), "ДЕЙСТВОВАЛ"),
    ("В феврале 2024 года?", (2024, 2, 1), (2024, 2, 29), "действовал только часть этого периода"),
    ("На 28.02.2024?", (2024, 2, 1), (2024, 2, 29), "ДЕЙСТВОВАЛ"),
    ("На 29.02.2024?", (2024, 2, 1), (2024, 2, 29), "НЕ действовал"),
    ("На 01.03.2024?", (2024, 3, 1), None, "ДЕЙСТВОВАЛ"),
    ("На 31.12.2023?", (2023, 1, 1), (2024, 1, 1), "ДЕЙСТВОВАЛ"),
    ("На 01.01.2024?", (2023, 1, 1), (2024, 1, 1), "НЕ действовал"),
])
def test_validity_covers_the_whole_period_or_is_explicitly_partial(question, start, end, state):
    assert f"[D1] {state} (" in edition_hint(question, start, end)


def test_invalid_user_calendar_date_requests_clarification(kb):
    hints = date_hints(kb, Dialog("Действовал ли регламент на 29.02.2023?"))
    assert any("некорректна" in h and "уточнить" in h for h in hints)
    assert not any("ДЕЙСТВОВАЛ" in h for h in hints)


def test_future_documents_and_law_exports_use_the_end_of_the_whole_period():
    documents = [
        SimpleNamespace(doc_id="D1", title="Документ февраля", meta={"date": (2024, 2, 29)}),
        SimpleNamespace(doc_id="D2", title="Документ марта", meta={"date": (2024, 3, 1)}),
    ]
    kb = SimpleNamespace(company_docs=documents, by_id={d.doc_id: d for d in documents},
                         law_docs=[SimpleNamespace(meta={"export_date": (2024, 3, 1)})])
    hints = date_hints(kb, Dialog("Какие правила были в феврале 2024 года?"))
    assert any("[D2]" in h and "позже" in h for h in hints)
    assert not any("[D1]" in h and "позже" in h for h in hints)
    assert any("выгрузка 01.03.2024" in h for h in hints)
