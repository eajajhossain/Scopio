"""Pure scoring helpers for the eval suites (no I/O, unit-tested in tests/).

Field policy:
- STRICT fields (phone, email, socials): scored with precision/recall after
  normalization. A wrong value counts against precision; a miss against recall;
  a prediction where the truth is null counts as a hallucination.
- LOOSE fields (opening_hours, address): free-text, so we score presence
  discipline (found it when it exists / stayed null when it doesn't) plus a
  loose containment match — enough to catch regressions without punishing
  harmless re-phrasings.
"""
import re
from dataclasses import dataclass, field


def norm_phone(value: str | None) -> str | None:
    """Digits only. `None` stays `None`; too-short strings normalize to None.
    Drops the UK-style parenthesized trunk zero ('+44 (0)20 …' → '+44 20 …')."""
    if not value:
        return None
    digits = re.sub(r"\D", "", re.sub(r"\(0\)", "", value))
    return digits if len(digits) >= 8 else None


def phones_match(expected: str | None, predicted: str | None) -> bool:
    """Suffix match on digits, tolerating country codes and trunk zeros
    ('+44 20 7946 0958' == '(0)20 7946 0958' == '020 7946 0958')."""
    e, p = norm_phone(expected), norm_phone(predicted)
    if e is None or p is None:
        return e == p
    e, p = e.lstrip("0"), p.lstrip("0")
    return e.endswith(p) or p.endswith(e)


def norm_email(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    return v if "@" in v else None


def norm_url(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "", v)
    return v.rstrip("/") or None


def _norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def loose_text_match(expected: str | None, predicted: str | None) -> bool:
    """Both null, or one normalized string contains the other."""
    e, p = _norm_text(expected), _norm_text(predicted)
    if not e or not p:
        return e == p
    return e in p or p in e


@dataclass
class FieldTally:
    """Running precision/recall tally for one strict field across all cases."""
    correct: int = 0          # predicted non-null and matches truth
    wrong: int = 0            # predicted non-null but truth differs
    missed: int = 0           # truth non-null, predicted null
    hallucinated: int = 0     # truth null, predicted non-null
    true_nulls: int = 0       # truth null, predicted null (correct restraint)

    def add(self, matches: bool, expected_present: bool, predicted_present: bool) -> None:
        if expected_present and predicted_present:
            self.correct += matches
            self.wrong += not matches
        elif expected_present:
            self.missed += 1
        elif predicted_present:
            self.hallucinated += 1
        else:
            self.true_nulls += 1

    @property
    def precision(self) -> float | None:
        pred = self.correct + self.wrong + self.hallucinated
        return self.correct / pred if pred else None

    @property
    def recall(self) -> float | None:
        exp = self.correct + self.wrong + self.missed
        return self.correct / exp if exp else None

    @property
    def hallucination_rate(self) -> float | None:
        """Of the cases where the truth was null, how often did we invent a value?"""
        nulls = self.hallucinated + self.true_nulls
        return self.hallucinated / nulls if nulls else None

    def as_dict(self) -> dict:
        return {
            "correct": self.correct, "wrong": self.wrong, "missed": self.missed,
            "hallucinated": self.hallucinated, "true_nulls": self.true_nulls,
            "precision": self.precision, "recall": self.recall,
            "hallucination_rate": self.hallucination_rate,
        }


@dataclass
class ExtractionScore:
    """Aggregated scores for the extraction suite."""
    phone: FieldTally = field(default_factory=FieldTally)
    email: FieldTally = field(default_factory=FieldTally)
    socials: FieldTally = field(default_factory=FieldTally)
    hours_ok: int = 0
    hours_total: int = 0
    address_ok: int = 0
    address_total: int = 0
    case_notes: list[dict] = field(default_factory=list)

    def score_case(self, case_id: str, expected: dict, predicted: dict) -> None:
        exp_phone, pred_phone = expected.get("phone"), predicted.get("phone")
        self.phone.add(phones_match(exp_phone, pred_phone),
                       bool(norm_phone(exp_phone)), bool(norm_phone(pred_phone)))

        exp_email, pred_email = norm_email(expected.get("email")), norm_email(predicted.get("email"))
        self.email.add(exp_email == pred_email, bool(exp_email), bool(pred_email))

        # Socials: one tally entry per platform that appears in truth or prediction.
        exp_soc = {k: norm_url(v) for k, v in (expected.get("socials") or {}).items() if norm_url(v)}
        pred_soc = {k: norm_url(v) for k, v in (predicted.get("socials") or {}).items() if norm_url(v)}
        for platform in sorted(set(exp_soc) | set(pred_soc)):
            e, p = exp_soc.get(platform), pred_soc.get(platform)
            self.socials.add(e == p, e is not None, p is not None)

        for attr, exp_key in (("hours", "opening_hours"), ("address", "address")):
            ok = loose_text_match(expected.get(exp_key), predicted.get(exp_key))
            setattr(self, f"{attr}_ok", getattr(self, f"{attr}_ok") + ok)
            setattr(self, f"{attr}_total", getattr(self, f"{attr}_total") + 1)

        self.case_notes.append({
            "id": case_id,
            "phone": {"expected": exp_phone, "predicted": pred_phone,
                      "ok": phones_match(exp_phone, pred_phone)},
            "email": {"expected": exp_email, "predicted": pred_email, "ok": exp_email == pred_email},
        })

    def as_dict(self) -> dict:
        return {
            "phone": self.phone.as_dict(),
            "email": self.email.as_dict(),
            "socials": self.socials.as_dict(),
            "opening_hours_loose_acc": self.hours_ok / self.hours_total if self.hours_total else None,
            "address_loose_acc": self.address_ok / self.address_total if self.address_total else None,
            "cases": self.case_notes,
        }


def score_outreach_case(expected: dict, predicted: dict) -> dict:
    """Exact-match scoring for one conversational-agent turn.

    `expected["intent"]` is a list of acceptable labels (genuinely ambiguous turns
    accept more than one). `set_reminder` / `callback_days` may be "any" when the
    turn is too ambiguous to demand one answer — those score as correct either way.
    """
    intent_ok = predicted.get("intent") in expected["intent"]
    exp_reminder = expected["set_reminder"]
    if exp_reminder == "any":
        reminder_ok = True
    else:
        reminder_ok = bool(predicted.get("set_reminder")) == bool(exp_reminder)
    exp_days = expected.get("callback_days")
    if exp_days == "any":
        days_ok = True
    else:
        days_ok = predicted.get("callback_days") == exp_days
    return {"intent_ok": intent_ok, "reminder_ok": reminder_ok, "days_ok": days_ok}
