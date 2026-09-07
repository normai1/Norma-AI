"""
Item 24d step 1: the shared redaction rules.

The negative cases carry as much weight as the positive ones. These rules will
run over stored transcript text (item 28), where redacting a quoted price, a
booked time, or a date silently destroys the record an operator relies on -
a worse outcome than the leak the rules exist to prevent, because it is
invisible.
"""

import pytest
from norma_shared.pii import redact_pii


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Email me at john.doe@example.com please", "Email me at [email] please"),
        ("reach me on +1 (555) 123-4567", "reach me on [phone]"),
        ("my number is 555-123-4567", "my number is [phone]"),
        ("call 5551234567", "call [phone]"),
        ("card 4111 1111 1111 1111", "card [card]"),
        ("card 4111111111111111", "card [card]"),
        # 15 digits: an Amex, still card-length.
        ("amex 3782 822463 10005", "amex [card]"),
        ("ssn 123-45-6789", "ssn [number]"),
        ("ref 12345678901234567890123", "ref [number]"),
    ],
)
def test_personal_details_are_redacted(spoken: str, expected: str) -> None:
    assert redact_pii(spoken) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        # Everything an operator must still be able to read back.
        "That comes to $45.00 including tax.",
        "The total is $1,234,567.89 for the year.",
        "We open at 9:30 and close at 5.",
        "Booked for 2026-09-07 at 14:00.",
        "There are 3 people on the booking.",
        "We have been open since 1998.",
        "You are in Suite 200, room 415.",
        "It is 1234567.89 dollars.",
        "A deposit of 250 is due.",
        "Invoice total 999999.50",
    ],
)
def test_ordinary_business_detail_survives_untouched(spoken: str) -> None:
    assert redact_pii(spoken) == spoken


@pytest.mark.parametrize(
    "spoken",
    [
        # A logged timestamp. This regressed live: the run scanner treats a
        # space as a group separator, so "2026-09-07 10:24:20" read as one
        # ten-digit run and came out as "[phone]:20" - on every log line the
        # redacting formatter touched. The same bug would take the date and
        # time of an appointment out of a stored transcript.
        "2026-09-07 10:24:20,284 | INFO | session started",
        "Your appointment is 2026-09-07 at 14:30.",
        "Booked 2026-9-7 09:00 with the hygienist.",
        "We reopen 2027-01-02.",
    ],
)
def test_dates_and_times_are_never_mistaken_for_a_phone_number(spoken: str) -> None:
    assert redact_pii(spoken) == spoken


def test_a_phone_written_with_dots_is_still_redacted() -> None:
    """
    Four trailing digits after a dot is the last group of a phone number, not
    the cents of an amount - the distinction _AMOUNT_TAIL draws.
    """

    assert redact_pii("call 555.123.4567 now") == "call [phone] now"


def test_several_details_in_one_utterance() -> None:
    assert redact_pii("I am jo@x.com on 555-123-4567") == "I am [email] on [phone]"


def test_it_is_pure_and_total() -> None:
    """
    The logging path calls this on every record. A redactor that raises, or
    returns None, would take out the log line it exists to protect.
    """

    for value in ("", "no digits here at all", "@@@", "+", "-", "0", "." * 500):
        assert isinstance(redact_pii(value), str)

    assert redact_pii("") == ""
    assert redact_pii(redact_pii("call 5551234567")) == "call [phone]"
