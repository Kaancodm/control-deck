"""Stage 2 tests: Hindsight writer, integration, nightly run and S-K80.

All tests are offline. The transport boundary is `opener.open(request)`:
the real HindsightWriter, HindsightClient and UrllibTransport build real
urllib requests, and `FakeOpener` captures them byte for byte. Any socket
use fails the test. The fake implements the ASSUMED Hindsight contract (F2);
passing here proves the client side only, never the server API.

Mapping (Fable plan, see README "Weitblick Stufe 2"):
T1 test_t1_*  T2 test_t2_*  T3 test_t3_*  T4 test_t4_*  T5 test_t5_* (+ S-K80)
T6 test_t6_*  T7 test_t7_*  T8 test_t8_* (provisional, offline part only)
S-K80 test_sk80_*  F3 test_f3_*  Pfadfilter test_privacy_*  F2 test_f2_* (Vertragspruefer)
"""

import contextlib
import hashlib
import io
import json
import re
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from weitblick import exitcodes, nachtlauf, sk80, vertrag
from weitblick.__main__ import main as cli_main
from weitblick.core import generate, read_sources
from weitblick.hindsight import (
    ABANDONED,
    CONFIRMED,
    FAILED,
    MAX_ATTEMPTS,
    RESERVED,
    RESUMED,
    RETAIN_ALLOWED_TOP_FIELDS,
    RETAIN_ITEM_FIELDS,
    RETAIN_TOP_FIELDS,
    RETURNED,
    SENDING,
    UNCLEAR,
    HindsightAuthError,
    HindsightClient,
    HindsightConfig,
    HindsightConfigError,
    HindsightWriter,
    TransportError,
    UrllibTransport,
    bank_record,
    build_chunk_id,
    config_from_env,
    default_opener,
    retain_body,
)
from weitblick.pipeline import run
from weitblick.writer import FileWriter

URL = "http://hindsight.test.invalid:8888"
TOKEN = "test-token-not-real-0000"
PRIVATE_DIR = "privat-fundstelle-7f3a"
SK80_BANK_A = "weitblick-sk80-a"
SK80_BANK_B = "weitblick-sk80-b"
PATH_RE = re.compile(r"^/v1/default/banks/([^/]+)/(memories|memories/recall|documents/([^/]+))$")
CHUNK_RE = re.compile(r"^/v1/default/chunks/([^/]+)$")


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHindsight:
    """In-memory model of the assumed contract with bank-scoped documents."""

    def __init__(self):
        self.banks = {}
        self.calls = []
        self.down = None  # exception instance raised for every call
        self.status_override = None  # (status, body) for retain calls
        # Per-retain overrides; None in the list means the normal success path.
        self.retain_queue = None
        self.chunks = {}
        self.leak_get = False
        self.leak_delete = False
        self.leak_chunk = False
        self.empty_success_get = False
        self.empty_success_chunk = False
        self.auth_fail_bank = None
        self.allowed = {"POST", "GET", "DELETE"}

    def open(self, request, timeout=None):
        body = request.data
        call = {
            "method": request.get_method(),
            "url": request.full_url,
            "headers": dict(request.header_items()),
            "body": body,
            "timeout": timeout,
        }
        self.calls.append(call)
        assert request.full_url.startswith(URL + "/"), "Unexpected Hindsight host"
        path = request.full_url[len(URL) :]
        method = call["method"]
        if self.down is not None:
            raise self.down
        if not request.get_header("Authorization"):
            return self._reply(request, 401, {"detail": "Unauthorized"})
        chunk_match = CHUNK_RE.match(path)
        if chunk_match is not None:
            if method != "GET" or method not in self.allowed:
                raise AssertionError(f"Unexpected Hindsight call: {method} {path}")
            chunk_id = urllib.request.unquote(chunk_match.group(1))
            return self._open_chunk(request, chunk_id)
        match = PATH_RE.match(path)
        if match is None or method not in self.allowed:
            raise AssertionError(f"Unexpected Hindsight call: {method} {path}")
        bank, kind, doc_id = match.group(1), match.group(2), match.group(3)
        if self.auth_fail_bank == bank:
            return self._reply(request, 401, {"detail": "Unauthorized"})
        if kind == "memories" and method == "POST":
            if self.retain_queue:
                item = self.retain_queue.pop(0)
                if item is not None:
                    status, raw = item
                    return self._reply(request, status, raw)
            if self.status_override:
                status, raw = self.status_override
                return self._reply(request, status, raw)
            payload = json.loads(body)
            unknown = set(payload) - RETAIN_ALLOWED_TOP_FIELDS
            if unknown:
                raise AssertionError(f"Unknown retain fields: {sorted(unknown)}")
            for item in payload["items"]:
                extra_item = set(item) - set(RETAIN_ITEM_FIELDS)
                if extra_item:
                    raise AssertionError(f"Unknown retain item fields: {sorted(extra_item)}")
                self.banks.setdefault(bank, {})[item["document_id"]] = {
                    "content": item["content"],
                    "metadata": item["metadata"],
                }
                self.chunks[build_chunk_id(bank, item["document_id"], 0)] = {
                    "bank_id": bank,
                    "document_id": item["document_id"],
                    "chunk_text": item["content"],
                }
            return self._reply(request, 200, {"success": True, "bank_id": bank, "items_count": 1})
        if kind == "memories/recall" and method == "POST":
            query = json.loads(body)["query"]
            hits = [
                {"document_id": k, "text": v["content"]}
                for k, v in self.banks.get(bank, {}).items()
                if query in v["content"]
            ]
            return self._reply(request, 200, {"results": hits})
        if doc_id is not None and method in {"GET", "DELETE"}:
            doc_id = urllib.request.unquote(doc_id)
            owner = bank if doc_id in self.banks.get(bank, {}) else None
            if owner is None and method == "GET" and self.empty_success_get:
                return self._reply(request, 200, {})
            leak = self.leak_get if method == "GET" else self.leak_delete
            if owner is None and leak:
                owner = next((b for b, docs in self.banks.items() if doc_id in docs), None)
            if owner is None:
                return self._reply(request, 404, {"detail": "Document not found"})
            if method == "DELETE":
                del self.banks[owner][doc_id]
                return self._reply(request, 200, {"success": True})
            doc = self.banks[owner][doc_id]
            return self._reply(
                request,
                200,
                {
                    "id": doc_id,
                    "bank_id": owner,
                    "original_text": doc["content"],
                    "metadata": doc["metadata"],
                },
            )
        raise AssertionError(f"Unexpected Hindsight call: {method} {path}")

    def _open_chunk(self, request, chunk_id):
        record = self.chunks.get(chunk_id)
        parsed = chunk_id.rsplit("_", 2)
        bank = parsed[0] if len(parsed) == 3 else None
        if self.auth_fail_bank is not None and bank == self.auth_fail_bank:
            return self._reply(request, 401, {"detail": "Unauthorized"})
        if record is None and self.leak_chunk:
            document_id = parsed[1] if len(parsed) == 3 else None
            record = next(
                (item for item in self.chunks.values() if item["document_id"] == document_id),
                None,
            )
        if record is None and (self.empty_success_get or self.empty_success_chunk):
            return self._reply(request, 200, {})
        if record is None:
            return self._reply(request, 404, {"detail": "Chunk not found"})
        return self._reply(
            request,
            200,
            {
                "chunk_id": chunk_id,
                "bank_id": record["bank_id"],
                "document_id": record["document_id"],
                "chunk_text": record["chunk_text"],
            },
        )

    @staticmethod
    def _reply(request, status, payload):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if 200 <= status < 300:
            return FakeResponse(status, raw)
        raise urllib.error.HTTPError(request.full_url, status, "fake", {}, io.BytesIO(raw))

    def retains(self):
        return [c for c in self.calls if c["method"] == "POST" and c["url"].endswith("/memories")]


class Stage2Base(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket", "socket.create_connection"):
            patcher = patch(target, side_effect=AssertionError("Network forbidden"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / PRIVATE_DIR
        self.src.mkdir()
        self.out = self.root / "output"
        samples = {
            "ENTSCHEIDE": "| Nummer | Thema | Optionen/Empfehlung | Ablauf-Status | Bezug |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| E-901 | Suche | em: Index; Option 2: einfache Tabelle | erledigt | S-901 |\n"
            "\n## E-902\n- Thema: Suche\n- Ablauf-Status: offen\n",
            "SERVER": "| Nummer | Dienst | Status | Letzte Prüfung |\n"
            "| --- | --- | --- | --- |\n| S-901 | Archiv | bereit | 2026-01-01 |\n",
            "AUFTRAEGE": "## A-901\n- Titel: Alter Auftrag\n- Ablauf-Status: blockiert\n",
        }
        self.paths = {}
        for name, content in samples.items():
            path = self.src / (name + ".md")
            path.write_text(content, encoding="utf-8")
            self.paths[name] = path
        self.fake = FakeHindsight()
        self.transport = UrllibTransport(opener=self.fake)
        self.env = {
            "WEITBLICK_HINDSIGHT_ENABLED": "1",
            "WEITBLICK_HINDSIGHT_URL": URL,
            "WEITBLICK_HINDSIGHT_TOKEN": TOKEN,
        }

    def entries(self, day="2026-01-02"):
        return generate(read_sources(self.paths), day)

    def writer(self):
        client = HindsightClient(config_from_env(self.env), self.transport)
        return HindsightWriter(self.out, client)

    def run_pipeline(self, env=None, **kwargs):
        lines = []
        code = run(
            self.paths,
            self.out,
            self.env if env is None else env,
            today="2026-01-02",
            transport=self.transport,
            out=lines.append,
            **kwargs,
        )
        return code, "\n".join(lines)

    def nightly_env(self, **extra):
        env = dict(self.env)
        env.update(
            WEITBLICK_ENTSCHEIDE=str(self.paths["ENTSCHEIDE"]),
            WEITBLICK_SERVER=str(self.paths["SERVER"]),
            WEITBLICK_AUFTRAEGE=str(self.paths["AUFTRAEGE"]),
            WEITBLICK_OUTPUT=str(self.out),
        )
        env.update(extra)
        return env

    def run_nightly(self, env):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = nachtlauf.main(env, transport=self.transport)
        return code, stdout.getvalue() + stderr.getvalue()

    def jsonl_bytes(self):
        return (self.out / "weitblick.jsonl").read_bytes()

    def status(self):
        return json.loads((self.out / "hindsight-status.json").read_text())["eintraege"]

    def assert_no_sensitive_output(self, text):
        for value in (URL, TOKEN, "hindsight.test.invalid", PRIVATE_DIR, "Waere denkbar"):
            self.assertNotIn(value, text)


class WriterAndFlowTests(Stage2Base):
    def test_t1_file_path_unchanged_without_hindsight(self):
        code, text = self.run_pipeline(env={})
        self.assertEqual(code, 0)
        self.assertIn("3 neue Fragen gespeichert.", text)
        self.assertIn("kein Hindsight-Aufruf", text)
        self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse((self.out / "hindsight-status.json").exists())

    def test_t2_write_mirrors_released_fields(self):
        code, text = self.run_pipeline()
        self.assertEqual(code, 0, text)
        stored = FileWriter(self.out, self.paths.values()).read_stored()
        bank = self.fake.banks["weitblick"]
        self.assertEqual(set(bank), {e["id"] for e in stored})
        client = HindsightClient(config_from_env(self.env), self.transport)
        for entry in stored:
            reply = client.get_document("weitblick", entry["id"])
            record = json.loads(reply.json()["metadata"]["eintrag"])
            expected = dict(entry)
            expected["fundstelle"] = [
                {"nummer": r["nummer"], "zeile": r["zeile"]} for r in entry["fundstelle"]
            ]
            self.assertEqual(record, expected)
            self.assertEqual(record, bank_record(entry))
            self.assertIn(entry["frage"], reply.json()["original_text"])
        self.assertIn("3 bestaetigt, 0 fehlgeschlagen, 0 unklar", text)

    def test_t2_return_value_meaning_preserved(self):
        # Stage 1 meaning: the CLI count is the number of new JSONL entries.
        code, text = self.run_pipeline()
        self.assertIn("3 neue Fragen gespeichert.", text)
        code, text = self.run_pipeline()
        self.assertIn("0 neue Fragen gespeichert.", text)
        self.assertIn(
            "0 bestaetigt, 0 fehlgeschlagen, 0 unklar, 3 bereits bestaetigt, 0 aufgegeben",
            text,
        )
        self.assertEqual(code, 0)

    def test_t3_daily_limit_twenty_candidates(self):
        prototype = self.entries()[0]
        rows = [dict(prototype, id=f"WB-candidate-{i}") for i in range(20)]
        writer = self.writer()
        self.assertEqual(writer.write(rows), 3)
        self.assertEqual(len(self.fake.retains()), 3)
        self.assertEqual(writer.write(rows), 0)
        self.assertEqual(len(self.fake.retains()), 3)

    def test_t3_total_limit_across_days(self):
        prototype = self.entries()[0]
        writer = self.writer()
        sent = 0
        for day in ("2026-01-02", "2026-01-03", "2026-01-04"):
            rows = [dict(prototype, id=f"WB-{day}-{i}", datum=day) for i in range(4)]
            sent += writer.write(rows)
        self.assertEqual(sent, 5)
        self.assertEqual(len(self.fake.retains()), 5)
        self.assertEqual(len(self.fake.banks["weitblick"]), 5)
        days = [s["datum"] for s in self.status().values()]
        self.assertEqual(days.count("2026-01-02"), 3)
        self.assertEqual(days.count("2026-01-03"), 2)

    def test_t3_repeated_nightly_run(self):
        env = self.nightly_env()
        code, text = self.run_nightly(env)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(self.fake.retains()), 3)
        code, text = self.run_nightly(env)
        self.assertEqual(code, 0, text)
        self.assertEqual(len(self.fake.retains()), 3)
        self.assertIn("3 bereits bestaetigt", text)

    def test_t3_retry_does_not_widen_budget(self):
        prototype = self.entries()[0]
        rows = [dict(prototype, id=f"WB-candidate-{i}") for i in range(20)]
        writer = self.writer()
        self.fake.down = urllib.error.URLError("refused")
        self.assertEqual(writer.write(rows), 0)
        self.assertEqual(len(self.fake.calls), 3)
        self.fake.down = None
        # New candidates must not take the reserved slots of the failed ones.
        self.assertEqual(writer.write(rows[10:] + rows[:3]), 3)
        self.assertEqual(set(self.fake.banks["weitblick"]), {r["id"] for r in rows[:3]})

    def test_t3_retry_stops_after_max_attempts(self):
        entry = self.entries()[0]
        writer = self.writer()
        self.fake.down = urllib.error.URLError("refused")
        for _ in range(MAX_ATTEMPTS):
            self.assertEqual(writer.write([entry]), 0)
        self.assertEqual(len(self.fake.retains()), MAX_ATTEMPTS)
        self.assertEqual(self.status()[entry["id"]]["versuche"], MAX_ATTEMPTS)
        self.assertEqual(writer.last_result[ABANDONED], 1)
        self.assertEqual(writer.write([entry]), 0)
        self.assertEqual(len(self.fake.retains()), MAX_ATTEMPTS)
        self.assertEqual(writer.last_result[ABANDONED], 1)
        self.assertEqual(writer.last_result[UNCLEAR], 0)

    def test_t3_abandoned_entries_fail_the_run(self):
        from weitblick.pipeline import EXIT_ERROR

        self.fake.down = urllib.error.URLError("refused")
        for _ in range(MAX_ATTEMPTS):
            code, text = self.run_pipeline()
            self.assertEqual(code, EXIT_ERROR, text)
        retains = len(self.fake.retains())
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_ERROR, text)
        self.assertEqual(len(self.fake.retains()), retains)
        self.assertIn("aufgegeben", text)
        self.assertIn(f"nach {MAX_ATTEMPTS} Versuchen aufgegeben", text)
        self.assertIn("JSONL ist gesichert", text)
        self.assertNotIn("URLError", text)
        self.assert_no_sensitive_output(text)

    def test_t4_only_retain_without_mode_field(self):
        self.run_pipeline()
        self.assertEqual(len(self.fake.calls), 3)
        for call in self.fake.calls:
            self.assertEqual(call["method"], "POST")
            self.assertEqual(call["url"], URL + "/v1/default/banks/weitblick/memories")
            body = json.loads(call["body"])
            self.assertEqual(set(body), set(RETAIN_TOP_FIELDS))
            self.assertNotIn("mode", body)
            self.assertIs(body["async"], False)
            self.assertEqual(len(body["items"]), 1)
            self.assertEqual(set(body["items"][0]), set(RETAIN_ITEM_FIELDS))
            self.assertNotIn("reflect", call["url"])
            self.assertEqual(call["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(set(retain_body("WB-x", "text", {"k": "v"})), set(RETAIN_TOP_FIELDS))

    def test_t4_mock_rejects_unknown_retain_fields(self):
        # Defence of T4: the fake models the real v1.2.1 field list, not the client.
        payload = {
            "items": [
                {
                    "content": "x",
                    "document_id": "id",
                    "context": "c",
                    "metadata": {},
                }
            ],
            "async": False,
            "mode": "chunks",
        }
        request = urllib.request.Request(
            URL + "/v1/default/banks/weitblick/memories",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self.assertRaisesRegex(AssertionError, "Unknown retain fields"):
            self.fake.open(request)

    def test_t4_unexpected_call_fails_loudly(self):
        self.fake.allowed = {"GET"}  # retain now counts as an unexpected call
        with self.assertRaises(AssertionError):
            self.writer().write(self.entries())

    def test_t5_production_bank_only(self):
        env = dict(self.env, WEITBLICK_HINDSIGHT_BANK="fokus")
        self.run_pipeline(env=env)
        self.assertTrue(self.fake.calls)
        for call in self.fake.calls:
            self.assertEqual(PATH_RE.match(call["url"][len(URL) :]).group(1), "weitblick")
        self.assertEqual(set(self.fake.banks), {"weitblick"})

    def test_t6_disabled_cli_makes_no_call(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = cli_main(["--disabled"], environ=self.env, transport=self.transport)
        self.assertEqual(code, 0)
        self.assertIn("ausgeschaltet", stdout.getvalue())
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.out.exists())

    def test_t6_hindsight_switch_keeps_jsonl(self):
        switched_off = dict(self.env, WEITBLICK_HINDSIGHT_ENABLED="0")
        for env, flag in ((self.env, True), (switched_off, False)):
            with self.subTest(flag=flag):
                code, text = self.run_pipeline(env=env, no_hindsight=flag)
                self.assertEqual(code, 0)
                self.assertIn("Hindsight ausgeschaltet; kein Hindsight-Aufruf.", text)
                self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)
                self.assertEqual(self.fake.calls, [])

    def test_t6_nightly_disabled_reads_and_writes_nothing(self):
        code, text = self.run_nightly(self.nightly_env(WEITBLICK_DISABLED="1"))
        self.assertEqual(code, 0)
        self.assertIn("ausgeschaltet", text)
        self.assertEqual(self.fake.calls, [])
        self.assertFalse(self.out.exists())

    def test_t7_unreachable_keeps_jsonl_and_exits_1(self):
        code, _ = self.run_pipeline(env={})
        before = self.jsonl_bytes()
        self.fake.down = urllib.error.URLError("refused " + URL)
        code, text = self.run_nightly(self.nightly_env())
        self.assertEqual(code, 1)
        self.assertIn("JSONL ist gesichert", text)
        self.assertIn("0 bestaetigt, 0 fehlgeschlagen, 3 unklar", text)
        self.assert_no_sensitive_output(text)
        self.assertEqual(self.jsonl_bytes(), before)
        self.assertEqual({s["status"] for s in self.status().values()}, {UNCLEAR})
        self.fake.down = None
        code, text = self.run_nightly(self.nightly_env())
        self.assertEqual(code, 0, text)
        self.assertEqual(self.jsonl_bytes(), before)
        self.assertEqual({s["status"] for s in self.status().values()}, {CONFIRMED})

    def test_t7_timeout_is_unclear(self):
        self.fake.down = TimeoutError("timed out")
        writer = self.writer()
        self.assertEqual(writer.write(self.entries()), 0)
        self.assertEqual(writer.last_result[UNCLEAR], 3)
        self.assertEqual(writer.last_result[CONFIRMED], 0)
        self.assertEqual({s["status"] for s in self.status().values()}, {UNCLEAR})

    def test_t7_response_classification_and_retry(self):
        cases = [
            ((500, b'{"detail": "boom"}'), UNCLEAR),
            ((429, b"{}"), UNCLEAR),
            ((400, b'{"detail": "bad"}'), FAILED),
            ((200, b"not json"), UNCLEAR),
            ((200, b'{"success": false}'), UNCLEAR),
        ]
        for override, expected in cases:
            with self.subTest(status=override[0], body=override[1]):
                self.fake = FakeHindsight()
                self.transport = UrllibTransport(opener=self.fake)
                self.out = self.root / f"out-{override[0]}-{len(override[1])}"
                self.fake.status_override = override
                writer = self.writer()
                self.assertEqual(writer.write(self.entries()), 0)
                self.assertEqual(writer.last_result[expected], 3)
                self.fake.status_override = None
                self.assertEqual(writer.write(self.entries()), 3)
                self.assertEqual(writer.write(self.entries()), 0)
                self.assertEqual(len(self.fake.retains()), 6)

    def test_t7_hindsight_failure_never_touches_jsonl(self):
        FileWriter(self.out, self.paths.values()).write(self.entries())
        digest = hashlib.sha256(self.jsonl_bytes()).hexdigest()
        self.fake.status_override = (500, b"{}")
        self.writer().write(FileWriter(self.out, self.paths.values()).read_stored())
        self.assertEqual(hashlib.sha256(self.jsonl_bytes()).hexdigest(), digest)

    def test_t7_corrupt_status_file_is_preserved(self):
        self.out.mkdir()
        (self.out / "hindsight-status.json").write_text("{broken")
        with self.assertRaises(ValueError):
            self.writer().write(self.entries())
        self.assertEqual((self.out / "hindsight-status.json").read_text(), "{broken")
        self.assertEqual(self.fake.calls, [])

    def test_t7_secondary_writer_exception_keeps_jsonl(self):
        from weitblick import pipeline as pipeline_mod
        from weitblick.pipeline import EXIT_ERROR

        real_hw = pipeline_mod.HindsightWriter

        class Exploding(real_hw):
            def write(self, entries):
                raise ValueError("unexpected secondary failure")

        with patch.object(pipeline_mod, "HindsightWriter", Exploding):
            code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("neue Fragen gespeichert", text)
        self.assertIn("JSONL ist gesichert", text)
        self.assertNotIn("unexpected secondary failure", text)
        self.assert_no_sensitive_output(text)
        self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)

    def test_t7_auth_error_aborts_without_counting_attempt(self):
        from weitblick.pipeline import EXIT_AUTH

        for status in (401, 403):
            with self.subTest(status=status):
                self.fake = FakeHindsight()
                self.transport = UrllibTransport(opener=self.fake)
                self.out = self.root / f"out-auth-{status}"
                self.fake.status_override = (status, b'{"detail": "auth-secret"}')
                writer = self.writer()
                with self.assertRaises(HindsightAuthError):
                    writer.write(self.entries())
                self.assertEqual(len(self.fake.retains()), 1)
                status_path = self.out / "hindsight-status.json"
                if status_path.exists():
                    self.assertEqual(self.status(), {})
                code, text = self.run_pipeline()
                self.assertEqual(code, EXIT_AUTH, text)
                self.assertIn("Hindsight-Anmeldung abgelehnt", text)
                self.assertIn("JSONL ist gesichert", text)
                self.assertIn("unveraendert", text)
                self.assertNotIn("auth-secret", text)
                self.assertNotIn("Unauthorized", text)
                self.assert_no_sensitive_output(text)
                self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)
                if status_path.exists():
                    self.assertEqual(self.status(), {})
                self.assertEqual(len(self.fake.retains()), 2)

    def test_t7_auth_error_does_not_occupy_budget(self):
        from weitblick.pipeline import EXIT_AUTH, EXIT_OK

        self.fake.status_override = (401, b"{}")
        for _ in range(MAX_ATTEMPTS + 1):
            code, text = self.run_pipeline()
            self.assertEqual(code, EXIT_AUTH, text)
            self.assertNotIn("aufgegeben", text)
        status_path = self.out / "hindsight-status.json"
        if status_path.exists():
            self.assertEqual(self.status(), {})
        self.fake.status_override = None
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.assertIn("3 bestaetigt", text)
        self.assertEqual(len(self.status()), 3)
        self.assertEqual({s["status"] for s in self.status().values()}, {CONFIRMED})

    def test_t7_auth_error_leaves_existing_status_unchanged(self):
        from weitblick.pipeline import EXIT_AUTH

        self.fake.status_override = (500, b"{}")
        writer = self.writer()
        self.assertEqual(writer.write(self.entries()), 0)
        before = json.loads((self.out / "hindsight-status.json").read_text())
        self.assertEqual({s["versuche"] for s in before["eintraege"].values()}, {1})
        self.fake.status_override = (403, b'{"detail": "forbidden"}')
        with self.assertRaises(HindsightAuthError):
            writer.write(self.entries())
        after = json.loads((self.out / "hindsight-status.json").read_text())
        self.assertEqual(after, before)
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_AUTH, text)
        self.assertEqual(
            json.loads((self.out / "hindsight-status.json").read_text()),
            before,
        )
        self.assertNotIn("forbidden", text)
        self.assert_no_sensitive_output(text)

    def test_fake_rejects_missing_authorization(self):
        paths = (
            ("POST", "/v1/default/banks/weitblick/memories", b'{"items":[],"async":false}'),
            ("GET", "/v1/default/banks/weitblick/documents/WB-x", None),
            ("DELETE", "/v1/default/banks/weitblick/documents/WB-x", None),
            ("GET", "/v1/default/chunks/weitblick_WB-x_0", None),
            ("POST", "/v1/default/banks/weitblick/memories/recall", b'{"query":"x"}'),
        )
        for method, path, data in paths:
            with self.subTest(method=method, path=path):
                headers = {"Content-Type": "application/json"} if data is not None else {}
                request = urllib.request.Request(
                    URL + path,
                    data=data,
                    headers=headers,
                    method=method,
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.fake.open(request)
                self.assertEqual(caught.exception.code, 401)

    def test_t7_auth_error_mid_run_keeps_sent_and_frees_unsent(self):
        from weitblick.pipeline import EXIT_AUTH, EXIT_OK

        cases = (
            (None, CONFIRMED),
            ((500, b'{"detail": "boom"}'), UNCLEAR),
        )
        for first, kept in cases:
            for auth_status in (401, 403):
                with self.subTest(first=first, auth=auth_status):
                    self.fake = FakeHindsight()
                    self.transport = UrllibTransport(opener=self.fake)
                    label = "ok" if first is None else str(first[0])
                    self.out = self.root / f"out-mid-{label}-{auth_status}"
                    entries = self.entries()
                    self.assertEqual(len(entries), 3)
                    self.fake.retain_queue = [
                        first,
                        (auth_status, b'{"detail": "auth-secret"}'),
                    ]
                    writer = self.writer()
                    with self.assertRaises(HindsightAuthError):
                        writer.write(entries)
                    ids = [entry["id"] for entry in entries]
                    status = self.status()
                    self.assertEqual(set(status), {ids[0]})
                    self.assertEqual(status[ids[0]]["status"], kept)
                    self.assertEqual(status[ids[0]]["versuche"], 1)
                    self.assertNotIn(ids[1], status)
                    self.assertNotIn(ids[2], status)
                    # Pipeline run must see 401 immediately, otherwise a later
                    # fresh entry would be sent before the auth error.
                    self.fake.retain_queue = [(auth_status, b'{"detail": "auth-secret"}')]
                    code, text = self.run_pipeline()
                    self.assertEqual(code, EXIT_AUTH, text)
                    self.assertIn("Hindsight-Anmeldung abgelehnt", text)
                    self.assertIn("JSONL ist gesichert", text)
                    self.assertNotIn("auth-secret", text)
                    self.assertNotIn("boom", text)
                    self.assert_no_sensitive_output(text)
                    self.assertEqual(set(self.status()), {ids[0]})
                    self.assertEqual(self.status()[ids[0]]["status"], kept)
                    self.assertEqual(self.status()[ids[0]]["versuche"], 1)
                    self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)
                    self.fake.retain_queue = None
                    code, text = self.run_pipeline()
                    self.assertEqual(code, EXIT_OK, text)
                    after = self.status()
                    self.assertEqual(set(after), set(ids))
                    self.assertEqual({s["status"] for s in after.values()}, {CONFIRMED})

    def test_t7_non_auth_errors_still_count_as_attempts(self):
        from weitblick.pipeline import EXIT_AUTH, EXIT_ERROR

        cases = [
            ((400, b'{"detail": "bad-secret"}'), FAILED),
            ((404, b'{"detail": "missing"}'), FAILED),
            ((409, b'{"detail": "conflict"}'), FAILED),
            ((500, b'{"detail": "boom"}'), UNCLEAR),
            ((503, b'{"detail": "busy"}'), UNCLEAR),
        ]
        for override, expected in cases:
            with self.subTest(status=override[0]):
                self.fake = FakeHindsight()
                self.transport = UrllibTransport(opener=self.fake)
                self.out = self.root / f"out-count-{override[0]}"
                self.fake.status_override = override
                code, text = self.run_pipeline()
                self.assertEqual(code, EXIT_ERROR, text)
                self.assertNotEqual(code, EXIT_AUTH)
                self.assertNotIn("Anmeldung abgelehnt", text)
                self.assertNotIn("bad-secret", text)
                self.assertNotIn("missing", text)
                self.assertNotIn("conflict", text)
                self.assertNotIn("boom", text)
                self.assertNotIn("busy", text)
                self.assert_no_sensitive_output(text)
                records = self.status()
                self.assertEqual(len(records), 3)
                self.assertEqual({s["status"] for s in records.values()}, {expected})
                self.assertEqual({s["versuche"] for s in records.values()}, {1})

        self.fake = FakeHindsight()
        self.transport = UrllibTransport(opener=self.fake)
        self.out = self.root / "out-count-timeout"
        self.fake.down = TimeoutError("timed out")
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_ERROR, text)
        self.assertNotEqual(code, EXIT_AUTH)
        self.assertNotIn("timed out", text)
        self.assert_no_sensitive_output(text)
        records = self.status()
        self.assertEqual({s["status"] for s in records.values()}, {UNCLEAR})
        self.assertEqual({s["versuche"] for s in records.values()}, {1})

    def test_exit_codes_are_stable_and_distinct(self):
        from weitblick.pipeline import EXIT_AUTH, EXIT_CONFIG, EXIT_ERROR, EXIT_OK

        self.assertEqual((EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_AUTH), (0, 1, 2, 3))
        self.assertEqual(exitcodes.EXIT_OPEN, 4)
        codes = {EXIT_OK, EXIT_ERROR, EXIT_CONFIG, EXIT_AUTH, exitcodes.EXIT_OPEN}
        self.assertEqual(len(codes), 5)
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.fake.status_override = (500, b"{}")
        self.out = self.root / "out-exit-error"
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_ERROR, text)
        self.fake.status_override = (401, b'{"detail": "auth-secret"}')
        self.out = self.root / "out-exit-auth"
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_AUTH, text)
        self.assertNotIn("auth-secret", text)
        self.assert_no_sensitive_output(text)
        code, text = self.run_pipeline(env={"WEITBLICK_HINDSIGHT_ENABLED": "1"})
        self.assertEqual(code, EXIT_CONFIG, text)

    def test_t3_reset_abandoned_entry_frees_budget(self):
        from weitblick.pipeline import EXIT_ERROR, EXIT_OK

        self.fake.down = urllib.error.URLError("refused")
        for _ in range(MAX_ATTEMPTS):
            code, text = self.run_pipeline()
            self.assertEqual(code, EXIT_ERROR, text)
        abandoned = self.status()
        self.assertEqual(len(abandoned), 3)
        self.assertTrue(all(s["versuche"] >= MAX_ATTEMPTS for s in abandoned.values()))
        self.fake.down = None
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_ERROR, text)
        self.assertIn("aufgegeben", text)
        self.assertEqual(set(self.status()), set(abandoned))
        # README reset path: drop abandoned keys in hindsight-status.json, keep JSONL.
        path = self.out / "hindsight-status.json"
        data = json.loads(path.read_text())
        for key in list(data["eintraege"]):
            data["eintraege"].pop(key)
        path.write_text(json.dumps(data, ensure_ascii=True, indent=1, sort_keys=True) + "\n")
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.assertIn("3 bestaetigt", text)
        self.assertEqual({s["status"] for s in self.status().values()}, {CONFIRMED})
        self.assertEqual(set(self.status()), set(abandoned))

    def test_t8_rebuild_from_jsonl_after_status_loss(self):
        # Provisional: only the offline part of T8. The backup restore drill is server-side.
        self.run_pipeline()
        (self.out / "hindsight-status.json").unlink()
        self.fake.banks.clear()
        code, text = self.run_pipeline()
        self.assertEqual(code, 0, text)
        stored = FileWriter(self.out, self.paths.values()).read_stored()
        self.assertEqual(set(self.fake.banks["weitblick"]), {e["id"] for e in stored})
        self.assertEqual(len(self.fake.retains()), 6)


class PrivacyTests(Stage2Base):
    def test_privacy_private_path_absent_from_full_request(self):
        self.run_pipeline()
        self.assertEqual(len(self.fake.calls), 3)
        private = str(self.src.resolve())
        variants = {
            private,
            PRIVATE_DIR,
            json.dumps(private)[1:-1],
            urllib.request.quote(private, safe=""),
        }
        for call in self.fake.calls:
            wire = "\n".join(
                [call["method"], call["url"], json.dumps(call["headers"]), call["body"].decode()]
            )
            for value in variants:
                self.assertNotIn(value, wire)
            for item in json.loads(call["body"])["items"]:
                record = json.loads(item["metadata"]["eintrag"])
                for ref in record["fundstelle"]:
                    self.assertEqual(set(ref), {"nummer", "zeile"})

    def test_privacy_guard_blocks_path_in_released_field(self):
        entry = dict(self.entries()[0])
        entry["nicht_gewaehlte_option"] = "siehe " + entry["fundstelle"][0]["datei"]
        writer = self.writer()
        self.assertEqual(writer.write([entry]), 0)
        self.assertEqual(writer.last_result[FAILED], 1)
        self.assertEqual(self.fake.calls, [])


class ConfigTests(Stage2Base):
    def test_f3_missing_values_named_without_values(self):
        cases = [
            (
                {"WEITBLICK_HINDSIGHT_ENABLED": "1"},
                ["WEITBLICK_HINDSIGHT_URL", "WEITBLICK_HINDSIGHT_TOKEN"],
            ),
            (dict(self.env, WEITBLICK_HINDSIGHT_TOKEN=""), ["WEITBLICK_HINDSIGHT_TOKEN"]),
            (dict(self.env, WEITBLICK_HINDSIGHT_URL="  "), ["WEITBLICK_HINDSIGHT_URL"]),
        ]
        for env, names in cases:
            with self.subTest(names=names):
                code, text = self.run_pipeline(env=env)
                self.assertEqual(code, 2)
                for name in names:
                    self.assertIn(name, text)
                self.assert_no_sensitive_output(text)
                self.assertFalse(self.out.exists())
                self.assertEqual(self.fake.calls, [])

    def test_f3_invalid_values_rejected_without_echo(self):
        secret_url = "https://user:" + "pw" + "@hindsight.test.invalid"
        for env in (
            dict(self.env, WEITBLICK_HINDSIGHT_URL=secret_url),
            dict(self.env, WEITBLICK_HINDSIGHT_URL="ftp://hindsight.test.invalid"),
            dict(self.env, WEITBLICK_HINDSIGHT_ENABLED="vielleicht"),
            dict(self.env, WEITBLICK_HINDSIGHT_TIMEOUT_S="abc"),
            dict(self.env, WEITBLICK_HINDSIGHT_TOKEN="two words"),
        ):
            with self.subTest(env=sorted(env)):
                code, text = self.run_pipeline(env=env)
                self.assertEqual(code, 2)
                self.assertNotIn("pw@", text)
                self.assertNotIn("two words", text)
                self.assert_no_sensitive_output(text)
                self.assertEqual(self.fake.calls, [])

    def test_f3_nightly_missing_paths(self):
        env = self.nightly_env(WEITBLICK_OUTPUT="", WEITBLICK_SERVER="")
        code, text = self.run_nightly(env)
        self.assertEqual(code, 2)
        self.assertIn("WEITBLICK_SERVER", text)
        self.assertIn("WEITBLICK_OUTPUT", text)
        self.assert_no_sensitive_output(text)

    def test_f3_config_repr_hides_values(self):
        config = config_from_env(self.env)
        self.assertNotIn(TOKEN, repr(config))
        self.assertNotIn("hindsight.test.invalid", repr(config))
        self.assertIsNone(config_from_env({}))

    def test_f3_transport_error_hides_target(self):
        class Broken:
            def open(self, request, timeout=None):
                raise urllib.error.URLError("cannot reach " + URL)

        with self.assertRaises(TransportError) as caught:
            UrllibTransport(opener=Broken()).request("GET", URL + "/x", {})
        self.assertNotIn("hindsight.test.invalid", str(caught.exception))

    def test_f3_default_opener_has_no_proxy_and_no_redirect(self):
        def env_proxies(candidate):
            return [
                h
                for h in candidate.handlers
                if isinstance(h, urllib.request.ProxyHandler) and h.proxies
            ]

        with patch.dict("os.environ", {"http_proxy": "http://proxy.test.invalid:1"}):
            # Control: the stock opener would pick up the environment proxy.
            self.assertTrue(env_proxies(urllib.request.build_opener()))
            opener = default_opener()
        self.assertEqual(env_proxies(opener), [])
        redirect = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertTrue(redirect)
        request = urllib.request.Request(URL + "/a", headers={"Authorization": "Bearer x"})
        for handler in redirect:
            self.assertIsNone(handler.redirect_request(request, None, 302, "", {}, "http://other"))


class SK80Tests(Stage2Base):
    def client(self):
        return HindsightClient(HindsightConfig(URL, TOKEN), self.transport)

    def test_sk80_separated_banks_pass(self):
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0001")
        self.assertEqual(report["sk80"], sk80.PASSED, report)
        self.assertEqual(report["t5"], sk80.PASSED, report)
        deletes = [c for c in self.fake.calls if c["method"] == "DELETE"]
        self.assertEqual(len(deletes), 1)
        self.assertIn("/banks/" + SK80_BANK_A + "/documents/sk80-x-t0001", deletes[0]["url"])
        self.assertIn("sk80-x-t0001", self.fake.banks[SK80_BANK_B])
        self.assertNotIn("/banks/weitblick/", "".join(c["url"] for c in self.fake.calls))
        gets_a = [
            c["url"] for c in self.fake.calls if c["method"] == "GET" and "-sk80-a/" in c["url"]
        ]
        self.assertEqual(len(gets_a), 1)
        self.assertTrue(gets_a[0].endswith("/banks/" + SK80_BANK_A + "/documents/sk80-x-t0001"))
        chunk_urls = [c["url"] for c in self.fake.calls if "/chunks/" in c["url"]]
        self.assertEqual(len(chunk_urls), 2)
        self.assertEqual(report["chunk"], sk80.OPEN)
        steps = report["schritte"]
        self.assertTrue(any("Lesen Chunk X aus B: HTTP 200" in s for s in steps))
        self.assertTrue(
            any("Lesen Chunk X unter Bank A" in s and "bleibt offen" in s for s in steps)
        )
        self.assertFalse(any("Chunk-Pfad belegt" in s or "Trennung belegt" in s for s in steps))

    def test_sk80_cross_bank_read_fails(self):
        self.fake.leak_get = True
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0002")
        self.assertEqual(report["sk80"], sk80.FAILED)

    def test_sk80_cross_bank_chunk_read_fails(self):
        self.fake.leak_chunk = True
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0009")
        self.assertEqual(report["sk80"], sk80.FAILED)
        self.assertEqual(report["chunk"], sk80.FAILED)
        leaked = any(
            "Lesen Chunk X unter Bank A" in s and "Erfolg" in s for s in report["schritte"]
        )
        self.assertTrue(leaked)

    def test_sk80_cross_bank_delete_fails(self):
        self.fake.leak_delete = True
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0003")
        self.assertEqual(report["sk80"], sk80.FAILED)
        self.assertTrue(any("X fehlt oder veraendert" in s for s in report["schritte"]))

    def test_sk80_empty_success_is_not_a_pass(self):
        self.fake.empty_success_get = True
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0004")
        self.assertEqual(report["sk80"], sk80.FAILED)

    def test_sk80_chunk_empty_success_is_failed(self):
        self.fake.empty_success_chunk = True
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0010")
        self.assertEqual(report["chunk"], sk80.FAILED)
        self.assertEqual(report["sk80"], sk80.FAILED)
        self.assertTrue(
            any(
                "Lesen Chunk X unter Bank A" in s and "Erfolg statt Fehler" in s
                for s in report["schritte"]
            )
        )

    def test_sk80_connection_error_is_open(self):
        self.fake.down = urllib.error.URLError("refused")
        report = sk80.run_sk80(self.client(), SK80_BANK_A, SK80_BANK_B, "t0005")
        self.assertEqual(report["sk80"], sk80.OPEN)

    def test_sk80_general_auth_error_is_open(self):
        # Y in A would already fail; so seed first, then deny A.
        client = self.client()
        original = self.fake.open

        def deny_a_after_seed(request, timeout=None):
            if request.get_method() in {"GET", "DELETE"}:
                self.fake.auth_fail_bank = SK80_BANK_A
            return original(request, timeout)

        self.fake.open = deny_a_after_seed
        report = sk80.run_sk80(client, SK80_BANK_A, SK80_BANK_B, "t0006")
        self.assertEqual(report["sk80"], sk80.OPEN)
        self.assertTrue(any("401" in s for s in report["schritte"]))

    def test_sk80_refuses_production_and_identical_banks(self):
        cases = (
            ("weitblick", SK80_BANK_B),
            (SK80_BANK_A, SK80_BANK_A),
            ("fokus", SK80_BANK_B),
        )
        for a, b in cases:
            with self.subTest(a=a, b=b), self.assertRaises(ValueError):
                sk80.run_sk80(self.client(), a, b, "t0007")
        self.assertEqual(self.fake.calls, [])

    def test_sk80_cli_output_hides_url_and_token(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        env = {"WEITBLICK_HINDSIGHT_URL": URL, "WEITBLICK_HINDSIGHT_TOKEN": TOKEN}
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sk80.main(
                ["--bank-a", SK80_BANK_A, "--bank-b", SK80_BANK_B, "--tag", "t0008"],
                environ=env,
                transport=self.transport,
            )
        text = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, 0, text)
        self.assertIn("S-K80: bestanden", text)
        self.assertIn("T5: bestanden", text)
        self.assertIn("Chunk: offen", text)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("hindsight.test.invalid", text)

    def test_sk80_cli_missing_config(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = sk80.main(
                ["--bank-a", SK80_BANK_A, "--bank-b", SK80_BANK_B],
                environ={},
            )
        self.assertEqual(code, 2)
        self.assertIn("WEITBLICK_HINDSIGHT_URL", stderr.getvalue())
        self.assertEqual(self.fake.calls, [])


class Killed(BaseException):
    """Stands in for SIGKILL: nothing after it runs, no cleanup code."""


class HaertungTests(Stage2Base):
    """Folge zu PR #98: exit codes, rollback errors, crash recovery, token format."""

    def write_status(self, records):
        self.out.mkdir(parents=True, exist_ok=True)
        payload = {"bank": "weitblick", "version": 1, "eintraege": records}
        (self.out / "hindsight-status.json").write_text(json.dumps(payload))

    # 1. Exit codes

    def test_exit_codes_shared_table_is_unique(self):
        from weitblick import pipeline

        table = (
            exitcodes.EXIT_OK,
            exitcodes.EXIT_ERROR,
            exitcodes.EXIT_CONFIG,
            exitcodes.EXIT_AUTH,
            exitcodes.EXIT_OPEN,
        )
        self.assertEqual(table, (0, 1, 2, 3, 4))
        self.assertEqual(set(exitcodes.EXIT_CODES), set(table))
        self.assertEqual(len(set(exitcodes.EXIT_CODES.values())), len(table))
        self.assertEqual(pipeline.EXIT_AUTH, exitcodes.EXIT_AUTH)
        self.assertEqual(pipeline.EXIT_CONFIG, exitcodes.EXIT_CONFIG)

    def sk80_cli(self, tag):
        stdout, stderr = io.StringIO(), io.StringIO()
        env = {"WEITBLICK_HINDSIGHT_URL": URL, "WEITBLICK_HINDSIGHT_TOKEN": TOKEN}
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sk80.main(
                ["--bank-a", SK80_BANK_A, "--bank-b", SK80_BANK_B, "--tag", tag],
                environ=env,
                transport=self.transport,
            )
        return code, stdout.getvalue() + stderr.getvalue()

    def test_sk80_open_has_own_exit_code_not_auth(self):
        self.fake.down = urllib.error.URLError("refused")
        code, text = self.sk80_cli("t0101")
        self.assertIn("S-K80: offen", text)
        self.assertEqual(code, exitcodes.EXIT_OPEN)
        self.assertNotEqual(code, exitcodes.EXIT_AUTH)
        self.assert_no_sensitive_output(text)

    def test_sk80_exit_codes_per_verdict(self):
        code, text = self.sk80_cli("t0102")
        self.assertEqual(code, exitcodes.EXIT_OK, text)
        self.fake = FakeHindsight()
        self.transport = UrllibTransport(opener=self.fake)
        self.fake.leak_get = True
        code, text = self.sk80_cli("t0103")
        self.assertIn("S-K80: nicht bestanden", text)
        self.assertEqual(code, exitcodes.EXIT_ERROR, text)

    # 2. Rollback error must not hide 401/403

    def fail_rollback_save(self):
        original = HindsightWriter._save_status
        fake = self.fake

        def save(writer, directory, status):
            # The queue is empty only after the auth reply was consumed.
            if fake.retain_queue == []:
                raise PermissionError(13, "denied", str(self.root / PRIVATE_DIR))
            return original(writer, directory, status)

        return patch.object(HindsightWriter, "_save_status", save)

    def test_t7_rollback_oserror_keeps_auth_error(self):
        for auth_status in (401, 403):
            with self.subTest(auth=auth_status):
                self.fake = FakeHindsight()
                self.transport = UrllibTransport(opener=self.fake)
                self.out = self.root / f"out-rb-{auth_status}"
                self.fake.retain_queue = [None, (auth_status, b'{"detail": "auth-secret"}')]
                with self.fail_rollback_save(), self.assertRaises(HindsightAuthError) as caught:
                    self.writer().write(self.entries())
                error = caught.exception
                self.assertTrue(error.rollback_failed)
                notes = " ".join(getattr(error, "__notes__", []))
                self.assertIn("PermissionError", notes)
                self.assertNotIn(PRIVATE_DIR, notes)
                self.assertNotIn(str(self.root), notes)

    def test_t7_rollback_oserror_pipeline_exits_auth_and_recovers(self):
        from weitblick.pipeline import EXIT_AUTH, EXIT_OK

        entries = self.entries()
        ids = [entry["id"] for entry in entries]
        self.fake.retain_queue = [None, (401, b'{"detail": "auth-secret"}')]
        with self.fail_rollback_save():
            code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_AUTH, text)
        self.assertIn("Hindsight-Anmeldung abgelehnt", text)
        self.assertIn("Reservierungen konnten nicht zurueckgegeben werden", text)
        self.assertNotIn("auth-secret", text)
        self.assertNotIn("denied", text)
        self.assert_no_sensitive_output(text)
        self.assertEqual(len(self.jsonl_bytes().splitlines()), 3)
        left = self.status()
        self.assertEqual(left[ids[0]]["status"], CONFIRMED)
        self.assertEqual(left[ids[1]]["status"], RESERVED)
        self.assertIn(SENDING, left[ids[1]])
        self.assertEqual(left[ids[2]]["status"], RESERVED)
        self.fake.retain_queue = None
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.assertIn("1 Reservierungen zurueckgegeben, 1 erneut offen", text)
        after = self.status()
        self.assertEqual({s["status"] for s in after.values()}, {CONFIRMED})
        self.assertEqual({s["versuche"] for s in after.values()}, {1})

    # 3. Crash between reservation and send

    def test_t3_killed_run_costs_no_attempt(self):
        from weitblick.pipeline import EXIT_OK

        entries = self.entries()
        ids = [entry["id"] for entry in entries]
        writer = self.writer()
        with patch.object(HindsightWriter, "_send", side_effect=Killed), self.assertRaises(Killed):
            writer.write(entries)
        self.assertEqual(self.fake.retains(), [])
        killed = self.status()
        self.assertEqual({s["status"] for s in killed.values()}, {RESERVED})
        self.assertEqual({s["versuche"] for s in killed.values()}, {0})
        self.assertEqual(len({s["lauf"] for s in killed.values()}), 1)
        self.assertIn(SENDING, killed[ids[0]])
        self.assertNotIn(SENDING, killed[ids[1]])
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.assertIn("abgebrochener Lauf erkannt", text)
        self.assertIn("2 Reservierungen zurueckgegeben, 1 erneut offen", text)
        self.assertNotIn("aufgegeben.", text.replace("0 aufgegeben.", ""))
        after = self.status()
        self.assertEqual(set(after), set(ids))
        self.assertEqual({s["status"] for s in after.values()}, {CONFIRMED})
        self.assertEqual({s["versuche"] for s in after.values()}, {1})
        for record in after.values():
            self.assertNotIn(SENDING, record)
        # Same document IDs, each sent once: no duplicate in the bank.
        self.assertEqual(sorted(self.fake.banks["weitblick"]), sorted(ids))
        self.assertEqual(len(self.fake.retains()), 3)

    def test_t3_killed_retry_keeps_last_attempt(self):
        from weitblick.pipeline import EXIT_OK

        entries = self.entries()
        ids = [entry["id"] for entry in entries]
        day = entries[0]["datum"]
        records = {
            ids[0]: {
                "datum": day,
                "status": FAILED,
                "versuche": MAX_ATTEMPTS - 1,
                SENDING: "abc",
            },
            # Stufe 2 format before this change: reservation without versuche.
            ids[1]: {"datum": day, "status": UNCLEAR},
            ids[2]: {"datum": day, "status": RESERVED, "versuche": 0, "lauf": "abc"},
        }
        self.write_status(records)
        writer = self.writer()
        self.assertEqual(writer.write(entries), 3)
        self.assertEqual(writer.last_result[RETURNED], 1)
        self.assertEqual(writer.last_result[RESUMED], 2)
        self.assertEqual(writer.last_result[ABANDONED], 0)
        after = self.status()
        self.assertEqual(after[ids[0]]["versuche"], MAX_ATTEMPTS)
        self.assertEqual(after[ids[1]]["versuche"], 1)
        self.assertEqual(after[ids[2]]["versuche"], 1)
        self.assertEqual({s["status"] for s in after.values()}, {CONFIRMED})
        code, text = self.run_pipeline()
        self.assertEqual(code, EXIT_OK, text)
        self.assertNotIn("abgebrochener Lauf", text)

    def test_t3_recovery_keeps_budget(self):
        rows = []
        for day in range(1, 5):
            rows += self.entries(f"2026-01-0{day}")
        writer = self.writer()
        with patch.object(HindsightWriter, "_send", side_effect=Killed), self.assertRaises(Killed):
            writer.write(rows)
        for _ in range(3):
            self.writer().write(rows)
        self.assertLessEqual(len(self.status()), 5)
        self.assertLessEqual(len(self.fake.banks.get("weitblick", {})), 5)

    def test_status_file_rejects_bad_crash_fields(self):
        day = "2026-01-02"
        for record in (
            {"datum": day, "status": RESERVED, "versuche": -1},
            {"datum": day, "status": RESERVED, "versuche": 0, SENDING: 7},
            {"datum": day, "status": "irgendwas", "versuche": 0},
        ):
            with self.subTest(record=record):
                self.write_status({"WB-x": record})
                with self.assertRaises(ValueError):
                    self.writer().write(self.entries())

    # 4. Token format, never echoed

    BAD_TOKENS = (
        "Bearer secretpart-abc123",
        "bearer\tsecretpart-abc123",
        "secret part-abc123",
        "secretpart\nabc123",
        ".secretpart-abc123",
        "secretpart-abc123.",
        "secretpart..abc123",
        "secretpart.abc123.=",
        "secretpart:abc123",
        "secretpart@abc123",
        "secretpart\u00e4abc123",
        "=secretpart",
    )

    def test_token_format_rejected_with_config_exit(self):
        from weitblick.pipeline import EXIT_CONFIG

        for token in self.BAD_TOKENS:
            with self.subTest(index=self.BAD_TOKENS.index(token)):
                env = dict(self.env, WEITBLICK_HINDSIGHT_TOKEN=token)
                with self.assertRaises(HindsightConfigError) as caught:
                    config_from_env(env)
                message = str(caught.exception)
                self.assertIn("WEITBLICK_HINDSIGHT_TOKEN", message)
                self.assertIsNone(caught.exception.__cause__)
                code, text = self.run_pipeline(env=env)
                self.assertEqual(code, EXIT_CONFIG, text)
                for part in ("secretpart", "abc123", token.strip()):
                    self.assertNotIn(part, message)
                    self.assertNotIn(part, text)
                self.assertEqual(self.fake.calls, [])

    def test_token_bearer_prefix_has_clear_message(self):
        env = dict(self.env, WEITBLICK_HINDSIGHT_TOKEN="Bearer " + TOKEN)
        with self.assertRaises(HindsightConfigError) as caught:
            config_from_env(env)
        self.assertIn("ohne Praefix", str(caught.exception))
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_token_valid_forms_accepted(self):
        for token in (TOKEN, "  " + TOKEN + "\n", "aaa.bbb.ccc", "abc+/~_-.x==", "x"):
            with self.subTest(index=len(token)):
                config = config_from_env(dict(self.env, WEITBLICK_HINDSIGHT_TOKEN=token))
                self.assertEqual(config.token, token.strip())
                self.assertNotIn(token.strip(), repr(config))
                self.assertNotIn(token.strip(), str(config))

    def test_token_never_in_cli_or_nightly_output(self):
        secret = "secretpart.abc123..tail"
        env = self.nightly_env(WEITBLICK_HINDSIGHT_TOKEN=secret)
        code, text = self.run_nightly(env)
        self.assertEqual(code, exitcodes.EXIT_CONFIG, text)
        for part in ("secretpart", "abc123", "tail"):
            self.assertNotIn(part, text)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = sk80.main(
                ["--bank-a", SK80_BANK_A, "--bank-b", SK80_BANK_B],
                environ={"WEITBLICK_HINDSIGHT_URL": URL, "WEITBLICK_HINDSIGHT_TOKEN": secret},
            )
        text = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(code, exitcodes.EXIT_CONFIG, text)
        for part in ("secretpart", "abc123", "tail"):
            self.assertNotIn(part, text)
        self.assertEqual(self.fake.calls, [])


def synthetic_openapi(version="1.2.1", with_mode=False, mode_enum=("chunks", "verbose")):
    """Hand-written OpenAPI fragment aligned with Hindsight v1.2.1 RetainRequest."""
    retain_props = {
        "items": {"type": "array", "items": {"$ref": "#/components/schemas/Item"}},
        "async": {"type": "boolean"},
    }
    if with_mode:
        retain_props["mode"] = {"type": "string", "enum": list(mode_enum)}
    doc_op = {
        "parameters": [
            {"name": "bank_id", "in": "path", "schema": {"type": "string"}},
            {"name": "document_id", "in": "path", "schema": {"type": "string"}},
        ],
        "responses": {"200": {}, "404": {}},
    }
    return {
        "info": {"version": version},
        "paths": {
            "/v1/default/banks/{bank_id}/memories": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Retain"},
                            },
                        },
                    },
                },
            },
            "/v1/default/banks/{bank_id}/documents/{document_id}": {
                "get": doc_op,
                "delete": doc_op,
            },
            "/v1/default/banks/{bank_id}/memories/recall": {"post": {}},
            "/v1/default/chunks/{chunk_id}": {"get": {}},
        },
        "components": {
            "schemas": {
                "Retain": {"type": "object", "properties": retain_props},
                "Item": {
                    "allOf": [
                        {
                            "properties": {
                                "content": {"type": "string"},
                                "document_id": {"type": "string"},
                            },
                        },
                        {"properties": {"metadata": {}, "context": {}}},
                    ],
                },
            },
        },
    }


class ChunkIdTests(unittest.TestCase):
    def test_build_chunk_id_matches_v121_escaping(self):
        self.assertEqual(build_chunk_id("a", "b", 0), "a_b_0")
        left = build_chunk_id("a", "b_c", 0)
        right = build_chunk_id("a_b", "c", 0)
        self.assertNotEqual(left, right)
        self.assertEqual(left, "a_b~5Fc_0")
        self.assertEqual(right, "a~5Fb_c_0")
        self.assertEqual(build_chunk_id("bank~x", "doc", 1), "bank~7Ex_doc_1")


class VertragTests(unittest.TestCase):
    def test_f2_matching_contract_passes(self):
        ok, lines = vertrag.check(synthetic_openapi())
        self.assertTrue(ok, lines)
        self.assertIn("OK: Version 1.2.1", lines)
        self.assertIn("OK: Retain-Felder items, async", lines)
        self.assertTrue(all(not line.startswith("ABWEICHUNG:") for line in lines), lines)
        self.assertIn("INFO: Lesen ID-Parameter Typ string", lines)
        self.assertIn("INFO: Lesen dokumentierte Status 200, 404", lines)

    def test_f2_missing_mode_is_not_a_deviation(self):
        ok, lines = vertrag.check(synthetic_openapi(with_mode=False))
        self.assertTrue(ok, lines)
        self.assertFalse(any(line.startswith("ABWEICHUNG:") for line in lines))
        self.assertFalse(any("Retain-Feld 'mode' fehlt" in line for line in lines))

    def test_f2_unexpected_mode_is_info_not_deviation(self):
        ok, lines = vertrag.check(synthetic_openapi(with_mode=True, mode_enum=("verbose",)))
        self.assertTrue(ok, lines)
        self.assertTrue(any("kein Request-Feld" in line for line in lines))
        self.assertFalse(any(line.startswith("ABWEICHUNG:") for line in lines))

    def test_f2_version_mismatch_is_a_deviation(self):
        ok, lines = vertrag.check(synthetic_openapi(version="1.2.3"))
        self.assertFalse(ok)
        self.assertIn("ABWEICHUNG: Version 1.2.3 statt 1.2.1", lines)

    def test_f2_unscoped_chunk_path_is_reported(self):
        _, lines = vertrag.check(synthetic_openapi())
        self.assertIn("INFO: Chunk-Pfad /v1/default/chunks/{chunk_id} (NICHT bankgebunden)", lines)

    def test_f2_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "spec.json"
            good.write_text(json.dumps(synthetic_openapi()))
            broken = Path(tmp) / "broken.json"
            broken.write_text("{nope")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(vertrag.main([str(good)]), 0)
                self.assertEqual(vertrag.main([str(broken)]), 2)
                self.assertEqual(vertrag.main([]), 2)
            self.assertIn("Vertrag: passt", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
