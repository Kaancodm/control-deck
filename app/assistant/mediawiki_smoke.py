"""One bounded encyclopedia query. Default tests do not run this module.

Set ``DECK_ASSISTANT_MEDIAWIKI_API`` to an https ``api.php`` the owner already
chose. The query is the public word ``octet``. A challenge or block stops here.
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

from app.assistant.errors import AssistantError
from app.assistant.search_providers import mediawiki_request_url, normalize_mediawiki
from app.assistant.web import BoundedFetch, parse_allowlist

QUERY = "octet"


def main() -> int:
    api = os.environ.get("DECK_ASSISTANT_MEDIAWIKI_API", "").strip()
    parts = urlsplit(api)
    host = parts.hostname or ""
    if parts.scheme != "https" or not host or not parts.path.endswith("/api.php"):
        print("missing or invalid DECK_ASSISTANT_MEDIAWIKI_API", file=sys.stderr)
        return 2
    client = BoundedFetch(
        allow=parse_allowlist(host),
        https_only=True,
        timeout_s=8,
        max_bytes=16384,
        search_url=None,
        fixture=False,
    )
    requested = mediawiki_request_url(api, QUERY, limit=1)
    try:
        payload, content_type, truncated = client.get_unfollowed(requested, cancelled=lambda: False)
    except AssistantError as exc:
        print(json.dumps({"outcome": "blocked", "code": exc.code, "calls": client.calls}))
        return 3
    if truncated or content_type != "application/json":
        print(
            json.dumps(
                {
                    "outcome": "rejected",
                    "content_type": content_type,
                    "truncated": truncated,
                    "calls": client.calls,
                }
            )
        )
        return 4
    try:
        parsed = json.loads(payload.decode("utf-8"))
        docs = normalize_mediawiki(parsed, host=host)
    except (UnicodeError, json.JSONDecodeError, AssistantError) as exc:
        code = exc.code if isinstance(exc, AssistantError) else "assistant_web_rejected"
        print(json.dumps({"outcome": "rejected", "code": code, "calls": client.calls}))
        return 4
    if not docs:
        print(json.dumps({"outcome": "empty", "fixture": False, "calls": client.calls}))
        return 0
    first = docs[0]
    print(
        json.dumps(
            {
                "outcome": "api",
                "fixture": False,
                "kind": first.kind,
                "title": first.title,
                "published_on": first.published_on,
                "host": first.url_label,
                "calls": client.calls,
                "excerpt_chars": len(first.text),
                "contains_fixture_token": "FIXTURE_REPLY_7" in first.text,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
