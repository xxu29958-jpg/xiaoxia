"""Installation and asset-consumer tests; no live service, account or GPU needed."""

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from tools.download_models import selected_models
from tools.installation.fish import prepare
from tools.installation.sources import archive_files, install_source

REPO = Path(__file__).resolve().parents[2]


def fixture_archive(names=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in (names or {"pinned/package.py": b"value = 1\n"}).items():
            member = zipfile.ZipInfo("placeholder")
            member.filename = name  # Keep deliberately invalid names on Windows too.
            archive.writestr(member, data)
    data = buffer.getvalue()
    return data, {
        "archive_sha256": hashlib.sha256(data).hexdigest(),
        "archive_root": "pinned",
    }


class SourceInstallationTests(unittest.TestCase):
    def test_existing_windows_checkout_retains_crlf_without_losing_source_identity(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            source = target / "package.py"
            source.write_bytes(b"value = 1\r\n")
            self.assertEqual(
                install_source(
                    target, {"package.py": b"value = 1\n"}, allow_create=False
                ),
                "verified",
            )
            self.assertEqual(source.read_bytes(), b"value = 1\r\n")

    def test_fresh_install_repeat_and_changed_source_preserved(self):
        data, specification = fixture_archive()
        files = archive_files(data, specification)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fish"
            self.assertEqual(
                install_source(target, files, allow_create=True), "installed"
            )
            self.assertEqual(
                install_source(target, files, allow_create=True), "verified"
            )
            source = target / "package.py"
            source.write_bytes(b"user change\n")
            with self.assertRaisesRegex(ValueError, "preserved"):
                install_source(target, files, allow_create=True)
            self.assertEqual(source.read_bytes(), b"user change\n")

    def test_corrupt_archive_rejected_before_creation(self):
        data, specification = fixture_archive()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            archive_files(data + b"corruption", specification)

    def test_traversal_and_absolute_archive_members_are_rejected(self):
        for name in (
            "pinned/../outside.py",
            "/pinned/absolute.py",
            "pinned/C:/file",
            "pinned/a\\b",
        ):
            with self.subTest(name=name):
                data, specification = fixture_archive({name: b"source"})
                with self.assertRaisesRegex(ValueError, "Unsafe"):
                    archive_files(data, specification)

    def test_unexpected_source_file_is_not_silently_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            (target / "package.py").write_bytes(b"expected")
            metadata = target / "package.egg-info"
            metadata.mkdir()
            (metadata / "PKG-INFO").write_bytes(b"Name: package")
            self.assertEqual(
                install_source(target, {"package.py": b"expected"}, allow_create=False),
                "verified",
            )
            extra = metadata / "injected.py"
            extra.write_bytes(b"unexpected")
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                install_source(target, {"package.py": b"expected"}, allow_create=False)
            self.assertEqual(extra.read_bytes(), b"unexpected")

    def test_missing_external_directory_is_never_created(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "external"
            with self.assertRaisesRegex(ValueError, "External"):
                install_source(target, {"package.py": b"source"}, allow_create=False)
            self.assertFalse(target.exists())

    def test_prepare_uses_managed_default_and_preserves_configuration(self):
        data, specification = fixture_archive()
        specification.update(
            revision="fixed", install_path=".runtime/sources/fish_speech"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vendor").mkdir()
            (root / "vendor/sources.json").write_text(
                json.dumps({"fish_speech": specification})
            )
            archive = root / "offline.zip"
            archive.write_bytes(data)
            config = root / "settings.json"
            config.write_bytes(b'{"speech_engine":"fish","retained":"value"}')
            before = config.read_bytes()
            with patch.dict(os.environ, {"XIAOXIA_CONFIG": str(config)}):
                self.assertEqual(prepare(root, archive)["status"], "installed")
                self.assertEqual(prepare(root, archive)["status"], "verified")
            self.assertEqual(config.read_bytes(), before)
            self.assertEqual(
                (root / ".runtime/sources/fish_speech/package.py").read_bytes(),
                b"value = 1\n",
            )


class ModelSelectionTests(unittest.TestCase):
    def test_selected_fish_gets_all_pinned_assets_without_other_speech_models(self):
        records = selected_models(REPO, None, {"fish"})
        components = {r["component"] for r in records}
        self.assertIn("fish_models", components)
        self.assertIn("seed_models", components)
        self.assertNotIn("cosy_models", components)
        self.assertNotIn("index_models", components)
        self.assertNotIn("index_aux", components)
        self.assertEqual(sum(r["component"] == "fish_models" for r in records), 11)

    def test_default_cosy_does_not_download_fish_and_explicit_only_works(self):
        self.assertNotIn(
            "fish_models",
            {r["component"] for r in selected_models(REPO, None, {"cosy"})},
        )
        self.assertEqual(
            {r["component"] for r in selected_models(REPO, "fish_models", {"cosy"})},
            {"fish_models"},
        )

    def test_unknown_component_is_an_error_instead_of_false_success(self):
        with self.assertRaisesRegex(ValueError, "Unknown model"):
            selected_models(REPO, "fish_typo", {"fish"})

    def test_model_assets_include_license_tokenizer_and_all_weight_shards(self):
        files = {r["file"] for r in selected_models(REPO, "fish_models", {"fish"})}
        self.assertTrue(
            {
                "LICENSE.md",
                "tokenizer.json",
                "config.json",
                "codec.pth",
                "model-00001-of-00002.safetensors",
                "model-00002-of-00002.safetensors",
            }
            <= files
        )


class ReproducibilityTests(unittest.TestCase):
    def test_generated_lock_matches_its_declared_inputs_and_output(self):
        provenance = json.loads(
            (REPO / "requirements/fish.provenance.json").read_text()
        )
        for name, expected in provenance["inputs"].items():
            with self.subTest(path=name):
                self.assertEqual(
                    hashlib.sha256((REPO / name).read_bytes()).hexdigest(), expected
                )
        output = provenance["output"]
        self.assertEqual(
            hashlib.sha256((REPO / output["path"]).read_bytes()).hexdigest(),
            output["sha256"],
        )
        self.assertIn("known_metadata_conflict", provenance)

    def test_setup_powershell_parses_without_running_installation(self):
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "$env:XIAOXIA_TEST_SCRIPT,[ref]$tokens,[ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
        )
        environment = {**os.environ, "XIAOXIA_TEST_SCRIPT": str(REPO / "setup.ps1")}
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
            env=environment,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
