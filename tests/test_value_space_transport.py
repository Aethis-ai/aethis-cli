"""Named value-space transport in the CLI (aethis-core#424, design note §10/§13-P3).

Covers the four CLI obligations of the transport phase:

1. `fields.yaml` may declare `value_space:` (XOR `enum_values` per field), and
   `_upload_field_vocabulary` transmits it on the pin.
2. Registry sync runs BEFORE spec-set, PUTs each locally-authored space with
   the sync-state base version, and any non-2xx ABORTS before `set_field_spec`
   naming the missing engine capability (DX-6 — an old engine is extra-ignore
   on ExpectedFieldSpec and would otherwise silently drop the pin).
3. The post-generation drift report is registry-aware for referenced fields:
   expected members resolve from the engine registry at the version the job
   result stamped — never the silent "pins no enum_values ⇒ opts out" path —
   and a version advance during generation is flagged, never invisible (C3,
   C5(d)).
4. `aethis fields pull` preserves `value_space:` and never writes members back
   for a referenced field (un-migration guard, C3).
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
import typer
import yaml

from aethis_cli.client import AethisAPIError
from aethis_cli.commands import generate_cmd
from aethis_cli.config import read_state, write_state

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _flat(capsys) -> str:
    return re.sub(r"\s+", " ", _ANSI.sub("", capsys.readouterr().out))


REFERENCED_FIELDS = """\
fields:
  - key: pi.nationality
    type: enum
    value_space: form-an/countries
    question: "What is your nationality?"
  - key: applicant.income
    type: int
"""

SPACE_FILE = """\
name: form-an/countries
provenance:
  source:
    package: pycountry
    version: 26.2.16
    standard: ISO 3166-1 alpha-3
derived_from:
  file: shared/countries.yaml
  sha256: abc123
members:
  - united_kingdom
  - france
  - germany
"""


def _write(path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _project_with_reference(tmp_path, space_file: bool = True):
    _write(tmp_path / "fields" / "fields.yaml", REFERENCED_FIELDS)
    if space_file:
        _write(tmp_path / "shared" / "value_spaces" / "form-an__countries.yaml", SPACE_FILE)
    return tmp_path


# --- validation: value_space XOR enum_values --------------------------------


def test_validate_rejects_both_value_space_and_enum_values():
    errors = generate_cmd.validate_fields_list(
        [{"key": "pi.nationality", "type": "enum", "value_space": "form-an/countries", "enum_values": ["x"]}]
    )
    assert any("mutually exclusive" in e for e in errors)


def test_validate_accepts_reference_form_enum():
    """P5's flagged defect: today the CLI actively rejects an enum field that
    carries value_space instead of enum_values. Reference-form must be valid."""
    errors = generate_cmd.validate_fields_list(
        [{"key": "pi.nationality", "type": "enum", "value_space": "form-an/countries"}]
    )
    assert errors == []


def test_validate_still_rejects_bare_enum():
    errors = generate_cmd.validate_fields_list([{"key": "pi.nationality", "type": "enum"}])
    assert any("enum_values" in e for e in errors)


def test_validate_rejects_value_space_on_non_enum():
    errors = generate_cmd.validate_fields_list(
        [{"key": "applicant.income", "type": "int", "value_space": "form-an/countries"}]
    )
    assert any("value_space" in e for e in errors)


# --- transmission + registry sync ordering ----------------------------------


def test_upload_transmits_value_space_on_the_pin(tmp_path):
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.put_value_space.return_value = {"name": "form-an/countries", "version": 1, "created": True}

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    _, expected_fields = client.set_field_spec.call_args.args
    by_key = {f["key"]: f for f in expected_fields}
    assert by_key["pi.nationality"]["value_space"] == "form-an/countries"
    assert "enum_values" not in by_key["pi.nationality"]
    assert "value_space" not in by_key["applicant.income"]


def test_registry_sync_puts_before_spec_set(tmp_path):
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.put_value_space.return_value = {"name": "form-an/countries", "version": 1, "created": True}

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.put_value_space.assert_called_once()
    kwargs = client.put_value_space.call_args.kwargs
    assert kwargs["name"] == "form-an/countries"
    assert kwargs["members"] == ["united_kingdom", "france", "germany"]
    assert kwargs["provenance"] == {
        "source": {"package": "pycountry", "version": "26.2.16", "standard": "ISO 3166-1 alpha-3"}
    }
    # The upsert happens BEFORE the pin is set — a spec-set against an
    # unsynced registry would 422 on a space the author has on disk.
    calls = [c[0] for c in client.method_calls]
    assert calls.index("put_value_space") < calls.index("set_field_spec")


def test_registry_sync_failure_aborts_before_spec_set(tmp_path, capsys):
    """DX-6: any non-2xx on the value-spaces routes is a HARD STOP before
    set_field_spec — a pre-#424 engine ignores unknown ExpectedFieldSpec keys,
    so proceeding would silently drop the pin and let the model free-author
    the vocabulary."""
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.put_value_space.side_effect = AethisAPIError(404, "Not Found")

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_not_called()
    out = _flat(capsys)
    assert "value-spaces" in out  # names the missing capability
    assert "value_space" in out or "pin" in out


def test_registry_sync_conflict_aborts_with_engine_detail(tmp_path, capsys):
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.put_value_space.side_effect = AethisAPIError(
        409, "Space 'form-an/countries' moved under you (current version 3, you based on 1) — pull first"
    )

    with pytest.raises(typer.Exit):
        generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.set_field_spec.assert_not_called()
    assert "moved under you" in _flat(capsys)


def test_registry_sync_uses_and_records_base_version(tmp_path):
    project = _project_with_reference(tmp_path)
    write_state(project, {"value_space_sync": {"form-an/countries": 3}})
    client = MagicMock()
    client.put_value_space.return_value = {"name": "form-an/countries", "version": 4, "created": True}

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    assert client.put_value_space.call_args.kwargs["base_version"] == 3
    assert read_state(project)["value_space_sync"]["form-an/countries"] == 4


def test_reference_without_local_file_warns_but_proceeds(tmp_path, capsys):
    """A reference with no local registry file may already be registered
    server-side (another project authored it). The sync says so out loud and
    lets spec-set's 422 be the loud gate for a genuinely unknown space."""
    project = _project_with_reference(tmp_path, space_file=False)
    client = MagicMock()

    generate_cmd._upload_field_vocabulary(client, "proj_1", project)

    client.put_value_space.assert_not_called()
    client.set_field_spec.assert_called_once()
    assert "form-an/countries" in _flat(capsys)


# --- registry-aware drift report (C3 / C5(d)) --------------------------------


def _schema(fields):
    return {"fields": fields}


def test_drift_report_verifies_referenced_field_against_registry(tmp_path, capsys):
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.get_schema.return_value = _schema(
        [
            {"field_id": "pi.nationality", "enum_values": ["united_kingdom", "france", "germany"]},
            {"field_id": "applicant.income"},
        ]
    )
    client.get_value_space.return_value = {
        "name": "form-an/countries",
        "version": 5,
        "members": ["united_kingdom", "france", "germany"],
    }

    generate_cmd._report_field_diff(
        client,
        "rs_1",
        project,
        value_spaces_resolved={"pi.nationality": {"space": "form-an/countries", "version": 5, "space_id": "vs_x"}},
    )

    out = _flat(capsys)
    # The verified line names the space, the resolved version, and the count.
    assert "pi.nationality ← form-an/countries@v5 (3 members verified)" in out
    # Resolution happened at the job-stamped version, not the head.
    assert client.get_value_space.call_args.kwargs.get("version") == 5


def test_drift_report_flags_mismatch_on_referenced_field(tmp_path, capsys):
    """The blind spot this exists to close: a referenced field pins no
    enum_values, so the old diff opted out and printed success with ZERO
    member verification on exactly the migrated fields. Mutating the
    registry-aware branch back to the opt-out turns this red."""
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.get_schema.return_value = _schema(
        [{"field_id": "pi.nationality", "enum_values": ["united_kingdom", "narnia"]}, {"field_id": "applicant.income"}]
    )
    client.get_value_space.return_value = {
        "name": "form-an/countries",
        "version": 5,
        "members": ["united_kingdom", "france", "germany"],
    }

    generate_cmd._report_field_diff(
        client,
        "rs_1",
        project,
        value_spaces_resolved={"pi.nationality": {"space": "form-an/countries", "version": 5, "space_id": "vs_x"}},
    )

    out = _flat(capsys)
    assert "narnia" in out  # produced-but-not-in-space is named
    assert "france" in out or "dropped" in out
    assert "all 2 pinned field(s) were produced" not in out


def test_drift_report_never_silently_skips_unverifiable_reference(tmp_path, capsys):
    """No stamped resolution (job predates the engine capability, or the run
    failed pre-resolution): the report says the field was NOT verified —
    silence here is the exact defect class this phase exists to kill."""
    project = _project_with_reference(tmp_path)
    client = MagicMock()
    client.get_schema.return_value = _schema(
        [{"field_id": "pi.nationality", "enum_values": ["united_kingdom"]}, {"field_id": "applicant.income"}]
    )

    generate_cmd._report_field_diff(client, "rs_1", project, value_spaces_resolved=None)

    out = _flat(capsys)
    assert "not verified" in out.lower() or "could not" in out.lower()
    assert "all 2 pinned field(s) were produced" not in out


def test_drift_report_flags_version_advance(tmp_path, capsys):
    """C5(d): the space advanced between sync and generation — legal (the
    snapshot resolves the new head), but never invisible."""
    project = _project_with_reference(tmp_path)
    write_state(project, {"value_space_sync": {"form-an/countries": 4}})
    client = MagicMock()
    client.get_schema.return_value = _schema(
        [
            {"field_id": "pi.nationality", "enum_values": ["united_kingdom", "france", "germany"]},
            {"field_id": "applicant.income"},
        ]
    )
    client.get_value_space.return_value = {
        "name": "form-an/countries",
        "version": 5,
        "members": ["united_kingdom", "france", "germany"],
    }

    generate_cmd._report_field_diff(
        client,
        "rs_1",
        project,
        value_spaces_resolved={"pi.nationality": {"space": "form-an/countries", "version": 5, "space_id": "vs_x"}},
    )

    out = _flat(capsys)
    assert "advanced" in out
    assert "v5" in out and "v4" in out


# --- GenerationOutcome carries the job's resolution --------------------------


def test_poll_threads_value_spaces_resolved_into_outcome(monkeypatch, tmp_path):
    resolved = {"pi.nationality": {"space": "form-an/countries", "version": 5, "space_id": "vs_x"}}
    client = MagicMock()
    client.get_status.return_value = {
        "job": {
            "job_id": "job_1",
            "status": "success",
            "progress_percent": 100,
            "result_ruleset_id": "rs_1",
            "value_spaces_resolved": resolved,
        },
        "latest_ruleset_id": "rs_1",
    }

    outcome = generate_cmd._poll_until_done(client, "proj_1", tmp_path, timeout=10, no_publish=True)

    assert outcome.status == "success"
    assert outcome.ruleset_id == "rs_1"
    assert outcome.value_spaces_resolved == resolved


# --- fields pull preserves the migration --------------------------------------


def test_fields_pull_preserves_value_space_and_never_writes_members(tmp_path, monkeypatch):
    from aethis_cli.commands import fields_cmd

    _write(tmp_path / "fields" / "fields.yaml", REFERENCED_FIELDS)
    _write(tmp_path / "aethis.yaml", "project: {name: t, section_id: s, domain: d}\n")
    write_state(tmp_path, {"ruleset_id": "rs_1"})

    client = MagicMock()
    client.get_schema.return_value = _schema(
        [
            {
                "field_id": "pi.nationality",
                "field_type": "enum",
                "enum_values": ["united_kingdom", "france", "germany"],
                "question": "What is your nationality?",
            },
            {"field_id": "applicant.income", "field_type": "int"},
        ]
    )

    cfg = MagicMock()
    cfg.config_path = tmp_path
    cfg.base_url = "http://test"
    monkeypatch.setattr(fields_cmd, "load_project_config", lambda: cfg)
    monkeypatch.setattr(fields_cmd, "resolve_api_key", lambda _cfg: "key")
    monkeypatch.setattr(fields_cmd, "make_authed_client", lambda *a, **k: client)

    fields_cmd.pull(ruleset_id=None)

    written = yaml.safe_load((tmp_path / "fields" / "fields.yaml").read_text())
    by_key = {f["key"]: f for f in written["fields"]}
    nat = by_key["pi.nationality"]
    assert nat["value_space"] == "form-an/countries"
    assert "enum_values" not in nat  # never materialised for a referenced field
    assert by_key["applicant.income"]["type"] == "int"


def test_poll_threads_resolution_on_the_publish_path_too(tmp_path):
    """The auto-publish success return is a SECOND exit — the mutation battery
    caught it dropping the resolution while the no-publish path carried it."""
    resolved = {"pi.nationality": {"space": "form-an/countries", "version": 5, "space_id": "vs_x"}}
    client = MagicMock()
    client.get_status.return_value = {
        "job": {
            "job_id": "job_1",
            "status": "success",
            "progress_percent": 100,
            "result_ruleset_id": "rs_1",
            "value_spaces_resolved": resolved,
        },
        "latest_ruleset_id": "rs_1",
    }

    outcome = generate_cmd._poll_until_done(client, "proj_1", tmp_path, timeout=10, no_publish=False)

    client.publish.assert_called_once()  # publish behaviour itself untouched
    assert outcome.value_spaces_resolved == resolved
