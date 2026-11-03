"""Synthetic scanner examples, independent from owner-authored reference cases."""

from dataclasses import asdict

import pytest

from app.privacy.scanner import FindingKind, scan_text


def test_synthetic_key_is_secret_without_disclosing_value() -> None:
    value = "sk-TEST-" + "NOT-A-REAL-CREDENTIAL"
    findings = scan_text(value, source="inputs[0].value")
    assert findings and findings[0].kind == FindingKind.SECRET
    assert (findings[0].start, findings[0].end) == (0, len(value))
    assert value not in repr(findings)
    assert value not in str(asdict(findings[0]))


@pytest.mark.parametrize(
    "value, rule",
    [
        ("AKIA" + "A" * 16, "aws_access_key"),
        ("ASIA" + "B" * 16, "aws_access_key"),
        ("ghp_" + "x" * 36, "github_token"),
        ("github_pat_" + "x" * 30, "github_token"),
        ("sk-ant-" + "x" * 32, "provider_api_key"),
        ("eyJ" + "x" * 12 + "." + "y" * 20 + "." + "z" * 16, "jwt"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
        ("-----BEGIN " + "OPENSSH PRIVATE KEY-----", "private_key"),
        ("pAsSwOrD=" + "synthetic", "credential_assignment"),
        ('"api_key": "' + "synthetic" + '"', "credential_assignment"),
        ("Bearer " + "x" * 20, "bearer_token"),
        ("https://" + "synthetic:password" + "@" + "example.invalid", "credential_url"),
    ],
)
def test_secret_catalog(value: str, rule: str) -> None:
    findings = scan_text(value, source="instructions")
    assert any(item.rule == rule and item.kind == FindingKind.SECRET for item in findings)
    assert all(0 <= item.start < item.end <= len(value) for item in findings)


@pytest.mark.parametrize(
    "value, rule",
    [
        ("synthetic" + "@" + "example.invalid", "email"),
        ("+41 " + "99 000 00 00", "phone"),
        ("0049 " + "00 000 000 00", "phone"),
        ("099" + "0" * 7, "phone"),
        ("CH00" + "0" * 17, "iban"),
        ("DE00 " + "0000 " * 4 + "00", "iban"),
        ("756." + "0000.0000.00", "ahv"),
    ],
)
def test_pii_catalog(value: str, rule: str) -> None:
    findings = scan_text(value, source="inputs[0].value")
    assert any(item.rule == rule and item.kind == FindingKind.REDACTABLE_PII for item in findings)


@pytest.mark.parametrize(
    "parts",
    [
        (10, 1, 2, 3),
        (127, 0, 0, 1),
        (172, 16, 1, 2),
        (192, 168, 1, 2),
        (100, 64, 1, 2),
        (100, 127, 1, 2),
        (169, 254, 1, 2),
    ],
)
def test_internal_ipv4_ranges(parts: tuple[int, ...]) -> None:
    findings = scan_text(".".join(map(str, parts)), source="labels[0].value")
    assert findings[0].kind == FindingKind.PROTECTED_IDENTIFIER
    assert findings[0].rule == "internal_address"


@pytest.mark.parametrize(
    "value",
    ["::" + "1", "fd00" + "::1", "fe80" + "::1", "::ffff:" + ".".join(map(str, (10, 1, 2, 3)))],
)
def test_internal_ipv6_ranges(value: str) -> None:
    findings = scan_text(value, source="instructions")
    assert findings and findings[0].rule == "internal_address"


@pytest.mark.parametrize(
    "parts", [(100, 63, 1, 2), (100, 128, 1, 2), (172, 32, 1, 2), (999, 1, 2, 3)]
)
def test_noninternal_or_invalid_addresses_do_not_match(parts: tuple[int, ...]) -> None:
    assert scan_text(".".join(map(str, parts)), source="instructions") == []


@pytest.mark.parametrize(
    "value",
    [
        "/home/" + "synthetic/file.txt",
        "/Users/" + "synthetic/file.txt",
        "C:\\Users\\" + "synthetic\\file.txt",
        "~/" + "file.txt",
        "synthetic" + ".internal",
        "localhost",
    ],
)
def test_protected_paths_and_hosts(value: str) -> None:
    findings = scan_text(value, source="instructions")
    assert findings and findings[0].kind == FindingKind.PROTECTED_IDENTIFIER


@pytest.mark.parametrize(
    "text",
    [
        "Summarize the document in three sentences.",
        "No API key is required for this example.",
        "The report date is 2026-11-03.",
        "Version 2.3.4 contains 1200 changes.",
        "Count 123456789 items and calculate 12.5 percent.",
        "Use relative paths such as inputs/report.txt.",
        '{"status": "queued", "count": 7}',
        "Die Uebersicht zeigt offene Auftraege und deren Status.",
        "Unicode: ä, ö, ü, Ω and an emoji 🧪.",
        "The password field is omitted; token is optional.",
        '{"token": null, "secret": false}',
    ],
)
def test_harmless_texts_have_no_findings(text: str) -> None:
    assert scan_text(text, source="instructions") == []


def test_blocklist_is_literal_case_insensitive_and_respects_word_boundaries() -> None:
    text = "SampleProjectX SAMPLEPROJECT xZZy x.*y ÄÜÖ-SYNTH"
    findings = scan_text(text, source="title", blocklist=("SampleProject", "x.*y", "äüö-synth"))
    assert [text[item.start : item.end] for item in findings] == [
        "SAMPLEPROJECT",
        "x.*y",
        "ÄÜÖ-SYNTH",
    ]
    assert all(item.rule == "blocklist" for item in findings)
    assert all(item.kind == FindingKind.PROTECTED_IDENTIFIER for item in findings)


def test_first_longest_overlap_is_deterministic() -> None:
    text = "SYNTHETIC PROJECT and SYNTHETIC"
    expected = ["SYNTHETIC PROJECT", "SYNTHETIC"]
    for blocklist in [("SYNTHETIC", "SYNTHETIC PROJECT"), ("SYNTHETIC PROJECT", "SYNTHETIC")]:
        assert [
            text[item.start : item.end]
            for item in scan_text(text, source="title", blocklist=blocklist)
        ] == expected


def test_unicode_offsets_refer_to_original_text() -> None:
    text = "🧪 prefix ÄÜÖ-SYNTH suffix"
    finding = scan_text(text, source="title", blocklist=("äüö-synth",))[0]
    assert finding.start == len("🧪 prefix ")
    assert text[finding.start : finding.end] == "ÄÜÖ-SYNTH"


def test_findings_are_capped_without_hiding_a_late_secret() -> None:
    email = "synthetic" + "@" + "example.invalid"
    secret = "sk-TEST-" + "NOT-A-REAL-CREDENTIAL"
    text = (email + " ") * 200 + secret
    findings = scan_text(text, source="instructions")
    assert len(findings) == 128
    assert any(
        item.kind == FindingKind.SECRET and text[item.start : item.end] == secret
        for item in findings
    )


def test_large_text_with_many_literals_finds_supported_end_marker() -> None:
    blocklist = tuple(f"SYNTHETIC-LITERAL-{index:04d}" for index in range(1000))
    text = ("ordinary harmless prose. " * 50000) + "SYNTHETIC-LITERAL-0999"
    findings = scan_text(text, source="inputs[0].content", blocklist=blocklist)
    assert len(findings) == 1
    assert text[findings[0].start : findings[0].end] == "SYNTHETIC-LITERAL-0999"
