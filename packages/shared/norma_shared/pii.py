"""
Item 24d: the project's single set of PII redaction rules.

`redact_pii` is used in two places with opposite risk profiles, and the rules
are tuned for the stricter one:

- **Logs**, right now, through `norma_shared.logging_setup` - here
  over-redaction costs nothing.
- **Stored transcript text**, once item 28 creates somewhere to store it -
  here over-redaction is a defect. An operator reading a call back needs the
  price the assistant quoted, the time it booked, and the house number it
  confirmed. A rule that eats those has broken the call-detail screen that
  CLAUDE.md section 25 calls the one that rebuilds trust after a mistake.

So the patterns are deliberately narrow, and the tests spend as much effort on
what must survive as on what must go.

Pattern-based only. Names and street addresses are not reliably detectable by
regex, and a model in this path is not affordable - the same reasoning that
kept an LLM judge out of the 24b output validator.
"""

import re

EMAIL_PLACEHOLDER = "[email]"
PHONE_PLACEHOLDER = "[phone]"
CARD_PLACEHOLDER = "[card]"
NUMBER_PLACEHOLDER = "[number]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Scanned left to right, first alternative wins.
#
# `protected` matches dates and clock times and hands them back untouched. It
# has to come first, and it has to be able to swallow a date *and* the time
# after it, because the digit run below treats a space as a group separator:
# without this, "2026-09-07 10:24:20" reads as one ten-digit run and becomes
# "[phone]:20". That was not hypothetical - it ate the timestamp of every log
# line this module formatted, found by reading the running container rather
# than the unit tests, and it would just as happily eat the date and time of
# an appointment out of a stored transcript.
#
# `run` is a run of digits grouped by spaces, dots, hyphens, or brackets - the
# shapes phone and card numbers are actually written in, by a caller speaking
# them or an STT provider transcribing them. Commas are deliberately NOT
# separators: "$1,234,567.89" would otherwise read as one nine-digit run.
_SCAN = re.compile(
    r"(?P<protected>"
    r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?"  # 2026-09-07, with optional time
    r"|\d{1,2}:\d{2}(?::\d{2})?"  # 9:30, 10:24:20
    r")"
    r"|(?P<run>\+?\d+(?:[\s().\-]{1,2}\d+)*)"
)

_DIGITS = re.compile(r"\d")

# Checked before the digit-count classification below, so it keeps its own
# meaning rather than being lumped in with phone numbers. Dates and times are
# handled by _SCAN's protected alternative instead, since they can run on past
# what a single run would match.
_US_SSN = re.compile(r"^\d{3}-\d{2}-\d{4}$")

# "1234567.89" - a bare decimal amount with no thousands separators. Two
# trailing decimal digits is money; four is the last group of a phone number
# written "555.123.4567", which must still be redacted.
_AMOUNT_TAIL = re.compile(r"\.\d{1,2}$")

_CURRENCY_PREFIX = re.compile(r"[$£€₹¥]\s?$")

# Card numbers run 13-19 digits (Visa 13/16, Amex 15, Maestro up to 19).
_CARD_MIN_DIGITS = 13
_CARD_MAX_DIGITS = 19

# Seven digits is a local subscriber number - the shortest run worth treating
# as a contact detail. Below it sit the things a transcript needs to keep:
# quantities, years, prices, room and suite numbers.
_PHONE_MIN_DIGITS = 7


def _classify(candidate: str, *, preceding: str) -> str | None:
    """
    Decide what a digit run is, or None to leave it exactly as written.
    """

    if _US_SSN.match(candidate):
        return NUMBER_PLACEHOLDER

    # An amount or a price is content the operator needs to read back, not a
    # contact detail.
    if _AMOUNT_TAIL.search(candidate):
        return None

    if _CURRENCY_PREFIX.search(preceding):
        return None

    digits = len(_DIGITS.findall(candidate))

    if _CARD_MIN_DIGITS <= digits <= _CARD_MAX_DIGITS:
        return CARD_PLACEHOLDER

    if digits > _CARD_MAX_DIGITS:
        return NUMBER_PLACEHOLDER

    if digits >= _PHONE_MIN_DIGITS:
        return PHONE_PLACEHOLDER

    return None


def redact_pii(text: str) -> str:
    """
    Replace personal details in `text` with fixed placeholders.

    Pure and total: the same input always produces the same output, it never
    raises, and it never returns None. Callers on the logging path depend on
    that - a redactor that can throw would take out the log line it was
    supposed to be protecting.

    Placeholders name what was removed (`[email]`, `[phone]`, `[card]`,
    `[number]`) so redacted text stays readable to a person reviewing it.
    """

    if not text:
        return text

    redacted = _EMAIL.sub(EMAIL_PLACEHOLDER, text)

    def _replace(match: re.Match[str]) -> str:
        run = match.group("run")

        if run is None:  # a protected date or time
            return match.group(0)

        placeholder = _classify(run, preceding=redacted[: match.start()])

        return placeholder if placeholder is not None else run

    return _SCAN.sub(_replace, redacted)
