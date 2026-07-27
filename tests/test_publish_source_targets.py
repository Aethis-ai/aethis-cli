"""`aethis publish --source-targets` — the P2 acceptance criteria as tests.

Spec: `aethis-workspace/docs/specs/2026-07-26-provenance-retained-bytes.md`,
phase P2 (epic Aethis-ai/aethis-workspace#704, sub-issue aethis-cli#96).

EARS lines under test:

1. WHEN a targets file names a local file THE SYSTEM SHALL upload it (or reuse
   an already-uploaded source with identical bytes) and publish citing its
   `artefact_source_id`.
2. IF an entry has both/neither of `url`/`file`, or the file is unreadable,
   THEN THE SYSTEM SHALL fail locally with actionable guidance before any API
   call.
3. WHEN rendering an artefact reference THE SYSTEM SHALL label it as an
   authenticated uploaded snapshot verified at publish time.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from typer.testing import CliRunner

from aethis_cli import contract, decision_view
from aethis_cli.commands import publish_cmd
from aethis_cli.main import app
from aethis_cli.source_targets import (
    SourceTargetsError,
    load_source_targets,
    resolve_source_targets,
)

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture()
def rendered(capsys, monkeypatch):
    """Captured console text, ANSI-stripped and wide enough not to wrap.

    Rich wraps at 80 columns under capture, which silently breaks substring
    assertions on the very labels this phase exists to add.
    """
    monkeypatch.setenv("COLUMNS", "220")

    def _read() -> str:
        return _ANSI_RE.sub("", capsys.readouterr().out)

    return _read


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _url_entry(**overrides):
    entry = {
        "url": "https://www.legislation.gov.uk/ukpga/1981/61/schedule/1",
        "title": "British Nationality Act 1981, Schedule 1",
        "authority": "UK Government",
        "licence": "OGL-UK-3.0",
        "quote": {"exact": "is of full age and capacity"},
    }
    entry.update(overrides)
    return entry


def _file_entry(path: Path, **overrides):
    entry = {
        "file": str(path),
        "title": "Naturalisation booklet AN",
        "authority": "Home Office",
        "licence": "OGL-UK-3.0",
        "quote": {"exact": "You must have been resident in the UK"},
    }
    entry.update(overrides)
    return entry


def _write_targets(tmp_path: Path, data: dict, name: str = "targets.yaml") -> Path:
    path = tmp_path / name
    if name.endswith(".json"):
        path.write_text(json.dumps(data))
    else:
        path.write_text(yaml.safe_dump(data, sort_keys=False))
    return path


def _corpus(tmp_path: Path, name: str = "guidance.txt", body: str = "guidance bytes") -> Path:
    path = tmp_path / name
    path.write_text(body)
    return path


def _fake_client(existing_sources=None, upload_source_id="src_new1"):
    client = MagicMock()
    client.list_sources.return_value = {"sources": existing_sources or []}
    client.upload_sources.return_value = {
        "uploaded": 1,
        "sources": [{"source_id": upload_source_id, "filename": "guidance.txt"}],
    }
    return client


# ---------------------------------------------------------------------------
# EARS 2 — local validation before any API call
# ---------------------------------------------------------------------------


def test_entry_with_both_url_and_file_fails_locally(tmp_path):
    corpus = _corpus(tmp_path)
    targets = _write_targets(tmp_path, {"K1": _url_entry(file=str(corpus))})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "exactly one of 'url'" in str(exc.value)
    assert "both" in str(exc.value)


def test_entry_with_neither_url_nor_file_fails_locally(tmp_path):
    entry = _url_entry()
    entry.pop("url")
    targets = _write_targets(tmp_path, {"K1": entry})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "exactly one of 'url'" in str(exc.value)
    assert "neither" in str(exc.value)


def test_missing_file_fails_locally_naming_the_path(tmp_path):
    targets = _write_targets(tmp_path, {"K1": _file_entry(tmp_path / "nope.txt")})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "file not found" in str(exc.value)
    assert "nope.txt" in str(exc.value)


def test_directory_as_file_fails_locally(tmp_path):
    subdir = tmp_path / "corpus"
    subdir.mkdir()
    targets = _write_targets(tmp_path, {"K1": _file_entry(subdir)})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "directory, not a file" in str(exc.value)


def test_unreadable_file_fails_locally(tmp_path):
    corpus = _corpus(tmp_path)
    corpus.chmod(0o000)
    try:
        targets = _write_targets(tmp_path, {"K1": _file_entry(corpus)})
        with pytest.raises(SourceTargetsError) as exc:
            load_source_targets(targets)
        assert "not readable" in str(exc.value)
    finally:
        corpus.chmod(0o644)


@pytest.mark.parametrize("missing", ["title", "authority", "licence"])
def test_missing_required_field_fails_locally(tmp_path, missing):
    entry = _url_entry()
    entry.pop(missing)
    targets = _write_targets(tmp_path, {"K1": entry})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert f"'{missing}' is required" in str(exc.value)


def test_missing_quote_exact_fails_locally(tmp_path):
    targets = _write_targets(tmp_path, {"K1": _url_entry(quote={"prefix": "before"})})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "quote.exact is required" in str(exc.value)


def test_non_https_url_fails_locally(tmp_path):
    targets = _write_targets(tmp_path, {"K1": _url_entry(url="http://example.gov.uk/doc")})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "https://" in str(exc.value)


def test_unknown_field_fails_rather_than_being_silently_dropped(tmp_path):
    targets = _write_targets(tmp_path, {"K1": _url_entry(loctaor="para 1")})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    assert "unknown field(s) loctaor" in str(exc.value)


def test_every_problem_is_reported_not_just_the_first(tmp_path):
    entry = _url_entry()
    entry.pop("licence")
    entry.pop("quote")
    targets = _write_targets(tmp_path, {"K1": entry, "K2": {"title": "t"}})
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(targets)
    problems = exc.value.problems
    assert len(problems) >= 4
    assert any("K1" in p and "licence" in p for p in problems)
    assert any("K2" in p for p in problems)


def test_empty_and_malformed_files_fail_locally(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("")
    with pytest.raises(SourceTargetsError):
        load_source_targets(empty)

    listy = tmp_path / "list.yaml"
    listy.write_text("- a\n- b\n")
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(listy)
    assert "top level must be a mapping" in str(exc.value)

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json")
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(bad_json)
    assert "invalid JSON" in str(exc.value)


def test_json_targets_file_is_accepted(tmp_path):
    targets = _write_targets(tmp_path, {"K1": _url_entry()}, name="targets.json")
    loaded = load_source_targets(targets)
    assert [t.key for t in loaded] == ["K1"]
    assert loaded[0].is_artefact is False


def test_file_path_resolves_relative_to_the_targets_file(tmp_path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    (corpus_dir / "guidance.txt").write_text("bytes")
    targets = _write_targets(tmp_path, {"K1": _file_entry(Path("corpus/guidance.txt"))})
    loaded = load_source_targets(targets)
    assert loaded[0].file == (corpus_dir / "guidance.txt").resolve()


def test_publish_with_invalid_targets_makes_no_api_call(tmp_path, monkeypatch):
    """EARS 2 — the failure is local: no client is ever constructed."""
    targets = _write_targets(tmp_path, {"K1": _url_entry(url="ftp://example.com/x")})

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("an API client was constructed before local validation")

    monkeypatch.setattr(publish_cmd, "load_project_config", _boom)
    monkeypatch.setattr(publish_cmd, "make_authed_client", _boom)

    result = runner.invoke(app, ["publish", "--source-targets", str(targets)])
    assert result.exit_code == 1
    assert "https://" in result.output
    assert "exactly one of 'url' or 'file'" in result.output or "must be an absolute https" in result.output


# ---------------------------------------------------------------------------
# EARS 1 — file targets upload (or reuse) and are cited by artefact_source_id
# ---------------------------------------------------------------------------


def test_file_target_uploads_and_cites_the_returned_source_id(tmp_path):
    corpus = _corpus(tmp_path)
    targets = load_source_targets(_write_targets(tmp_path, {"HO#4": _file_entry(corpus)}))
    client = _fake_client(upload_source_id="src_abc123")

    wire, resolutions = resolve_source_targets(client, "proj_1", targets)

    client.upload_sources.assert_called_once()
    assert client.upload_sources.call_args[0][1] == [corpus]
    assert wire["HO#4"]["artefact_source_id"] == "src_abc123"
    assert "url" not in wire["HO#4"]
    assert wire["HO#4"]["title"] == "Naturalisation booklet AN"
    assert wire["HO#4"]["quote"] == {"exact": "You must have been resident in the UK"}
    assert resolutions[0].kind == "artefact"
    assert resolutions[0].reused is False


def test_identical_bytes_already_uploaded_are_reused_not_re_uploaded(tmp_path):
    """EARS 1 (reuse half). The engine never dedupes on upload, so the CLI's
    sha256 comparison is the only thing preventing a duplicate source."""
    corpus = _corpus(tmp_path, body="the exact same bytes")
    digest = hashlib.sha256(corpus.read_bytes()).hexdigest()
    targets = load_source_targets(_write_targets(tmp_path, {"HO#4": _file_entry(corpus)}))
    client = _fake_client(existing_sources=[{"source_id": "src_existing", "raw_sha256": digest}])

    wire, resolutions = resolve_source_targets(client, "proj_1", targets)

    client.upload_sources.assert_not_called()
    assert wire["HO#4"]["artefact_source_id"] == "src_existing"
    assert resolutions[0].reused is True


def test_different_bytes_are_uploaded_even_when_sources_exist(tmp_path):
    corpus = _corpus(tmp_path, body="new bytes")
    targets = load_source_targets(_write_targets(tmp_path, {"HO#4": _file_entry(corpus)}))
    client = _fake_client(existing_sources=[{"source_id": "src_other", "raw_sha256": "0" * 64}])

    wire, _ = resolve_source_targets(client, "proj_1", targets)

    client.upload_sources.assert_called_once()
    assert wire["HO#4"]["artefact_source_id"] == "src_new1"


def test_two_entries_naming_identical_bytes_upload_once(tmp_path):
    a = _corpus(tmp_path, name="a.txt", body="same")
    b = _corpus(tmp_path, name="b.txt", body="same")
    targets = load_source_targets(_write_targets(tmp_path, {"K1": _file_entry(a), "K2": _file_entry(b)}))
    client = _fake_client()

    wire, _ = resolve_source_targets(client, "proj_1", targets)

    assert client.upload_sources.call_count == 1
    assert wire["K1"]["artefact_source_id"] == wire["K2"]["artefact_source_id"] == "src_new1"


def test_url_targets_pass_through_without_touching_the_sources_api(tmp_path):
    targets = load_source_targets(_write_targets(tmp_path, {"K1": _url_entry(locator="Paragraph 1(1)(a)")}))
    client = _fake_client()

    wire, resolutions = resolve_source_targets(client, "proj_1", targets)

    client.list_sources.assert_not_called()
    client.upload_sources.assert_not_called()
    assert wire["K1"]["url"].startswith("https://")
    assert "artefact_source_id" not in wire["K1"]
    assert wire["K1"]["locator"] == "Paragraph 1(1)(a)"
    assert resolutions[0].kind == "url"


def test_upload_without_a_source_id_fails_rather_than_publishing_a_broken_citation(tmp_path):
    corpus = _corpus(tmp_path)
    targets = load_source_targets(_write_targets(tmp_path, {"K1": _file_entry(corpus)}))
    client = _fake_client()
    client.upload_sources.return_value = {"uploaded": 0, "sources": []}

    with pytest.raises(SourceTargetsError) as exc:
        resolve_source_targets(client, "proj_1", targets)
    assert "no source_id" in str(exc.value)


def test_publish_sends_source_targets_in_the_request_body(tmp_path):
    """End-to-end through the client: the body carries the resolved map."""
    import respx
    from httpx import Response

    from aethis_cli.client import AethisClient

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.aethis.ai/api/v1/public/projects/proj_1/publish").mock(
            return_value=Response(200, json={"ruleset_id": "rs_1"})
        )
        AethisClient("ak", "https://api.aethis.ai").publish(
            "proj_1",
            source_targets={"K1": {"artefact_source_id": "src_1", "title": "t"}},
        )
    body = json.loads(route.calls[0].request.content)
    assert body["source_targets"]["K1"]["artefact_source_id"] == "src_1"


def test_publish_without_source_targets_sends_no_such_field(tmp_path):
    import respx
    from httpx import Response

    from aethis_cli.client import AethisClient

    with respx.mock(assert_all_called=True) as mock:
        route = mock.post("https://api.aethis.ai/api/v1/public/projects/proj_1/publish").mock(
            return_value=Response(200, json={"ruleset_id": "rs_1"})
        )
        AethisClient("ak", "https://api.aethis.ai").publish("proj_1", slug="a/b")
    body = json.loads(route.calls[0].request.content)
    assert "source_targets" not in body


def test_list_sources_hits_the_sources_endpoint():
    import respx
    from httpx import Response

    from aethis_cli.client import AethisClient

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.aethis.ai/api/v1/public/projects/proj_1/sources").mock(
            return_value=Response(200, json={"sources": [{"source_id": "s1", "raw_sha256": "ab"}]})
        )
        out = AethisClient("ak", "https://api.aethis.ai").list_sources("proj_1")
    assert out["sources"][0]["raw_sha256"] == "ab"


# ---------------------------------------------------------------------------
# EARS 3 — artefact references render as authenticated, verified-at-publish
# ---------------------------------------------------------------------------


V2_REFERENCE = {
    "schema_version": 2,
    "target_kind": "artefact",
    "artefact_project_id": "proj_1",
    "artefact_source_id": "src_abc123",
    "source_id": "HO#4",
    "title": "Naturalisation booklet AN",
    "authority": "Home Office",
    "url": "/api/v1/public/projects/proj_1/sources/src_abc123/raw",
    "content_digest": "sha256:" + "a" * 64,
    "licence": "OGL-UK-3.0",
    "verified_at": "2026-07-27T10:00:00Z",
    "quote": {"exact": "You must have been resident in the UK"},
    "media_type": "pdf",
    "deep_link": "/api/v1/public/projects/proj_1/sources/src_abc123/raw#page=4",
}

V1_REFERENCE = {
    "schema_version": 1,
    "source_id": "BNA1981#Sch1",
    "title": "British Nationality Act 1981",
    "authority": "UK Government",
    "url": "https://www.legislation.gov.uk/ukpga/1981/61/schedule/1",
    "content_digest": "sha256:" + "b" * 64,
    "licence": "OGL-UK-3.0",
    "verified_at": "2026-07-27T10:00:00Z",
    "quote": {"exact": "is of full age and capacity"},
    "media_type": "html",
    "deep_link": "https://www.legislation.gov.uk/ukpga/1981/61/schedule/1#:~:text=is%20of%20full%20age",
    "snapshot": "sha256:" + "b" * 64,
}


def test_target_kind_classifies_v1_and_v2():
    assert contract.reference_target_kind(V2_REFERENCE) == "artefact"
    assert contract.reference_target_kind(V1_REFERENCE) == "url"
    # A v2 reference that only carries target_kind is still an artefact.
    assert contract.reference_target_kind({"target_kind": "artefact"}) == "artefact"
    assert contract.reference_target_kind(None) == "url"


def test_artefact_reference_is_labelled_authenticated_and_verified_at_publish(rendered):
    cited = contract.CitedCriterion("c1", "Residence", None, (V2_REFERENCE,))
    decision_view.print_sources([cited], base_url="https://staging.api.aethis.ai")
    out = rendered()
    assert "uploaded artefact" in out
    assert "uploaded snapshot, verified at publish" in out
    assert "authenticated download" in out
    assert "API key" in out


def test_artefact_download_link_is_absolute_against_the_engine(rendered):
    cited = contract.CitedCriterion("c1", "Residence", None, (V2_REFERENCE,))
    decision_view.print_sources([cited], base_url="https://staging.api.aethis.ai")
    out = rendered()
    assert "https://staging.api.aethis.ai/api/v1/public/projects/proj_1/sources/src_abc123/raw" in out


def test_v1_reference_is_not_labelled_as_an_artefact(rendered):
    cited = contract.CitedCriterion("c1", "Age", None, (V1_REFERENCE,))
    decision_view.print_sources([cited], base_url="https://api.aethis.ai")
    out = rendered()
    assert "uploaded artefact" not in out
    assert "authenticated download" not in out
    assert "snapshot of the fetched page" in out
    assert "https://www.legislation.gov.uk" in out


def test_relative_artefact_link_is_not_dressed_up_as_a_url_without_a_base(rendered):
    """Without an engine base URL the relative /raw path is shown as-is —
    inventing a host would point the reader at the wrong engine."""
    cited = contract.CitedCriterion("c1", "Residence", None, (V2_REFERENCE,))
    decision_view.print_sources([cited])
    out = rendered()
    download_line = next(line for line in out.splitlines() if "Download:" in line)
    assert "/api/v1/public/projects/proj_1/sources/src_abc123/raw" in download_line
    assert "http" not in download_line


def test_reference_link_joins_only_relative_paths():
    assert contract.reference_link(V1_REFERENCE, "https://api.aethis.ai").startswith("https://www.legislation")
    assert contract.reference_link(V2_REFERENCE, "https://api.aethis.ai/").startswith(
        "https://api.aethis.ai/api/v1/public/"
    )
    assert contract.reference_link({}, "https://api.aethis.ai") is None


def test_resolution_summary_distinguishes_the_two_kinds(rendered):
    from aethis_cli.source_targets import ResolvedTarget

    publish_cmd._print_resolutions(
        [
            ResolvedTarget("K1", "url", "https://example.gov.uk/doc"),
            ResolvedTarget("K2", "artefact", "src_abc123", reused=True),
        ]
    )
    out = rendered()
    assert "url" in out and "artefact" in out
    assert "reused existing upload" in out
    assert "authenticated download, never anonymously readable" in out
    assert "public link" in out


# ---------------------------------------------------------------------------
# Command-level wiring — the whole path, from targets file to publish body
# ---------------------------------------------------------------------------


def _project_dir(tmp_path):
    (tmp_path / "aethis.yaml").write_text("project: test-project\napi_key_env: AETHIS_API_KEY\n")
    state = tmp_path / ".aethis"
    state.mkdir()
    (state / "state.json").write_text('{"project_id": "proj_test"}')


def test_publish_command_uploads_file_targets_and_cites_them(tmp_path, monkeypatch, rendered):
    """EARS 1, end to end: `aethis publish --source-targets` uploads the file
    named by the entry and sends its source_id as the citation's artefact."""
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)
    corpus = _corpus(tmp_path)
    targets_file = _write_targets(tmp_path, {"HO#4": _file_entry(corpus), "BNA#1": _url_entry()}, name="t.yaml")

    client = _fake_client(upload_source_id="src_up1")
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish", "--source-targets", str(targets_file)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    sent = client.publish.call_args.kwargs["source_targets"]
    assert sent["HO#4"]["artefact_source_id"] == "src_up1"
    assert sent["BNA#1"]["url"].startswith("https://")
    out = _ANSI_RE.sub("", result.output)
    assert "Source targets (2)" in out
    assert "authenticated download, never anonymously readable" in out


def test_publish_command_without_targets_sends_none(tmp_path, monkeypatch):
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)

    client = _fake_client()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert client.publish.call_args.kwargs["source_targets"] is None
    client.list_sources.assert_not_called()


# ---------------------------------------------------------------------------
# Review fix 1 — per-key citation failures reach the user
# ---------------------------------------------------------------------------

#: The real 422 envelope the engine returns when publish-time citation
#: resolution fails: a per-key `failures` list of {source_id, reason_code,
#: message}. Shape read from aethis-core `routes/projects.py`
#: (source_reference_resolution_failed) and `services/source_resolution.py`
#: (`SourceResolutionError.failures`), not invented.
RESOLUTION_FAILED_DETAIL = {
    "error": "validation_error",
    "reason_code": "source_reference_resolution_failed",
    "message": (
        "Refusing to publish a public ruleset with unresolved source references. "
        "Every declared citation key must resolve to a validated HTTPS authority "
        "with title, licence and the verbatim quoted text it cites."
    ),
    "failures": [
        {
            "source_id": "BNA1981#Sch1",
            "reason_code": "quote_not_found",
            "message": (
                "The verbatim quote for 'BNA1981#Sch1' does not occur in the fetched "
                "source. Quotes must be exact text from the source, never a summary "
                "or paraphrase."
            ),
        },
        {
            "source_id": "HO#4",
            "reason_code": "artefact_unsupported_type",
            "message": (
                "Source 'src_abc123' has file_type 'unknown' — its bytes cannot be "
                "quote-checked, so it cannot back a citation."
            ),
        },
    ],
}


def test_per_key_citation_failures_are_rendered(rendered):
    """Regression for the swallowed-`failures` defect: the envelope's summary
    line alone tells an author with several citations neither which key failed
    nor why."""
    from aethis_cli.output import render_api_error

    render_api_error(422, RESOLUTION_FAILED_DETAIL)
    out = rendered()
    assert "2 citation(s) could not be resolved" in out
    for key in ("BNA1981#Sch1", "HO#4"):
        assert key in out
    for reason in ("quote_not_found", "artefact_unsupported_type"):
        assert reason in out
    assert "does not occur in the fetched source" in out
    assert "cannot be quote-checked" in out


def test_publish_command_surfaces_per_key_failures(tmp_path, monkeypatch, rendered):
    from unittest.mock import patch

    from aethis_cli.errors import AethisAPIError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)
    corpus = _corpus(tmp_path)
    targets_file = _write_targets(tmp_path, {"HO#4": _file_entry(corpus)}, name="t.yaml")

    client = _fake_client()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.side_effect = AethisAPIError(422, RESOLUTION_FAILED_DETAIL)

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish", "--source-targets", str(targets_file)], catch_exceptions=False)

    assert result.exit_code == 1
    out = _ANSI_RE.sub("", result.output)
    assert "citation(s) could not be resolved" in out
    assert "quote_not_found" in out
    # Review fix 3 — the uploads are not rolled back, and retrying is safe.
    assert "remain in the project" in out
    assert "does not duplicate" in out


def test_failure_list_is_absent_from_ordinary_errors(rendered):
    from aethis_cli.output import render_api_error

    render_api_error(403, {"message": "Denied", "reason_code": "forbidden"})
    out = rendered()
    assert "could not be resolved" not in out


def test_no_upload_note_when_only_url_targets_were_sent(tmp_path, monkeypatch):
    from unittest.mock import patch

    from aethis_cli.errors import AethisAPIError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)
    targets_file = _write_targets(tmp_path, {"BNA#1": _url_entry()}, name="t.yaml")

    client = _fake_client()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.side_effect = AethisAPIError(422, RESOLUTION_FAILED_DETAIL)

    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish", "--source-targets", str(targets_file)], catch_exceptions=False)
    assert "remain in the project" not in _ANSI_RE.sub("", result.output)


# ---------------------------------------------------------------------------
# Review fix 2 — targets that the ruleset never declared do not pass silently
# ---------------------------------------------------------------------------


def _explain_with(keys):
    return {
        "ruleset_id": "rs_x",
        "criteria": [
            {
                "criterion_id": "c1",
                "source_references": [{"source_id": k, "title": k} for k in keys],
            }
        ],
    }


def _publish_with_targets(tmp_path, monkeypatch, client, entries):
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)
    targets_file = _write_targets(tmp_path, entries, name="t.yaml")
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish", "--source-targets", str(targets_file)], catch_exceptions=False)
    return result


def test_publish_warns_loudly_when_no_citations_landed(tmp_path, monkeypatch):
    """The engine ignores a target whose key no criterion declares, so a
    typo'd key publishes 'successfully' with zero citations after uploading
    the files. That must not read as success."""
    client = _fake_client()
    client.explain.return_value = _explain_with([])
    corpus = _corpus(tmp_path)
    result = _publish_with_targets(tmp_path, monkeypatch, client, {"TYPO#1": _file_entry(corpus)})

    assert result.exit_code == 0
    out = _ANSI_RE.sub("", result.output)
    assert "0 of 1 supplied citation target(s) landed" in out
    assert "Not attached: TYPO#1" in out
    assert "source_refs" in out


def test_publish_reports_quietly_when_every_citation_landed(tmp_path, monkeypatch):
    client = _fake_client()
    client.explain.return_value = _explain_with(["BNA#1"])
    result = _publish_with_targets(tmp_path, monkeypatch, client, {"BNA#1": _url_entry()})

    out = _ANSI_RE.sub("", result.output)
    assert "1 citation(s) attached" in out
    assert "Not attached" not in out


def test_publish_names_only_the_targets_that_did_not_land(tmp_path, monkeypatch):
    client = _fake_client()
    client.explain.return_value = _explain_with(["BNA#1"])
    corpus = _corpus(tmp_path)
    result = _publish_with_targets(tmp_path, monkeypatch, client, {"BNA#1": _url_entry(), "HO#4": _file_entry(corpus)})

    out = _ANSI_RE.sub("", result.output)
    assert "1 of 2 supplied citation target(s) landed" in out
    assert "Not attached: HO#4" in out


def test_verification_failure_never_fails_a_successful_publish(tmp_path, monkeypatch):
    client = _fake_client()
    client.explain.side_effect = RuntimeError("engine unreachable")
    result = _publish_with_targets(tmp_path, monkeypatch, client, {"BNA#1": _url_entry()})

    assert result.exit_code == 0
    out = _ANSI_RE.sub("", result.output)
    assert "Published ruleset" in out
    assert "Could not read the ruleset back" in out


def test_no_verification_call_when_no_targets_were_supplied(tmp_path, monkeypatch):
    from unittest.mock import patch

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AETHIS_API_KEY", "ak_test")
    _project_dir(tmp_path)
    client = _fake_client()
    client.run_tests.return_value = {"passed": 1, "failed": 0, "errors": 0, "total": 1}
    client.publish.return_value = {"ruleset_id": "rs_x"}
    with patch("aethis_cli.client.AethisClient", return_value=client):
        result = runner.invoke(app, ["publish"], catch_exceptions=False)
    assert result.exit_code == 0
    client.explain.assert_not_called()


# ---------------------------------------------------------------------------
# Review fix 4 — a duplicate citation key never silently discards a document
# ---------------------------------------------------------------------------


def test_duplicate_key_in_yaml_is_rejected(tmp_path):
    path = tmp_path / "dupe.yaml"
    path.write_text(
        '"K1":\n'
        "  url: https://example.gov.uk/a\n"
        "  title: A\n"
        "  authority: A\n"
        "  licence: OGL-UK-3.0\n"
        "  quote: {exact: 'a'}\n"
        '"K1":\n'
        "  url: https://example.gov.uk/b\n"
        "  title: B\n"
        "  authority: B\n"
        "  licence: OGL-UK-3.0\n"
        "  quote: {exact: 'b'}\n"
    )
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(path)
    assert "duplicate citation key 'K1'" in str(exc.value)


def test_duplicate_key_in_json_is_rejected(tmp_path):
    path = tmp_path / "dupe.json"
    path.write_text(json.dumps({"K1": _url_entry()})[:-1] + ', "K1": ' + json.dumps(_url_entry()) + "}")
    with pytest.raises(SourceTargetsError) as exc:
        load_source_targets(path)
    assert "duplicate citation key 'K1'" in str(exc.value)


def test_repeated_field_inside_one_entry_is_also_rejected(tmp_path):
    path = tmp_path / "dupe-field.yaml"
    path.write_text(
        '"K1":\n'
        "  url: https://example.gov.uk/a\n"
        "  url: https://example.gov.uk/b\n"
        "  title: A\n"
        "  authority: A\n"
        "  licence: OGL-UK-3.0\n"
        "  quote: {exact: 'a'}\n"
    )
    with pytest.raises(SourceTargetsError):
        load_source_targets(path)
