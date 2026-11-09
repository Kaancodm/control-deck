"""Offline golden-set contracts: schema, split, scorer, public report, and privacy lint."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from app.assistant import golden_set
from app.assistant.errors import AssistantError
from app.assistant.golden_set import (
    ACTIONS,
    LANGUAGE_CATEGORIES,
    ROUTES,
    TAGS,
    GoldRow,
    Prediction,
    assign_splits,
    constant_predictions,
    default_gold_path,
    false_trigger_reasons,
    load_gold,
    load_predictions,
    main,
    oracle_predictions,
    parse_pipe_table,
    parse_predictions,
    pii_findings,
    score,
    validate_document,
)

_SCHEMA_PATH = default_gold_path().parent / "golden_set.v1.schema.json"
# Built from parts so this file holds no literal address or token.
_MAIL = "kontakt" + "@" + "example.org"
_PUBLIC_IP = ".".join(["203", "0", "113", "7"])
_LONG_TOKEN = "a1" * 20
_PROVIDER_KEY = "sk-" + "synthetictrap123"


def _row(row_id: str = "r1", route: str = "chat", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": row_id,
        "raw_input": f"Rohsatz {row_id}",
        "vorkontext": "",
        "sprachkategorie": "hochdeutsch",
        "tags": [],
        "expected_route": route,
        "expected_clarification": route == "clarify",
        "erlaubte_aktion": "none",
        "kritische_parameter": {},
        "begruendung": "Dummy",
        "source": "synthetic",
    }
    row.update(overrides)
    return row


def _document(*rows: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "golden_set.v1",
        "synthetic": True,
        "cost_chf": 0,
        "currency": "CHF",
        "network": "forbidden",
        "rows": list(rows) or [_row()],
    }
    document.update(overrides)
    return document


def _gold_row(row_id: str = "r1", route: str = "chat", **overrides: Any) -> GoldRow:
    return validate_document(_document(_row(row_id, route, **overrides))).rows[0]


def _prediction(row_id: str, route: str = "chat", action: str = "none") -> dict[str, Any]:
    return {"id": row_id, "predicted_route": route, "predicted_action": action}


def _schema_validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))


def test_repo_synthetic_is_valid_free_offline_and_covers_the_taxonomy() -> None:
    raw = json.loads(default_gold_path().read_text(encoding="utf-8"))
    assert raw["synthetic"] is True and raw["cost_chf"] == 0 and raw["network"] == "forbidden"
    gold = load_gold()
    assert gold.synthetic is True and 12 <= len(gold.rows) <= 16
    assert {row.expected_route for row in gold.rows} == set(ROUTES)
    assert {row.sprachkategorie for row in gold.rows} == set(LANGUAGE_CATEGORIES)
    assert {tag for row in gold.rows for tag in row.tags} == set(TAGS)
    assert {row.source for row in gold.rows} == {"synthetic"}
    assert pii_findings(gold.rows) == ()
    assert not list(_schema_validator().iter_errors(raw))


def test_json_schema_lists_match_the_module() -> None:
    row = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["row"]["properties"]
    assert tuple(row["expected_route"]["enum"]) == ROUTES
    assert tuple(row["erlaubte_aktion"]["enum"]) == ACTIONS
    assert ACTIONS == ("refuse", "none", "read", "draft", "write_confirmed")
    assert tuple(row["sprachkategorie"]["enum"]) == LANGUAGE_CATEGORIES
    assert tuple(row["tags"]["items"]["enum"]) == TAGS


@pytest.mark.parametrize(
    ("mutate", "code", "schema_rejects"),
    [
        (lambda d: d["rows"][0].update(expected_route="web"), "golden_schema", True),
        (lambda d: d["rows"][0].update(erlaubte_aktion="delete"), "golden_schema", True),
        (lambda d: d["rows"][0].update(sprachkategorie="latein"), "golden_schema", True),
        (lambda d: d["rows"][0].update(tags=["trap_unknown"]), "golden_schema", True),
        (lambda d: d["rows"][0].update(tags=["negation", "negation"]), "golden_schema", True),
        (lambda d: d["rows"][0].update(id="Bad Id"), "golden_schema", True),
        (lambda d: d["rows"][0].update(extra="x"), "golden_schema", True),
        (lambda d: d["rows"][0].pop("begruendung"), "golden_schema", True),
        (lambda d: d["rows"][0].update(raw_input="   "), "golden_schema", True),
        (lambda d: d["rows"][0].update(expected_clarification="ja"), "golden_schema", True),
        (lambda d: d["rows"][0].update(kritische_parameter={"menge": 2}), "golden_schema", True),
        (lambda d: d["rows"][0].update(split="train"), "golden_schema", True),
        (lambda d: d["rows"][0].update(expected_route="clarify"), "golden_schema", True),
        (
            lambda d: d["rows"][0].update(tags=["trap_rights"], erlaubte_aktion="read"),
            "golden_schema",
            True,
        ),
        (
            lambda d: d["rows"][0].update(tags=["trap_secret"], erlaubte_aktion="draft"),
            "golden_schema",
            True,
        ),
        (
            lambda d: d["rows"][0].update(
                tags=["trap_local_only"], expected_route="search_external"
            ),
            "golden_schema",
            True,
        ),
        (lambda d: d.update(schema_version="golden_set.v2"), "golden_schema", True),
        (lambda d: d.update(cost_chf=5), "golden_cost", True),
        (lambda d: d.update(network="allowed"), "golden_network", True),
        (lambda d: d.update(rows=[]), "golden_schema", True),
        (lambda d: d["rows"].append(copy.deepcopy(d["rows"][0])), "golden_duplicate_id", False),
        (lambda d: d["rows"][0].update(source="owner"), "golden_schema", False),
        (
            lambda d: (
                d["rows"][0].update(split="dev"),
                d["rows"].append(_row("r2")),
            ),
            "golden_split",
            False,
        ),
    ],
)
def test_invalid_documents_are_rejected(mutate: Any, code: str, schema_rejects: bool) -> None:
    document = _document()
    mutate(document)
    with pytest.raises(AssistantError) as refused:
        validate_document(document)
    assert refused.value.code == code
    assert bool(list(_schema_validator().iter_errors(document))) is schema_rejects


def test_errors_never_echo_row_text() -> None:
    marker = "GeheimerRohsatz"
    with pytest.raises(AssistantError) as refused:
        validate_document(_document(_row(raw_input=marker, expected_route=marker)))
    assert marker not in json.dumps(refused.value.detail)
    assert refused.value.detail == {"row": 0, "field": "expected_route", "reason": "value"}


def test_pipe_table_matches_the_json_document(tmp_path: Path) -> None:
    text = "\n".join(
        [
            "# schema_version=golden_set.v1",
            "# synthetic=true",
            "# cost_chf=0",
            "# network=forbidden",
            "# a comment without key",
            "id|raw_input|vorkontext|sprachkategorie|tags|expected_route|expected_clarification"
            "|erlaubte_aktion|kritische_parameter|begruendung|source|split",
            r"p1|Satz mit \| Pipe||hochdeutsch|trap_ambiguous,negation|clarify|true|none||Grund"
            "|synthetic|dev",
            "p2|Zwei Stück|Kontext|diktat||order_draft|false|draft|artikel=Kabel;menge=2|Grund"
            "|synthetic|holdout",
        ]
    )
    target = tmp_path / "gold.tsv"
    target.write_text(text, encoding="utf-8")
    from_pipe = load_gold(target)
    from_json = validate_document(
        _document(
            _row(
                "p1",
                "clarify",
                raw_input="Satz mit | Pipe",
                tags=["trap_ambiguous", "negation"],
                begruendung="Grund",
                split="dev",
            ),
            _row(
                "p2",
                "order_draft",
                raw_input="Zwei Stück",
                vorkontext="Kontext",
                sprachkategorie="diktat",
                erlaubte_aktion="draft",
                kritische_parameter={"artikel": "Kabel", "menge": "2"},
                begruendung="Grund",
                split="holdout",
            ),
            currency="CHF",
        )
    )
    assert from_pipe == from_json


_PIPE_OK_META = "# schema_version=golden_set.v1\n# cost_chf=0\n# network=forbidden\n"


@pytest.mark.parametrize(
    ("text", "code", "field"),
    [
        ("# schema_version=golden_set.v1\n# cost_chf=1\nid\nx", "golden_cost", None),
        (_PIPE_OK_META + "# synthetic=vielleicht\nid\nx", "golden_schema", "synthetic"),
        (_PIPE_OK_META + "# synthetic=true\nid|raw_input\nnur_eine_zelle", "golden_schema", "row"),
    ],
)
def test_pipe_table_rejects_bad_metadata_and_shape(text: str, code: str, field: str | None) -> None:
    with pytest.raises(AssistantError) as refused:
        validate_document(parse_pipe_table(text))
    assert refused.value.code == code
    assert refused.value.detail.get("field") == field


def _rows_for(sizes: dict[str, int]) -> list[GoldRow]:
    rows: list[dict[str, Any]] = []
    for route, size in sizes.items():
        rows.extend(_row(f"{route}-{index}", route) for index in range(size))
    return list(validate_document(_document(*rows)).rows)


def test_split_is_deterministic_and_independent_of_row_order() -> None:
    rows = _rows_for(dict.fromkeys(ROUTES, 5))
    first = assign_splits(rows)
    assert first == assign_splits(list(reversed(rows)))
    assert first != assign_splits(rows, seed="other-seed")


@pytest.mark.parametrize(
    "sizes",
    [
        dict.fromkeys(ROUTES, 5),
        dict(zip(ROUTES, (12, 8, 4, 4, 4, 4, 2, 2), strict=True)),
    ],
)
def test_split_of_forty_rows_is_24_16_and_stratified(sizes: dict[str, int]) -> None:
    rows = _rows_for(sizes)
    assignment = assign_splits(rows)
    assert sum(1 for value in assignment.values() if value == "holdout") == 16
    assert sum(1 for value in assignment.values() if value == "dev") == 24
    for route, size in sizes.items():
        holdout = sum(
            1 for row in rows if row.expected_route == route and assignment[row.id] == "holdout"
        )
        assert holdout >= min(2, size // 2)
        assert size - holdout >= min(2, (size + 1) // 2)


def _holdout_counts(rows: list[GoldRow], assignment: dict[str, str]) -> dict[str, int]:
    return dict(Counter(row.expected_route for row in rows if assignment[row.id] == "holdout"))


def test_split_fills_by_holdout_share_not_by_absolute_shortfall() -> None:
    sizes = dict(zip(ROUTES, (30, 10, 4, 4, 4, 4, 4, 4), strict=True))
    rows = _rows_for(sizes)
    counts = _holdout_counts(rows, assign_splits(rows))
    # Filling by absolute shortfall (0.4 * size - holdout) would end at chat 11, status 3.
    assert counts == {**dict.fromkeys(ROUTES, 2), "chat": 10, "status": 4}
    assert sum(counts.values()) == 26


def test_split_breaks_share_ties_by_route_order_not_row_order() -> None:
    rows = _rows_for({"search_local": 8, "status": 8, "clarify": 1})
    counts = _holdout_counts(rows, assign_splits(rows))
    assert counts == {"status": 4, "search_local": 3}


def test_synthetic_split_is_frozen_with_one_row_per_route_on_each_side() -> None:
    gold = load_gold()
    assignment = assign_splits(gold.rows)
    assert {row.id: row.split for row in gold.rows} == assignment
    for route in ROUTES:
        sides = sorted(assignment[row.id] for row in gold.rows if row.expected_route == route)
        assert sides == ["dev", "holdout"]


def test_stored_split_overrides_the_computed_one() -> None:
    rows = [_row(f"f{index}", "chat", split="holdout") for index in range(4)]
    gold = validate_document(_document(*rows))
    report = score(gold, oracle_predictions(gold.rows), split="holdout")
    assert report.row_count == 4
    with pytest.raises(AssistantError) as empty:
        score(gold, oracle_predictions(gold.rows), split="dev")
    assert empty.value.code == "golden_split"


@pytest.mark.parametrize("split", ["all", "dev", "holdout"])
def test_oracle_scores_perfectly_without_false_triggers(split: str) -> None:
    gold = load_gold()
    report = score(gold, oracle_predictions(gold.rows), split=split, label="oracle")
    assert report.macro_f1 == 1.0 and report.route_accuracy == 1.0
    assert report.false_triggers == () and report.misrouted == ()
    assert report.clarification_correct == report.clarification_scored == report.row_count
    assert report.params_matched == report.params_expected > 0
    assert report.cost_chf == 0 and report.network_calls == 0


def test_constant_baseline_scores_the_majority_route_only() -> None:
    gold = load_gold()
    report = score(gold, constant_predictions(gold.rows), label="constant")
    chat = report.per_route["chat"]
    assert (chat.precision, chat.recall, chat.f1, chat.support) == (0.125, 1.0, 0.222222, 2)
    assert report.macro_f1 == round(chat.f1 / len(ROUTES), 6)
    assert report.route_accuracy == 0.125
    assert report.confusion["status"]["chat"] == 2
    # "none" oversteps only the two rows whose allowed action is "refuse".
    assert {item.id for item in report.false_triggers} == {"g02", "g12"}
    assert report.clarification_scored == 0 and report.params_rows_not_scored > 0


@pytest.mark.parametrize(
    ("split", "fitted_route"),
    [("holdout", "status"), ("dev", "chat"), ("all", "search_local")],
)
def test_cli_constant_baseline_fits_on_the_rows_it_does_not_score(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, split: str, fitted_route: str
) -> None:
    routes = {
        "dev": ("status", "status", "status", "search_local", "search_local"),
        "holdout": ("chat", "chat", "chat", "search_local", "search_local"),
    }
    rows = [
        _row(f"{side}-{index}", route, split=side)
        for side, side_routes in routes.items()
        for index, route in enumerate(side_routes)
    ]
    target = tmp_path / "gold.json"
    target.write_text(json.dumps(_document(*rows)), encoding="utf-8")
    assert main(["score", "--gold", str(target), "--baseline", "constant", "--split", split]) == 0
    confusion = json.loads(capsys.readouterr().out)["confusion"]
    predicted = {route for row in confusion.values() for route, count in row.items() if count}
    assert predicted == {fitted_route}


def test_cli_constant_baseline_refuses_a_split_without_rows_to_fit_on(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "gold.json"
    target.write_text(json.dumps(_document(_row(split="holdout"))), encoding="utf-8")
    argv = ["score", "--gold", str(target), "--baseline", "constant", "--split", "holdout"]
    assert main(argv) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "golden_split" and output["detail"] == {"reason": "empty_fit"}


def test_metrics_match_a_hand_computed_example() -> None:
    gold = validate_document(
        _document(_row("a"), _row("b"), _row("c", "status"), _row("d", "status"))
    )
    predictions = parse_predictions(
        [
            _prediction("a", "chat"),
            _prediction("b", "status"),
            _prediction("c", "status"),
            _prediction("d", "status"),
        ]
    )
    report = score(gold, predictions)
    assert report.per_route["chat"].precision == 1.0
    assert report.per_route["chat"].recall == 0.5
    assert report.per_route["chat"].f1 == 0.666667
    assert report.per_route["status"].precision == 0.666667
    assert report.per_route["status"].f1 == 0.8
    # Routes that are neither expected nor predicted do not dilute the macro average.
    assert report.macro_f1 == 0.733333
    assert report.confusion["chat"] == {**dict.fromkeys(ROUTES, 0), "chat": 1, "status": 1}
    assert report.misrouted == ("b",)
    assert report.route_accuracy == 0.75


def test_clarification_and_critical_params_are_scored_when_predicted() -> None:
    gold = validate_document(
        _document(
            _row("a", kritische_parameter={"menge": "2", "artikel": "Kabel"}),
            _row("b", kritische_parameter={"menge": "4"}),
        )
    )
    predictions = parse_predictions(
        [
            {
                **_prediction("a"),
                "predicted_clarification": True,
                "predicted_params": {"menge": " 2 ", "artikel": "kabel"},
            },
            _prediction("b"),
        ]
    )
    report = score(gold, predictions)
    assert (report.clarification_scored, report.clarification_correct) == (1, 0)
    assert (report.params_expected, report.params_matched) == (2, 2)
    assert report.params_rows_not_scored == 1


@pytest.mark.parametrize(
    ("overrides", "route", "action", "reasons"),
    [
        ({"erlaubte_aktion": "read"}, "search_local", "read", ()),
        ({"erlaubte_aktion": "read"}, "search_local", "draft", ("action_exceeds_allowed",)),
        (
            {"erlaubte_aktion": "draft"},
            "order_draft",
            "write_confirmed",
            ("action_exceeds_allowed",),
        ),
        (
            {"tags": ["trap_local_only"], "erlaubte_aktion": "read"},
            "search_external",
            "read",
            ("search_external_local_only",),
        ),
        ({"tags": ["trap_local_only"], "erlaubte_aktion": "read"}, "search_local", "read", ()),
        ({"erlaubte_aktion": "read"}, "search_external", "read", ()),
        (
            {"tags": ["trap_rights"], "erlaubte_aktion": "none"},
            "memory_write",
            "read",
            ("action_exceeds_allowed", "action_on_trap"),
        ),
        (
            {"tags": ["trap_secret"], "erlaubte_aktion": "refuse"},
            "chat",
            "write_confirmed",
            ("action_exceeds_allowed", "action_on_trap"),
        ),
        ({"tags": ["trap_secret"], "erlaubte_aktion": "refuse"}, "chat", "refuse", ()),
        (
            {"tags": ["trap_rights"], "erlaubte_aktion": "refuse"},
            "chat",
            "none",
            ("action_exceeds_allowed",),
        ),
    ],
)
def test_false_trigger_rules(
    overrides: dict[str, Any], route: str, action: str, reasons: tuple[str, ...]
) -> None:
    row = _gold_row(**overrides)
    assert false_trigger_reasons(row, Prediction("r1", route, action)) == reasons


def test_scorer_counts_a_row_once_with_all_reasons() -> None:
    gold = validate_document(
        _document(_row("t", tags=["trap_rights"], erlaubte_aktion="refuse"), _row("u"))
    )
    report = score(
        gold,
        parse_predictions([_prediction("t", "memory_write", "write_confirmed"), _prediction("u")]),
    )
    assert len(report.false_triggers) == 1
    assert report.false_triggers[0].reasons == ("action_exceeds_allowed", "action_on_trap")
    assert report.public()["false_trigger_count"] == 1


@pytest.mark.parametrize(
    ("predictions", "reason"),
    [
        ([_prediction("r1"), _prediction("r1")], "duplicate"),
        ([_prediction("r1"), _prediction("geist")], "unknown"),
        ([], "missing"),
    ],
)
def test_prediction_sets_must_match_the_gold_ids(
    predictions: list[dict[str, Any]], reason: str
) -> None:
    gold = validate_document(_document())
    with pytest.raises(AssistantError) as refused:
        score(gold, parse_predictions(predictions))
    assert refused.value.code == "golden_prediction"
    assert refused.value.detail["reason"] == reason


@pytest.mark.parametrize(
    "bad",
    [
        {"id": "r1", "predicted_route": "web", "predicted_action": "none"},
        {"id": "r1", "predicted_route": "chat", "predicted_action": "delete"},
        {"id": "r1", "predicted_route": "chat"},
        {**_prediction("r1"), "predicted_clarification": "ja"},
        {**_prediction("r1"), "predicted_params": {"menge": 2}},
        {**_prediction("r1"), "raw_output": "Modelltext"},
        "kein Objekt",
    ],
)
def test_malformed_predictions_are_rejected(bad: Any) -> None:
    with pytest.raises(AssistantError) as refused:
        parse_predictions([bad])
    assert refused.value.code == "golden_prediction"


def test_predictions_load_from_array_and_json_lines(tmp_path: Path) -> None:
    array = tmp_path / "array.json"
    array.write_text(json.dumps([_prediction("r1")]), encoding="utf-8")
    lines = tmp_path / "lines.jsonl"
    lines.write_text(json.dumps(_prediction("r1")) + "\n\n", encoding="utf-8")
    assert load_predictions(array) == load_predictions(lines)
    broken = tmp_path / "broken.json"
    broken.write_text("{nope", encoding="utf-8")
    with pytest.raises(AssistantError) as refused:
        load_predictions(broken)
    assert refused.value.code == "golden_file"


@pytest.mark.parametrize(
    "content",
    [b'{"raw_input": "GeheimerRohsatz', b"\xff GeheimerRohsatz"],
    ids=["invalid_json", "invalid_utf8"],
)
def test_file_errors_do_not_chain_the_exception_holding_the_raw_text(
    tmp_path: Path, content: bytes
) -> None:
    target = tmp_path / "broken.json"
    target.write_bytes(content)
    with pytest.raises(AssistantError) as refused:
        load_gold(target)
    assert refused.value.code == "golden_file"
    assert refused.value.__cause__ is None and refused.value.__context__ is None


def test_public_report_holds_no_row_text() -> None:
    gold = load_gold()
    for predictions in (oracle_predictions(gold.rows), constant_predictions(gold.rows)):
        blob = json.dumps(score(gold, predictions).public(), ensure_ascii=False)
        for row in gold.rows:
            assert row.raw_input not in blob
            assert row.begruendung not in blob
            if row.vorkontext:
                assert row.vorkontext not in blob
            for value in row.kritische_parameter.values():
                if len(value) > 1:
                    assert value not in blob
    assert "raw_input" not in blob and "vorkontext" not in blob


@pytest.mark.parametrize(
    ("field", "value", "rule"),
    [
        ("raw_input", f"Schreib an {_MAIL} bitte", "email"),
        ("vorkontext", f"Server auf {_PUBLIC_IP}", "ipv4"),
        ("begruendung", f"Referenz {_LONG_TOKEN}", "long_token"),
        ("raw_input", f"Nutze {_PROVIDER_KEY}", "provider_api_key"),
        ("kritische_parameter", {"empfaenger": _MAIL}, "email"),
    ],
)
def test_privacy_lint_rejects_synthetic_rows_without_echoing_the_match(
    field: str, value: Any, rule: str
) -> None:
    with pytest.raises(AssistantError) as refused:
        validate_document(_document(_row(**{field: value})))
    assert refused.value.code == "golden_pii"
    findings = refused.value.detail["findings"]
    assert {"id": "r1", "field": field, "rule": rule} in findings
    detail = json.dumps(refused.value.detail)
    assert _MAIL not in detail and _PUBLIC_IP not in detail and _LONG_TOKEN not in detail
    assert _PROVIDER_KEY not in detail


def test_private_file_skips_the_lint_but_cannot_live_in_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    private = _document(
        _row(raw_input=f"Mail an {_MAIL}", source="owner"),
        synthetic=False,
    )
    target = tmp_path / "private.json"
    target.write_text(json.dumps(private), encoding="utf-8")
    gold = load_gold(target)
    assert gold.synthetic is False and len(pii_findings(gold.rows)) == 1
    monkeypatch.setattr(golden_set, "_REPO_ROOT", tmp_path)
    with pytest.raises(AssistantError) as refused:
        load_gold(target)
    assert refused.value.code == "golden_private_in_repo"


def test_golden_directory_ships_only_the_schema_and_the_synthetic_set() -> None:
    directory = default_gold_path().parent
    shipped = sorted(path.name for path in directory.iterdir())
    assert shipped == ["golden_set.v1.schema.json", "synthetic_v1.json"]


def test_cli_defaults_work_offline_and_print_no_row_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network")

    monkeypatch.setattr("socket.create_connection", boom)
    monkeypatch.setattr("urllib.request.urlopen", boom)
    gold = load_gold()

    assert main(["validate"]) == 0
    validated = json.loads(capsys.readouterr().out)
    assert validated["ok"] is True and validated["rows"] == len(gold.rows)
    assert validated["split"] == {"dev": 8, "holdout": 8} and validated["frozen_split"] is True

    assert main(["score"]) == 0
    oracle = capsys.readouterr().out
    assert json.loads(oracle)["macro_f1"] == 1.0
    assert not any(row.raw_input in oracle for row in gold.rows)

    assert main(["score", "--baseline", "constant", "--split", "holdout"]) == 0
    constant = json.loads(capsys.readouterr().out)
    assert constant["label"] == "constant" and constant["row_count"] == 8

    predictions = tmp_path / "pred.json"
    predictions.write_text(json.dumps([_prediction(row.id) for row in gold.rows]), encoding="utf-8")
    assert main(["score", "--pred", str(predictions)]) == 0
    assert json.loads(capsys.readouterr().out)["label"] == "predictions"

    assert main(["split"]) == 0
    assert json.loads(capsys.readouterr().out)["assignment"] == assign_splits(gold.rows)


def test_cli_reports_codes_only_and_exits_non_zero(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "bad.json"
    target.write_text(json.dumps(_document(_row(raw_input=f"Mail an {_MAIL}"))), encoding="utf-8")
    assert main(["validate", str(target)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"] == "golden_pii" and _MAIL not in output
    assert main(["validate", str(tmp_path / "missing.json")]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "golden_file"
