"""
Item 24d step 1: the shared redaction rules.

The negative cases carry as much weight as the positive ones. These rules will
run over stored transcript text (item 29), where redacting a quoted price, a
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


@pytest.mark.parametrize(
    "spoken",
    [
        # Found in the running container's own logs as
        # "8b24fb84-f98a-[phone]-f4b46f6ab5a7". A UUID's groups are
        # hyphen-separated like a phone number, so two adjacent all-digit
        # groups offered the run scanner an eight-digit match. Intermittent by
        # nature - most UUIDs carry a hex letter in those groups and escape.
        "8b24fb84-f98a-4123-4567-f4b46f6ab5a7",
        "8B24FB84-F98A-4123-4567-F4B46F6AB5A7",
        "POST /workspaces/8b24fb84-f98a-4123-4567-f4b46f6ab5a7/knowledge-sources",
        # The identifiers CLAUDE.md section 27 asks be logged *instead of* the
        # content. Redacting them defeats the exchange.
        "call=14ce7019-51cc-4e0e-bbe5-4ec95703da93"
        " assistant=074be008-f8bb-4a69-928d-49a6ff0e9487",
    ],
)
def test_uuids_are_never_mistaken_for_a_phone_number(spoken: str) -> None:
    assert redact_pii(spoken) == spoken


@pytest.mark.parametrize(
    "spoken",
    [
        # Found in the running container's own logs as
        # "peak=[phone] of full scale)". The scanner treats a space as a
        # group separator, so "12345 (0" read as one six-digit run running on
        # into the decimal after it - a merge across a boundary, not one
        # number. It destroyed the audio-level diagnostic it existed to
        # provide, the same way the UUID case did.
        #
        # Each case here carries three or more decimal places, so none of
        # them is already saved by the trailing-amount rule above.
        "peak=12345 (0.377 of full scale)",
        "rms=8192 (0.2513 of full scale)",
        "latency=1024 (0.4096 seconds)",
        # A space rather than a bracket, and the decimal in the middle.
        "chunk 4501 3.1416 tokens",
    ],
)
def test_two_numbers_run_together_are_not_read_as_one_phone_number(
    spoken: str,
) -> None:
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
