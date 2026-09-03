"""Pure formatting helpers for generation progress and heartbeat telemetry."""

from __future__ import annotations

from typing import Any


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def format_progress_detail(job: dict[str, Any]) -> str:
    """Compact, truthful convergence detail for a status payload.

    Every member is optional because this CLI remains compatible with engines
    from before the live-telemetry fields were added.
    """
    parts: list[str] = []
    turn = job.get("current_turn")
    max_turns = job.get("max_turns")
    if isinstance(turn, int) and not isinstance(turn, bool):
        parts.append(f"turn {turn}/{max_turns}" if isinstance(max_turns, int) else f"turn {turn}")

    best = job.get("best_passed")
    total = job.get("test_total")
    if isinstance(best, int) and not isinstance(best, bool):
        parts.append(f"best {best}/{total} tests" if isinstance(total, int) else f"best {best} tests")

    calls = job.get("tool_calls")
    if isinstance(calls, int) and not isinstance(calls, bool):
        noun = "call" if calls == 1 else "calls"
        parts.append(f"{calls} tool {noun}")
    return " · ".join(parts)


def format_heartbeat(job: dict[str, Any]) -> str:
    """Describe the server-computed heartbeat age without inventing a stall."""
    seconds = _non_negative_number(job.get("seconds_since_progress"))
    if seconds is not None:
        rounded = int(seconds)
        if rounded < 60:
            age = f"{rounded}s ago"
        elif rounded < 3600:
            age = f"{rounded // 60}m {rounded % 60}s ago"
        else:
            age = f"{rounded // 3600}h {(rounded % 3600) // 60}m ago"
        tool = job.get("last_tool")
        return f"last progress {age}" + (f" · {tool}" if isinstance(tool, str) and tool else "")

    timestamp = job.get("last_progress_at")
    if isinstance(timestamp, str) and timestamp:
        return f"last progress {timestamp}"
    return ""


def format_poll_description(job: dict[str, Any]) -> str:
    """One progress-bar line carrying status, convergence, and heartbeat."""
    status = str(job.get("status", "unknown"))
    pct = job.get("progress_percent", 0)
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        pct = 0
    parts = [f"{status} — {pct:g}%"]
    progress = format_progress_detail(job)
    heartbeat = format_heartbeat(job)
    if progress:
        parts.append(progress)
    if heartbeat:
        parts.append(heartbeat)
    return " — ".join(parts)
