"""
Turn a model's written reply into something a person can actually be told.

The realtime model is trained on text and reaches for markdown the moment an
answer has structure - a table of plans, a bulleted list of options, a bold
heading. On a screen that is helpful. On a phone call the caller hears the
punctuation read out: "pipe Plan pipe Included interviews per month pipe",
followed by a row of hyphens. That is what prompted this module.

Two defences, and this is the second one. The prompt tells the assistant to
speak plainly (see conversation._SPOKEN_STYLE_RULE), which handles the common
case. This module handles the rest, because a model instruction is a request
and not a guarantee - the same reasoning that puts every other guardrail
outside the model (CLAUDE.md section 15).

The rule followed throughout: **remove syntax, never content**. A caller who
loses a price or a date to an over-eager cleanup is worse off than one who
hears a stray asterisk, so anything ambiguous is left alone. Hyphens inside
words and dates stay exactly as written for that reason.

A second, related job lives here too: canonicalizing a number that the model
spelled out as words ("fifty", "three hundred") into digits, but only in the
narrow contexts where a number word cannot mean anything else. Reported live:
a reply mixed "1" and "one" for the same value in one breath, which reads as
sloppy or as two different facts. See _normalize_spoken_numbers for the exact
scope and why it stops short of every "one" in the language.
"""

import re

# ```sql ... ``` - the fence goes, whatever it was wrapping stays.
_CODE_FENCE = re.compile(r"^\s*```.*$", re.MULTILINE)
_INLINE_CODE = re.compile(r"`([^`]*)`")

# ![alt](url) before [text](url), so an image's "!" does not survive as a
# stray character once the link form has consumed the brackets.
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

# |------|:----:|---| - a table's separator row carries no words at all, so
# it is dropped rather than cleaned. This is the row that gets read out as a
# long run of hyphens.
_TABLE_RULE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$")

# --- or *** on its own line: a horizontal rule, also wordless.
_THEMATIC_BREAK = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")

_TABLE_ROW = re.compile(r"^\s*\|.*\|?\s*$")

_HEADING = re.compile(r"^\s*#{1,6}\s*")
_BLOCKQUOTE = re.compile(r"^\s*>\s*")
_BULLET = re.compile(r"^\s*[-*+•]\s+")

_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
_ITALIC_STAR = re.compile(r"\*(\S(?:.*?\S)?)\*", re.DOTALL)
_BOLD_UNDERSCORE = re.compile(r"__(.+?)__", re.DOTALL)
# Only when the underscores wrap a word, so snake_case identifiers survive.
_ITALIC_UNDERSCORE = re.compile(r"(?<![A-Za-z0-9_])_(\S(?:.*?\S)?)_(?![A-Za-z0-9_])")

# --- number-word normalization -------------------------------------------
#
# A spelled-out number ("fifty", "three hundred") is converted to digits, but
# only where that cannot collide with an ordinary, non-numeric use of the
# same word - "one" above all, which is also a pronoun ("the one you
# mentioned") and hides inside "no one" and "someone". Converted only when:
#
# - the phrase names a scale ("hundred", "thousand") - there is no non-
#   numeric English use of those words, so "three hundred" always means 300
#   regardless of what is nearby, or
# - the phrase sits immediately next to a currency word or an am/pm marker -
#   "twenty dollars", "nine am" - contexts where a number word can only mean
#   a number.
#
# Everything else - bare "one", "two", "fifty" with nothing either side - is
# left exactly as written, the same selective standard guardrails.py's own
# _MONEY/_CLOCK patterns already apply to digit form (see that module's
# docstring: "Prose numbers... are left alone").
#
# Known, deliberate gap: "a" ("a hundred", "a thousand") is not a recognised
# token. It is too overloaded - "a call", "a dollar", "a person" - to add
# safely even within this narrow scope, so those phrases stay spelled out.
#
# This closes a real gap, not only a style one: _MONEY and _CLOCK require a
# digit character, so a price or time stated in words reached the caller
# with no grounding check at all before this ran - "That will cost you
# twenty dollars" passed CLAUDE.md section 15's ungrounded-price guardrail
# purely because it had no digit in it.
_ONES_OR_TEENS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_ONES_UNIT = {word: value for word, value in _ONES_OR_TEENS.items() if 1 <= value <= 9}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALE_WORD = "hundred"

_NUMBER_WORD_TOKENS = frozenset({*_ONES_OR_TEENS, *_TENS, _SCALE_WORD})
_NUMBER_WORD_ALTERNATION = "|".join(sorted(_NUMBER_WORD_TOKENS, key=len, reverse=True))

# A run of one or more number-word tokens, joined by a space or hyphen - the
# two ways English compounds them ("twenty one", "twenty-one"). May over-match
# an invalid sequence ("twenty twenty") - _parse_number_words rejects those
# and the candidate is then left untouched, which is the safe direction.
_NUMBER_WORD_PHRASE = re.compile(
    rf"\b(?:{_NUMBER_WORD_ALTERNATION})(?:[\s-]+(?:{_NUMBER_WORD_ALTERNATION}))*\b",
    re.IGNORECASE,
)

_CURRENCY_WORD = re.compile(
    r"\s*(?:dollars?|pounds?|euros?|rupees?|usd|gbp|eur|inr)\b", re.IGNORECASE
)
_TIME_MARKER = re.compile(r"\s*(?:am|pm)\b", re.IGNORECASE)


def _parse_number_words(phrase: str) -> int | None:
    """
    "three hundred" -> 300, "twenty one" -> 21, "fifty" -> 50, or None if the
    tokens do not form a valid number (a stray "hundred" with nothing before
    it, or two tens words in a row).

    Bounded to 0-999. Every quantity, price, and count in this project's own
    data - interview allowances, seat counts, subscription prices - sits
    comfortably under that; reliably parsing "twelve hundred" alongside "one
    thousand two hundred" is a materially bigger grammar than this domain has
    needed yet, and the safe direction on a phrase this cannot parse is to
    leave it as words, not guess.
    """

    tokens = [t for t in re.split(r"[\s-]+", phrase.strip().lower()) if t]

    def below_hundred(remaining: list[str]) -> tuple[int, list[str]] | None:
        if not remaining:
            return None

        head, rest = remaining[0], remaining[1:]

        if head in _ONES_OR_TEENS:
            return _ONES_OR_TEENS[head], rest

        if head in _TENS:
            value = _TENS[head]

            if rest and rest[0] in _ONES_UNIT:
                return value + _ONES_UNIT[rest[0]], rest[1:]

            return value, rest

        return None

    parsed = below_hundred(tokens)

    if parsed is None:
        return None

    value, rest = parsed

    if rest and rest[0] == _SCALE_WORD:
        value *= 100
        rest = rest[1:]

        if rest:
            remainder = below_hundred(rest)

            if remainder is None:
                return None

            remainder_value, rest = remainder
            value += remainder_value

    return value if not rest else None


def _normalize_spoken_numbers(text: str) -> str:
    """Rewrite an unambiguous spelled-out number as digits."""

    def _replace(match: re.Match[str]) -> str:
        phrase = match.group(0)
        value = _parse_number_words(phrase)

        if value is None:
            return phrase

        names_a_scale = _SCALE_WORD in phrase.lower()
        tail = text[match.end() :]
        next_to_context = bool(_CURRENCY_WORD.match(tail) or _TIME_MARKER.match(tail))

        return str(value) if names_a_scale or next_to_context else phrase

    return _NUMBER_WORD_PHRASE.sub(_replace, text)


# A dash between digits reads as a range: "1-2 seats", "9-5". Only the typed
# en/em dash is converted, never the ASCII hyphen - "2026-09-07" is a date and
# must not become "2026 to 09 to 07".
_RANGE_DASH = re.compile(r"(?<=\d)\s*[–—]\s*(?=\d)")

# A spaced dash is punctuation, not part of a word: "Lite - 50 interviews".
# Requiring the spaces is what protects "follow-up" and "2026-09-07".
_SPACED_DASH = re.compile(r"\s+[-–—]+\s+")

# Whatever markdown punctuation survived the passes above, once it is certain
# no word is riding on it.
_LEFTOVER_SYNTAX = re.compile(r"[|*#`]+")

_WHITESPACE = re.compile(r"\s+")

_SENTENCE_END = ".!?,:;"


def _clean_line(line: str) -> str:
    """Strip one line's markdown structure, keeping its words."""

    if _TABLE_ROW.match(line) and "|" in line:
        # Cells become a comma-separated run, so the row is spoken with the
        # pauses that the pipes were standing in for.
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        line = ", ".join(cell for cell in cells if cell)

    line = _HEADING.sub("", line)
    line = _BLOCKQUOTE.sub("", line)
    line = _BULLET.sub("", line)

    return line


def to_spoken_text(text: str) -> str:
    """
    Rewrite `text` as plain speech: same words, no markup.

    Pure and total - the same input always gives the same output, it never
    raises and never returns None. It sits in the turn path in front of the
    TTS provider, so a failure here would cost the caller the reply itself.
    """

    if not text:
        return text

    working = _CODE_FENCE.sub("", text)
    working = _INLINE_CODE.sub(r"\1", working)
    working = _IMAGE.sub(r"\1", working)
    working = _LINK.sub(r"\1", working)

    lines: list[str] = []

    for raw in working.splitlines():
        if _TABLE_RULE.match(raw) or _THEMATIC_BREAK.match(raw):
            continue

        cleaned = _clean_line(raw).strip()

        if cleaned:
            lines.append(cleaned)

    joined = ""

    for line in lines:
        if not joined:
            joined = line
        elif joined[-1] in _SENTENCE_END:
            joined = f"{joined} {line}"
        else:
            # Without this the last word of a table row runs straight into the
            # first word of the next one, as one breathless sentence.
            joined = f"{joined}. {line}"

    joined = _BOLD.sub(r"\1", joined)
    joined = _BOLD_UNDERSCORE.sub(r"\1", joined)
    joined = _ITALIC_STAR.sub(r"\1", joined)
    joined = _ITALIC_UNDERSCORE.sub(r"\1", joined)

    joined = _normalize_spoken_numbers(joined)

    joined = _RANGE_DASH.sub(" to ", joined)
    joined = _SPACED_DASH.sub(", ", joined)

    joined = _LEFTOVER_SYNTAX.sub("", joined)

    return _WHITESPACE.sub(" ", joined).strip()
