"""Generation telemetry formatting remains backward-compatible and truthful."""

from aethis_cli.generation_status import format_heartbeat, format_poll_description, format_progress_detail


def test_formats_turn_convergence_and_tool_count():
    job = {"current_turn": 11, "max_turns": 20, "best_passed": 31, "test_total": 42, "tool_calls": 27}
    assert format_progress_detail(job) == "turn 11/20 · best 31/42 tests · 27 tool calls"


def test_formats_server_computed_heartbeat_age_and_last_tool():
    job = {"seconds_since_progress": 3725.8, "last_tool": "compile_and_test"}
    assert format_heartbeat(job) == "last progress 1h 2m ago · compile_and_test"


def test_old_engine_payload_has_no_invented_heartbeat_or_convergence():
    job = {"status": "running", "progress_percent": 20}
    assert format_progress_detail(job) == ""
    assert format_heartbeat(job) == ""
    assert format_poll_description(job) == "running — 20%"


def test_bad_numeric_values_are_not_rendered_as_heartbeat_age():
    assert format_heartbeat({"seconds_since_progress": -1}) == ""
    assert format_heartbeat({"seconds_since_progress": True}) == ""
