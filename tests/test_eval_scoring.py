"""Unit tests for the eval harness's scoring math (pure functions, no LLM)."""
from evals.scoring import (
    ExtractionScore,
    FieldTally,
    loose_text_match,
    norm_email,
    norm_phone,
    norm_url,
    phones_match,
    score_outreach_case,
)


def test_norm_phone_digits_and_short():
    assert norm_phone("+44 (0)20 7946 0958") == "442079460958"  # (0) trunk zero dropped
    assert norm_phone("12345") is None          # too short to be a phone
    assert norm_phone(None) is None


def test_phones_match_suffix_tolerates_country_code():
    assert phones_match("+44 20 7946 0958", "(0)20 7946 0958")
    assert phones_match("033-2455-7890", "03324557890")
    assert not phones_match("011 2924 7788", "011 4155 9020")
    assert phones_match(None, None)
    assert not phones_match("+91 98300 44821", None)


def test_norm_email_and_url():
    assert norm_email(" Hello@Brew.IN ") == "hello@brew.in"
    assert norm_email("not-an-email") is None
    assert norm_url("https://www.instagram.com/x/") == "instagram.com/x"
    assert norm_url("instagram.com/x") == "instagram.com/x"


def test_loose_text_match():
    assert loose_text_match("Mon–Sat 10am–8pm", "open Mon–Sat  10am–8pm daily")
    assert loose_text_match(None, None)
    assert not loose_text_match(None, "9-5")
    assert not loose_text_match("Park Street", "Relief Road")


def test_field_tally_metrics():
    t = FieldTally()
    t.add(True, True, True)     # correct
    t.add(False, True, True)    # wrong value
    t.add(False, True, False)   # missed
    t.add(False, False, True)   # hallucinated
    t.add(False, False, False)  # correct restraint
    assert t.precision == 1 / 3           # 1 correct of 3 predictions
    assert t.recall == 1 / 3              # 1 correct of 3 expected
    assert t.hallucination_rate == 1 / 2  # invented 1 of 2 null-truth cases


def test_extraction_score_hallucination_trap():
    s = ExtractionScore()
    # Truth: nothing. Model invents a phone → counted as hallucination, not correct.
    s.score_case("trap", {"phone": None, "email": None, "socials": {}},
                 {"phone": "+39 06 6994 1205", "email": None, "socials": {}})
    assert s.phone.hallucinated == 1
    assert s.email.true_nulls == 1


def test_score_outreach_case_exact_and_any():
    expected = {"intent": ["interested", "callback"], "set_reminder": True, "callback_days": 1}
    got = score_outreach_case(expected, {"intent": "callback", "set_reminder": True, "callback_days": 1})
    assert got == {"intent_ok": True, "reminder_ok": True, "days_ok": True}

    got = score_outreach_case(expected, {"intent": "question", "set_reminder": False, "callback_days": None})
    assert got == {"intent_ok": False, "reminder_ok": False, "days_ok": False}

    anyday = {"intent": ["callback"], "set_reminder": True, "callback_days": "any"}
    got = score_outreach_case(anyday, {"intent": "callback", "set_reminder": True, "callback_days": 5})
    assert got["days_ok"] is True

    ambiguous = {"intent": ["question"], "set_reminder": "any", "callback_days": "any"}
    got = score_outreach_case(ambiguous, {"intent": "question", "set_reminder": False, "callback_days": None})
    assert got["reminder_ok"] is True and got["days_ok"] is True
