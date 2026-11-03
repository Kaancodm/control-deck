"""Conservative, bounded privacy patterns returning locations rather than values."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from ipaddress import IPv6Address
from itertools import groupby


class FindingKind(StrEnum):
    SECRET = "secret"
    REDACTABLE_PII = "redactable_pii"
    PROTECTED_IDENTIFIER = "protected_identifier"


@dataclass(frozen=True)
class Finding:
    """Offsets refer to Unicode characters in the supplied source text."""

    kind: FindingKind
    rule: str
    source: str
    start: int
    end: int


_MAX_FINDINGS = 128
_SPACE = r"[ \t\u00a0\u202f]"
_SECRET_PATTERNS = (
    ("aws_access_key", r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    ("github_token", r"(?<![A-Za-z0-9_])(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{16,512}"),
    ("provider_api_key", r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_-]{12,512}"),
    (
        "jwt",
        r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,2000}\."
        r"[A-Za-z0-9_-]{1,8000}\.[A-Za-z0-9_-]{8,2048}",
    ),
    (
        "private_key",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    ),
    (
        "credential_assignment",
        r"(?i)(?<!\w)(?:api[_ -]?key|access[_ -]?token|token|secret|password|passwort|"
        r"authorization)\b[\"']?\s*[:=]\s*(?P<quote>[\"']?)"
        r"(?P<value>[^\s\"',;{}\[\]]{1,512})",
    ),
    ("bearer_token", r"(?i)(?<!\w)Bearer[ \t]+[A-Za-z0-9._~+/-]{8,2048}={0,2}"),
    ("credential_url", r"(?i)\bhttps?://[^\s/@:]{1,128}:[^\s/@]{1,512}@"),
)

# ISO country lengths let formatted identifiers stop before following prose.
# These are syntax checks, not proof that an account or its checksum is valid.
_IBAN_LENGTHS = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "LY": 25,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NL": 18,
    "NO": 15,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "SA": 24,
    "SC": 31,
    "SD": 18,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
}
_IBAN_PATTERN = (
    r"(?i)(?<!\w)(?:"
    + "|".join(
        country
        + _SPACE
        + r"?\d"
        + _SPACE
        + r"?\d(?:"
        + _SPACE
        + r"?[A-Z0-9]){"
        + str(length - 4)
        + "}"
        for country, length in _IBAN_LENGTHS.items()
    )
    + r")(?!\w)"
)
_PII_PATTERNS = (
    (
        "email",
        r"(?i)(?<![\w.!#$%&'*+/=?^`{|}~-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@"
        r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.){1,8}[A-Z]{2,63}(?![\w.-])",
    ),
    (
        "phone",
        r"(?<![\w+])(?:\+|00)(?:41|49)(?:[ \t\u00a0\u202f().-]?\d){8,13}(?!\d)",
    ),
    ("phone", r"(?<!\w)0[2-9]\d(?:[ \t\u00a0\u202f.-]?\d){7}(?!\d)"),
    ("iban", _IBAN_PATTERN),
    ("ahv", r"(?<!\d)756[ .-]?\d{4}[ .-]?\d{4}[ .-]?\d{2}(?!\d)"),
)
_PROTECTED_PATTERNS = (
    ("internal_address", r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
    (
        "internal_address",
        r"(?i)(?<![\w:])(?:[A-F0-9]{0,4}:){2,7}"
        r"(?:[A-F0-9]{0,4}|(?:\d{1,3}\.){3}\d{1,3})(?![\w:.])",
    ),
    (
        "home_path",
        r"(?i)(?<!\w)(?:/(?:home|Users)/[^/\s\"']{1,255}|"
        r"[A-Z]:[\\/]Users[\\/][^\\/\s\"']{1,255}|~/)[^\s\"'<>]{0,4096}",
    ),
    (
        "internal_hostname",
        r"(?i)(?<![\w.-])(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.){1,8}"
        r"(?:internal|local|lan|corp)(?![\w.-])|(?<![\w.-])localhost(?![\w.-])",
    ),
)
_COMPILED = {
    FindingKind.SECRET: tuple((name, re.compile(pattern)) for name, pattern in _SECRET_PATTERNS),
    FindingKind.PROTECTED_IDENTIFIER: tuple(
        (name, re.compile(pattern)) for name, pattern in _PROTECTED_PATTERNS
    ),
    FindingKind.REDACTABLE_PII: tuple(
        (name, re.compile(pattern)) for name, pattern in _PII_PATTERNS
    ),
}


def _is_internal_address(value: str) -> bool:
    if ":" in value:
        try:
            address = IPv6Address(value)
        except ValueError:
            return False
        if address.ipv4_mapped is not None:
            return _is_internal_address(str(address.ipv4_mapped))
        return (
            address.is_loopback
            or address.is_unspecified
            or address.is_link_local
            or address.packed[0] & 0xFE == 0xFC
        )
    octets = [int(part) for part in value.split(".")]
    if any(part > 255 for part in octets):
        return False
    first, second, _, _ = octets
    return (
        first in (10, 127)
        or (first == 172 and 16 <= second <= 31)
        or (first == 192 and second == 168)
        or (first == 100 and 64 <= second <= 127)
        or (first == 169 and second == 254)
    )


def _matches(text: str, *, source: str, kind: FindingKind) -> Iterator[Finding]:
    for rule, pattern in _COMPILED[kind]:
        count = 0
        for match in pattern.finditer(text):
            if rule == "internal_address" and not _is_internal_address(match.group()):
                continue
            if (
                rule == "credential_assignment"
                and not match.group("quote")
                and match.group("value").lower() in {"none", "null", "true", "false"}
            ):
                continue
            yield Finding(kind, rule, source, match.start(), match.end())
            count += 1
            if count == _MAX_FINDINGS:
                break


def _literal_alternation(literals: Sequence[str], depth: int = 8) -> str:
    """Factor consecutive prefixes without changing case or alternative order."""
    if len(literals) == 1:
        return re.escape(literals[0])
    if depth == 0:
        return "(?:" + "|".join(re.escape(literal) for literal in literals) + ")"
    branches = []
    for first, group in groupby(literals, key=lambda literal: literal[:1]):
        if first:
            branches.append(
                re.escape(first)
                + _literal_alternation([literal[1:] for literal in group], depth - 1)
            )
        else:
            branches.append("")
    return "(?:" + "|".join(branches) + ")"


@lru_cache(maxsize=4)
def _blocklist_patterns(entries: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    groups: dict[tuple[bool, bool], list[str]] = {}
    for entry in sorted(set(entries), key=lambda item: (-len(item), item)):
        if entry:
            bounds = (
                entry[0].isalnum() or entry[0] == "_",
                entry[-1].isalnum() or entry[-1] == "_",
            )
            groups.setdefault(bounds, []).append(entry)
    patterns = []
    for (start_word, end_word), literals in groups.items():
        prefix = r"(?<!\w)" if start_word else ""
        suffix = r"(?!\w)" if end_word else ""
        # Prefix factoring avoids testing every literal at each word boundary.
        # Bound the nesting depth even for 256 successive configured prefixes.
        # Literal escaping and original branch order preserve matching semantics.
        patterns.append(re.compile(prefix + _literal_alternation(literals) + suffix, re.IGNORECASE))
    return tuple(patterns)


def _blocklist_matches(text: str, source: str, blocklist: Sequence[str]) -> Iterator[Finding]:
    for pattern in _blocklist_patterns(tuple(blocklist)):
        for count, match in enumerate(pattern.finditer(text), start=1):
            yield Finding(
                FindingKind.PROTECTED_IDENTIFIER, "blocklist", source, match.start(), match.end()
            )
            if count == _MAX_FINDINGS:
                break


def _select_nonoverlapping(candidates: Sequence[Finding], selected: list[Finding]) -> None:
    for finding in sorted(candidates, key=lambda item: (item.start, -item.end, item.rule)):
        if len(selected) == _MAX_FINDINGS:
            return
        if not any(finding.start < other.end and other.start < finding.end for other in selected):
            selected.append(finding)


def scan_text(text: str, *, source: str, blocklist: Sequence[str] = ()) -> list[Finding]:
    """Find supported patterns without retaining matched values.

    Secret findings take priority over protected identifiers and redactable PII,
    including when the 128-finding limit is reached. Within a category, the first
    match wins, choosing the longest match at the same position. Matching is
    conservative syntax recognition, not exhaustive secret or identity discovery.
    Blocklist literals use Unicode-aware case-insensitive word boundaries.
    """
    selected: list[Finding] = []
    for kind in (FindingKind.SECRET, FindingKind.PROTECTED_IDENTIFIER, FindingKind.REDACTABLE_PII):
        candidates = list(_matches(text, source=source, kind=kind))
        if kind == FindingKind.PROTECTED_IDENTIFIER:
            candidates.extend(_blocklist_matches(text, source, blocklist))
        _select_nonoverlapping(candidates, selected)
        if len(selected) == _MAX_FINDINGS:
            break
    return sorted(selected, key=lambda item: (item.start, item.end, item.rule))
