"""Supply-chain hardening checks for the CI workflow."""

import re
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
SHA_REF = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
EXPECTED = {
    "actions/checkout": "11d5960a326750d5838078e36cf38b85af677262",
    "astral-sh/setup-uv": "d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86",
}


def _workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps() -> list[dict]:
    return [s for job in _workflow()["jobs"].values() for s in job["steps"]]


def test_permissions_are_read_only_at_top_level() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_checkout_does_not_persist_credentials() -> None:
    checkouts = [s for s in _steps() if s.get("uses", "").startswith("actions/checkout@")]
    assert checkouts
    for step in checkouts:
        assert step["with"]["persist-credentials"] is False


def test_all_actions_pinned_to_full_sha() -> None:
    uses = [s["uses"] for s in _steps() if "uses" in s]
    assert uses
    for ref in uses:
        assert SHA_REF.match(ref), ref
    pinned = {ref.split("@")[0]: ref.split("@")[1] for ref in uses}
    for name, sha in EXPECTED.items():
        assert pinned[name] == sha


def test_pinned_actions_carry_version_comment() -> None:
    for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
        if "uses:" in line:
            assert re.search(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+$", line), line


PGVECTOR_IMAGE = (
    "pgvector/pgvector:0.8.7-pg17"
    "@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8"
)


def test_pgvector_service_image_is_pinned_by_version_and_digest() -> None:
    images = [
        service["image"]
        for job in _workflow()["jobs"].values()
        for service in job.get("services", {}).values()
        if "pgvector" in service["image"]
    ]
    assert images == [PGVECTOR_IMAGE]


def test_no_unpinned_pgvector_reference_in_repository() -> None:
    root = WORKFLOW.parents[2]
    pattern = re.compile(r"pgvector/pgvector:\S+")
    for path in [WORKFLOW, *root.glob("docker/**/*"), *root.glob("**/docker-compose*.y*ml")]:
        if path.is_file() and ".venv" not in path.parts:
            for match in pattern.findall(path.read_text(encoding="utf-8", errors="ignore")):
                assert match.rstrip("\"'") == PGVECTOR_IMAGE, (path, match)
