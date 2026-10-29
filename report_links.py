"""Validation for links the model puts in a PlaceReport.

The model chooses the link, and it reads untrusted text (web pages, place data)
along the way, so a prompt injection can plant a `javascript:` or `data:` URL.
The browser escapes the link before rendering it, but escaping does not change
the URL scheme. Only absolute http and https URLs survive; anything else becomes
None and the UI shows "No relevant link found".
"""

from typing import Optional
from urllib.parse import urlparse

ALLOWED_LINK_SCHEMES = ("http", "https")
MAX_LINK_CHARS = 2048


def safe_link(value: Optional[str]) -> Optional[str]:
    """Return the link if it is an absolute http(s) URL, otherwise None."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_LINK_CHARS:
        return None
    # Browsers strip ASCII tab and newline from URLs before parsing them, so
    # "java\nscript:" would run. Refuse any control character outright.
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in candidate):
        return None
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_LINK_SCHEMES or not parsed.hostname:
        return None
    return candidate
