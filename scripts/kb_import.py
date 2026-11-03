"""Import a bounded inbox text into the explicit knowledge simulation contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.schemas import DataClass  # noqa: E402
from app.kb.contracts import KnowledgeError, digest_text  # noqa: E402
from app.kb.repository import configured_store  # noqa: E402
from app.privacy.config import get_privacy_settings  # noqa: E402
from app.privacy.files import FileInputError, read_text_input  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an inbox document into the test KB.")
    parser.add_argument("path", help="Relative path below the configured inbox")
    parser.add_argument("--title", required=True)
    parser.add_argument("--data-class", required=True, choices=[item.value for item in DataClass])
    parser.add_argument("--simulate", required=True, action="store_true")
    args = parser.parse_args()
    try:
        content = read_text_input(get_privacy_settings().inbox, args.path)
        identifier = uuid5(
            NAMESPACE_URL, "control-deck:import:" + digest_text(args.title + "\n" + content)
        )
        store = configured_store()
        try:
            store.put_document(
                document_id=identifier,
                title=args.title,
                content=content,
                data_class=DataClass(args.data_class),
            )
        finally:
            store.engine.dispose()
    except (KnowledgeError, FileInputError) as exc:
        print("Import rejected: " + exc.code, file=sys.stderr)
        return 1
    print(identifier)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
