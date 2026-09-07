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

    joined = _RANGE_DASH.sub(" to ", joined)
    joined = _SPACED_DASH.sub(", ", joined)

    joined = _LEFTOVER_SYNTAX.sub("", joined)

    return _WHITESPACE.sub(" ", joined).strip()
