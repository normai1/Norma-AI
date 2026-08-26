import re

from app.core.slug import MAX_SLUG_LENGTH, slugify

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def test_simple_name_becomes_readable_slug() -> None:
    assert slugify("Acme Corp") == "acme-corp"


def test_case_and_surrounding_whitespace_are_normalized() -> None:
    assert slugify("  ACME   Corp  ") == "acme-corp"


def test_repeated_separators_collapse() -> None:
    assert slugify("Acme -- & ++ Corp") == "acme-corp"


def test_punctuation_does_not_leave_trailing_hyphens() -> None:
    assert slugify("Acme Corp!!!") == "acme-corp"
    assert slugify("...Acme...") == "acme"


def test_symbol_only_name_falls_back_instead_of_empty() -> None:
    slug = slugify("!!!")

    assert slug.startswith("org-")
    assert SLUG_PATTERN.match(slug)


def test_non_latin_name_falls_back_instead_of_empty() -> None:
    slug = slugify("株式会社")

    assert slug.startswith("org-")
    assert SLUG_PATTERN.match(slug)


def test_fallback_is_random_across_calls() -> None:
    assert slugify("!!!") != slugify("!!!")


def test_long_name_is_truncated_without_trailing_hyphen() -> None:
    slug = slugify("a" * 300 + " " + "b" * 300)

    assert len(slug) <= MAX_SLUG_LENGTH
    assert not slug.endswith("-")
    assert SLUG_PATTERN.match(slug)
