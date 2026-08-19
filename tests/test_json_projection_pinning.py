"""A ``--json <fields>`` projection may not drop the enforcement record.

``aethis_cli_contract`` records that the CLI overrode something the server
said. A field selection that loses it hands the caller a payload that looks
clean while the enforcement went unreported — the failure is silent by
construction, because the caller asked for a subset and got one.

`render.PINNED_JSON_FIELDS` exists to prevent that, and until now nothing
asserted it did: the mutation that deletes the pinning survived the whole
suite. It survived unnoticed for longer still, because the mutation harness
scored every mutation as killed while its own baseline was red.
"""

from __future__ import annotations

from aethis_cli import contract
from aethis_cli.render import PINNED_JSON_FIELDS, _filter_fields

NOTE = {"exit_code": 3, "violations": ["decision"]}


def test_the_enforcement_record_survives_a_projection_that_omits_it():
    record = {"decision": "undetermined", "ruleset_id": "rs_1", contract.CONTRACT_NOTE_KEY: NOTE}

    projected = _filter_fields(record, ["decision"])

    assert projected == {"decision": "undetermined", contract.CONTRACT_NOTE_KEY: NOTE}


def test_it_survives_per_record_in_a_list_projection():
    records = [
        {"decision": "satisfied", "x": 1},
        {"decision": "undetermined", "x": 2, contract.CONTRACT_NOTE_KEY: NOTE},
    ]

    projected = _filter_fields(records, ["decision"])

    assert projected == [
        {"decision": "satisfied"},
        {"decision": "undetermined", contract.CONTRACT_NOTE_KEY: NOTE},
    ]


def test_a_record_that_carries_no_enforcement_record_does_not_gain_one():
    """Pinning preserves what is there; it never fabricates the key, which
    would read as an enforcement that never happened."""
    projected = _filter_fields({"decision": "satisfied", "x": 1}, ["decision"])

    assert projected == {"decision": "satisfied"}


def test_asking_for_it_explicitly_does_not_duplicate_or_reorder_it():
    record = {"decision": "undetermined", contract.CONTRACT_NOTE_KEY: NOTE}

    projected = _filter_fields(record, ["decision", contract.CONTRACT_NOTE_KEY])

    assert projected == record


def test_unpinned_fields_are_still_dropped():
    """The pin is narrow — it must not turn the projection into a no-op."""
    projected = _filter_fields({"decision": "satisfied", "secret": "drop me"}, ["decision"])

    assert "secret" not in projected


def test_the_enforcement_record_is_the_pinned_key():
    """Guards the wiring itself: a pin naming some other key would satisfy
    every assertion above while leaving the real record droppable."""
    assert contract.CONTRACT_NOTE_KEY in PINNED_JSON_FIELDS
