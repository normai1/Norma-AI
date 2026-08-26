import re
import secrets

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

MAX_SLUG_LENGTH = 255


def random_suffix() -> str:
    """
    Short random token used to break slug collisions.
    """

    return secrets.token_hex(4)


def slugify(name: str) -> str:
    """
    Turn an organization name into a URL-safe slug.

    Names are user-supplied and may be non-Latin, punctuation-only, or empty
    once sanitized, so a random fallback keeps the result usable rather than
    returning an empty string that could never be a valid path segment.
    """

    slug = _NON_ALNUM.sub("-", name.strip().lower()).strip("-")

    if not slug:
        return f"org-{random_suffix()}"

    return slug[:MAX_SLUG_LENGTH].rstrip("-")
