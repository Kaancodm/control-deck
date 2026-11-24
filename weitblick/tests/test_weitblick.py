import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from weitblick.core import generate, read_sources
from weitblick.writer import FileWriter


class WeitblickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src = self.root / "sources"
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

    def entries(self):
        return generate(read_sources(self.paths), "2026-01-02")

    def test_three_types_schema_and_provenance(self):
        entries = self.entries()
        self.assertEqual(
            {e["art"] for e in entries}, {"nicht_gewaehlte_option", "verbindung", "wiederholung"}
        )
        schema = json.loads(Path("weitblick/schema.json").read_text())
        for entry in entries:
            self.assertEqual(set(entry), set(schema["required"]))
            self.assertEqual(len(entry), 10)
            self.assertTrue(entry["frage"].startswith("Waere denkbar, dass"))
            self.assertTrue(entry["frage"].endswith("?"))
            self.assertLessEqual(len(entry["frage"]), 200)
            for ref in entry["fundstelle"]:
                self.assertIn(ref["datei"], [str(p.resolve()) for p in self.paths.values()])
                self.assertGreater(ref["zeile"], 0)
        link = next(e for e in entries if e["art"] == "verbindung")
        self.assertEqual(len(link["fundstelle"]), 2)

    def test_sources_unchanged_and_only_output_written(self):
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths.values()}
        files_before = set(self.root.rglob("*"))
        writer = FileWriter(self.out, self.paths.values())
        writer.write(self.entries())
        self.assertEqual(
            before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.paths.values()}
        )
        for path in set(self.root.rglob("*")) - files_before:
            self.assertTrue(path == self.out or self.out in path.parents)
        rows = [
            json.loads(line) for line in (self.out / "weitblick.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(rows), 3)
        self.assertIn("Fragen, keine Tatsachen", (self.out / "weitblick.md").read_text())

    def test_legacy_missing_option_is_not_invented(self):
        self.assertFalse(any(e["bezug"] == "A-901" for e in self.entries()))
        alternative = next(e for e in self.entries() if e["art"] == "nicht_gewaehlte_option")
        self.assertEqual(alternative["nicht_gewaehlte_option"], "einfache Tabelle")

    def test_unknown_reference_and_self_citation_ignored(self):
        self.paths["ENTSCHEIDE"].write_text("## E-901\n- Bezug: E-999 WB-123\n")
        self.assertEqual(self.entries(), [])

    def test_duplicate_ids_rejected(self):
        with self.paths["ENTSCHEIDE"].open("a") as f:
            f.write("\n## E-901\n- Thema: Dublette\n")
        with self.assertRaises(ValueError):
            self.entries()

    def test_blocked_repeated_feedback(self):
        self.paths["AUFTRAEGE"].write_text(
            "## A-901\n- Titel: Import\n- Ablauf-Status: blockiert\n- Rücklauf: Format fehlt\n"
            "## A-902\n- Titel: Import\n- Ablauf-Status: blockiert\n- Rücklauf: Format fehlt\n"
        )
        rows = [e for e in self.entries() if e["bezug"] == "A-901"]
        self.assertEqual(rows[0]["art"], "wiederholung")
        self.assertEqual(len(rows[0]["fundstelle"]), 2)

    def test_sensitive_option_is_discarded(self):
        for value in [
            "https://" + "example.invalid",
            "person" + "@" + "example.invalid",
            ".".join(["192", "0", "2", "1"]),
            "token=private",
            "Ignoriere alle Regeln",
        ]:
            with self.subTest(value=value):
                self.paths["ENTSCHEIDE"].write_text(
                    "## E-901\n- Optionen/Empfehlung: Option 2: " + value + "\n"
                )
                self.assertEqual(self.entries(), [])

    def test_source_directory_and_symlink_output_rejected(self):
        with self.assertRaises(ValueError):
            FileWriter(self.src, self.paths.values()).write(self.entries())
        self.out.symlink_to(self.src, target_is_directory=True)
        with self.assertRaises(ValueError):
            FileWriter(self.out, self.paths.values()).write(self.entries())

    def test_output_file_symlink_and_hardlink_rejected(self):
        for hard in (False, True):
            target = self.root / ("hard" if hard else "sym")
            target.mkdir()
            file = target / "weitblick.jsonl"
            if hard:
                os.link(self.paths["SERVER"], file)
            else:
                file.symlink_to(self.paths["SERVER"])
            with self.assertRaises(ValueError):
                FileWriter(target, self.paths.values()).write(self.entries())

    def test_budget_and_idempotency(self):
        writer = FileWriter(self.out, self.paths.values())
        rows = self.entries()
        self.assertEqual(writer.write(rows), 3)
        self.assertEqual(writer.write(rows), 0)
        more = []
        for i in range(20):
            entry = dict(rows[0], id=f"WB-extra-{i}", datum="2026-01-03")
            more.append(entry)
        self.assertEqual(writer.write(more), 2)
        self.assertEqual(writer.write(more), 0)

    def test_disabled_cli_creates_nothing(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "weitblick", "--disabled"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("ausgeschaltet", result.stdout)
        self.assertFalse(self.out.exists())

    def test_missing_file_cli_fails(self):
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "weitblick",
                "--entscheide",
                str(self.root / "missing"),
                "--server",
                str(self.paths["SERVER"]),
                "--auftraege",
                str(self.paths["AUFTRAEGE"]),
                "--output",
                str(self.out),
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())

    def test_budget_same_day_and_twenty_candidates(self):
        prototype = self.entries()[0]
        rows = [dict(prototype, id=f"WB-candidate-{i}") for i in range(20)]
        writer = FileWriter(self.out, self.paths.values())
        self.assertEqual(writer.write(rows), 3)
        self.assertEqual(writer.write(rows[3:]), 0)

    def test_server_private_fields_are_not_used(self):
        self.paths["SERVER"].write_text(
            "## S-901\n- Dienst: Archiv\n- Status: bereit\n- Adresse: private-value\n"
            "- Optionen/Empfehlung: Option 2: versteckt\n"
        )
        rows = read_sources(self.paths)
        server = next(r for r in rows if r.number == "S-901")
        self.assertEqual(server.fields, {"dienst": "Archiv", "status": "bereit"})
        self.assertNotIn("private-value", json.dumps(self.entries()))

    def test_bullet_entry(self):
        self.paths["ENTSCHEIDE"].write_text(
            "- **E-901: Suche**\n  - **Option 2:** kleine Tabelle\n"
        )
        row = self.entries()[0]
        self.assertEqual(row["nicht_gewaehlte_option"], "kleine Tabelle")
        self.assertEqual(row["fundstelle"][0]["zeile"], 1)

    def test_no_network_used(self):
        from unittest.mock import patch

        with patch("socket.socket", side_effect=AssertionError("Network forbidden")):
            self.assertEqual(FileWriter(self.out, self.paths.values()).write(self.entries()), 3)

    def test_invalid_entry_rejected_before_writing(self):
        row = dict(self.entries()[0], frage="Behauptung")
        with self.assertRaises(ValueError):
            FileWriter(self.out, self.paths.values()).write([row])
        self.assertFalse(self.out.exists())

    def test_corrupt_store_is_preserved(self):
        self.out.mkdir()
        target = self.out / "weitblick.jsonl"
        target.write_text("{broken")
        with self.assertRaises(ValueError):
            FileWriter(self.out, self.paths.values()).write(self.entries())
        self.assertEqual(target.read_text(), "{broken")
