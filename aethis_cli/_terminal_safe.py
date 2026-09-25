"""Make server-supplied text safe to print to a terminal.

Printing text through ``rich.text.Text`` stops Rich *markup* from being
interpreted, but it passes terminal control characters straight through. Text
derived from uploaded sources or model output can therefore clear the screen,
plant a hidden hyperlink (OSC 8), rewrite the window title, or reorder what
follows with a bidirectional override.

``safe_text`` replaces every such character with a visible escape, so the
reader sees that it was there without the terminal acting on it. It fails
closed: any value is coerced with ``str()`` and then sanitised; nothing is
ever returned unexamined.

What it does not do: judge whether the printable text itself is misleading.
"""

from __future__ import annotations

from typing import Any

# Bidirectional embeddings, overrides, isolates and marks: they reorder how the
# surrounding text is displayed.
_BIDI = frozenset([0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A)])


def _escape(ch: str) -> str:
    code = ord(ch)
    return f"\\x{code:02x}" if code <= 0xFF else f"\\u{code:04x}"


def safe_text(value: Any) -> str:
    """Return ``value`` as one printable line with control characters escaped.

    Newlines, carriage returns and tabs become a space, so a field cannot start
    a line of its own. C0 controls, DEL, C1 controls (U+0080-U+009F) and
    bidirectional controls become a visible ``\\xNN`` / ``\\uNNNN`` escape.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in "\n\r\t":
            out.append(" ")
        elif code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F or code in _BIDI:
            out.append(_escape(ch))
        else:
            out.append(ch)
    return "".join(out)
