import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "jsonl_byte_shards.py"


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def closed_stdout(self, *args):
        with subprocess.Popen([sys.executable, str(SCRIPT), *map(str, args)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            child.stdout.close()
            code = child.wait(timeout=15)
            errors = child.stderr.read()
        self.assertEqual(code, 2, errors)
        self.assertNotIn(b'Exception ignored', errors)

    def test_closed_stdout_split_returns_2_and_preserves_valid_publication(self):
        raw = b'1\n2\n3\n'
        source = self.base / 'source.jsonl'
        source.write_bytes(raw)
        directory = self.base / 'parts'
        self.closed_stdout('split', source, '--out-dir', directory, '--part-bytes', 4)
        self.cli('verify', directory / 'manifest.json')
        self.assertEqual(source.read_bytes(), raw)

    def test_closed_stdout_verify_returns_2_and_preserves_bundle(self):
        manifest = self.split()
        original_manifest = manifest.read_bytes()
        source = self.base / 'source.jsonl'
        self.closed_stdout('verify', manifest)
        self.cli('verify', manifest)
        self.assertEqual(manifest.read_bytes(), original_manifest)
        self.assertEqual(source.read_bytes(), b'1\n2\n3\n')

    def test_closed_stdout_join_returns_2_and_preserves_valid_publication(self):
        manifest = self.split()
        output = self.base / 'joined.jsonl'
        self.closed_stdout('join', manifest, '--out', output)
        self.assertEqual(output.read_bytes(), b'1\n2\n3\n')
        self.assertEqual((self.base / 'source.jsonl').read_bytes(), b'1\n2\n3\n')
        self.cli('verify', manifest)

    def split(self, data=b'1\n2\n3\n', limit=4):
        source = self.base / "source.jsonl"
        source.write_bytes(data)
        directory = self.base / "parts"
        self.cli("split", source, "--out-dir", directory, "--part-bytes", limit)
        return directory / "manifest.json"

    def manifest(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_exact_roundtrip_greedy_and_deterministic_manifest(self):
        data = '{"x":"雪"}\r\n42\n[true,null]'.encode()
        manifest = self.split(data, 16)
        value = self.manifest(manifest)
        self.assertEqual(value["source"], {"bytes": len(data), "records": 3,
                                          "sha256": hashlib.sha256(data).hexdigest()})
        self.assertEqual([p["bytes"] for p in value["parts"]], [len(data.splitlines(keepends=True)[0]) + 3, 11])
        self.assertEqual([(p["first_record"], p["last_record"]) for p in value["parts"]], [(1, 2), (3, 3)])
        self.cli("verify", manifest)
        out = self.base / "roundtrip.jsonl"
        self.cli("join", manifest, "--out", out)
        self.assertEqual(out.read_bytes(), data)
        second = self.base / "second"
        self.cli("split", self.base / "source.jsonl", "--out-dir", second, "--part-bytes", 16)
        self.assertEqual(manifest.read_bytes(), (second / "manifest.json").read_bytes())

    def test_tampered_hash_size_and_line_metadata(self):
        manifest = self.split()
        original = manifest.read_bytes()
        for location, key, replacement in [("source", "sha256", "0" * 64),
                                             ("source", "bytes", 7),
                                             ("part", "sha256", "0" * 64),
                                             ("part", "bytes", 3),
                                             ("part", "last_record", 1)]:
            with self.subTest(location=location, key=key):
                manifest.write_bytes(original)
                value = self.manifest(manifest)
                target = value["source"] if location == "source" else value["parts"][0]
                target[key] = replacement
                self.save(manifest, value)
                self.cli("verify", manifest, code=1)
                out = self.base / "rejected.jsonl"
                self.cli("join", manifest, "--out", out, code=1)
                self.assertFalse(out.exists())

    def test_tampered_part_bytes_and_payload_never_reported(self):
        manifest = self.split(b'"SECRET_PAYLOAD"\n', 30)
        value = self.manifest(manifest)
        part = manifest.parent / value["parts"][0]["filename"]
        part.write_bytes(b'"SECRET_CHANGED"\n')
        result = self.cli("verify", manifest, code=1)
        self.assertNotIn("SECRET", result.stdout + result.stderr)
        part.write_bytes(b'SECRET_INVALID_JSON\n')
        result = self.cli("verify", manifest, code=1)
        self.assertNotIn("SECRET", result.stdout + result.stderr)

    def test_strict_jsonl_and_oversized_records_create_no_output(self):
        for data in [b'', b'\n', b'1\n \n', b'\xef\xbb\xbf1\n', b'\xff\n',
                     b'{"x":1,"x":2}\n', b'NaN\n', b'Infinity\n', b'1 2\n', b'1\r2\n', b'12345\n']:
            with self.subTest(data=data):
                source = self.base / "invalid.jsonl"
                source.write_bytes(data)
                out = self.base / "absent"
                self.cli("split", source, "--out-dir", out, "--part-bytes", 5, code=2)
                self.assertFalse(os.path.lexists(out))
                self.assertEqual(source.read_bytes(), data)

    def test_any_json_values_and_large_number_lexemes(self):
        self.split(b'null\ntrue\n"ok"\n[]\n{}\n1e999999\n' + b'9' * 5000 + b'\n', 6000)

    def test_occupied_outputs_and_hardlinks_preserve_bytes(self):
        manifest = self.split()
        target = self.base / "occupied"
        target.write_bytes(b"KEEP")
        self.cli("join", manifest, "--out", target, code=2)
        self.cli("split", self.base / "source.jsonl", "--out-dir", manifest.parent, "--part-bytes", 4, code=2)
        link = self.base / "hardlink"
        os.link(target, link)
        self.cli("join", manifest, "--out", link, code=2)
        self.assertEqual(target.read_bytes(), b"KEEP")
        self.assertEqual(link.read_bytes(), b"KEEP")
        self.cli("verify", manifest)

    def test_malicious_names_reorder_unknown_schema_and_boolean_integers(self):
        manifest = self.split()
        original = manifest.read_bytes()
        values = []
        for filename in ["../outside", "/absolute", "C:\\bad", "sub/part-00001.jsonl", "part-00002.jsonl"]:
            value = json.loads(original)
            value["parts"][0]["filename"] = filename
            values.append(value)
        for key, replacement in [("format_version", True), ("part_bytes", True), ("part_bytes", 0),
                                 ("part_bytes", 16777217), ("unexpected", 1)]:
            value = json.loads(original)
            value[key] = replacement
            values.append(value)
        value = json.loads(original)
        value["source"]["records"] = True
        values.append(value)
        value = json.loads(original)
        value["parts"].reverse()
        values.append(value)
        for value in values:
            self.save(manifest, value)
            self.cli("verify", manifest, code=2)

    def test_duplicate_manifest_keys_and_corrupt_manifest(self):
        manifest = self.split()
        for data in [b'{}', b'null', b'{"format_version":1,"format_version":1}', b'\xef\xbb\xbf{}', b'{"bad":NaN}']:
            manifest.write_bytes(data)
            self.cli("verify", manifest, code=2)

    def test_nonfinal_unterminated_record_and_non_greedy_manifest(self):
        manifest = self.split()
        value = self.manifest(manifest)
        part = manifest.parent / value["parts"][0]["filename"]
        part.write_bytes(b'1\n2')
        value["parts"][0]["bytes"] = 3
        value["parts"][0]["sha256"] = hashlib.sha256(b'1\n2').hexdigest()
        value["source"]["bytes"] = 5
        value["source"]["sha256"] = hashlib.sha256(b'1\n23\n').hexdigest()
        self.save(manifest, value)
        self.cli("verify", manifest, code=1)

    def test_extra_files_are_outside_scope(self):
        manifest = self.split()
        (manifest.parent / "unlisted.jsonl").write_bytes(b"do not read")
        out = self.base / "joined"
        self.cli("join", manifest, "--out", out)
        self.assertEqual(out.read_bytes(), b'1\n2\n3\n')

    def test_links_rejected_when_os_supports_symlinks(self):
        manifest = self.split()
        part = manifest.parent / self.manifest(manifest)["parts"][0]["filename"]
        saved = self.base / "saved"
        part.rename(saved)
        try:
            part.symlink_to(saved)
        except OSError as exc:
            self.skipTest("OS symlink privilege unavailable: " + str(exc.winerror))
        self.cli("verify", manifest, code=2)
        dangling = self.base / "dangling"
        dangling.symlink_to(self.base / "missing")
        self.cli("join", manifest, "--out", dangling, code=2)
        self.assertTrue(dangling.is_symlink())

    def test_actual_record_and_part_limits(self):
        source = self.base / "records"
        source.write_bytes(b'0\n' * 100001)
        self.cli("split", source, "--out-dir", self.base / "none", "--part-bytes", 10000, code=2)
        source.write_bytes(b'0\n' * 1001)
        self.cli("split", source, "--out-dir", self.base / "none", "--part-bytes", 2, code=2)
        source.write_bytes(b'"' + b'x' * (1024 * 1024) + b'"\n')
        self.cli("split", source, "--out-dir", self.base / "none", "--part-bytes", 2000000, code=2)
        self.assertFalse((self.base / "none").exists())

    def test_manifest_size_limit_and_missing_file_are_setup_errors(self):
        manifest = self.base / "big.json"
        manifest.write_bytes(b' ' * (1024 * 1024 + 1))
        self.cli("verify", manifest, code=2)
        self.cli("verify", self.base / "missing", code=2)

    def test_actual_source_and_part_hard_byte_limits(self):
        source = self.base / "large"
        with source.open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        self.cli("split", source, "--out-dir", self.base / "absent", "--part-bytes", 100, code=2)
        manifest = self.split()
        part = manifest.parent / self.manifest(manifest)["parts"][0]["filename"]
        with part.open("wb") as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
        self.cli("verify", manifest, code=2)

    def test_hardlinked_parts_are_refused(self):
        manifest = self.split()
        part = manifest.parent / self.manifest(manifest)["parts"][0]["filename"]
        alias = self.base / "alias"
        os.link(part, alias)
        self.cli("verify", manifest, code=2)
        self.assertEqual(alias.read_bytes(), b'1\n2\n')

    def test_nonregular_input_is_refused_before_opening(self):
        module = self.load_module()
        with patch.object(module.os, "open", side_effect=AssertionError("must not open nonregular node")):
            with self.assertRaises(module.ShardError):
                module.read_bounded(self.base, 100)

    def test_non_greedy_pack_with_matching_all_hashes_is_rejected(self):
        manifest = self.split(b'1\n2\n3\n', 4)
        value = self.manifest(manifest)
        first, second = value["parts"]
        for part, data, begin, end in [(first, b'1\n', 1, 1), (second, b'2\n3\n', 2, 3)]:
            (manifest.parent / part["filename"]).write_bytes(data)
            part.update(bytes=len(data), first_record=begin, last_record=end, sha256=hashlib.sha256(data).hexdigest())
        self.save(manifest, value)
        self.cli("verify", manifest, code=1)

    def test_reparsed_content_rejected_even_with_recomputed_hashes(self):
        manifest = self.split(b'"ok"\n', 20)
        value = self.manifest(manifest)
        data = b'NaN\n'
        part = value["parts"][0]
        (manifest.parent / part["filename"]).write_bytes(data)
        part.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        value["source"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.save(manifest, value)
        self.cli("verify", manifest, code=1)

    def test_argument_hard_bounds(self):
        source = self.base / "source"
        source.write_bytes(b'0\n')
        for limit in [0, -1, 16777217]:
            self.cli("split", source, "--out-dir", self.base / "none", "--part-bytes", limit, code=2)

    def test_split_publication_io_failure_leaves_no_valid_manifest(self):
        module = self.load_module()
        source = self.base / "source"
        source.write_bytes(b'1\n2\n3\n')
        out = self.base / "partial"
        real_write = module.write_new
        def fail_during_publish(path, data):
            if Path(path).parent == out and Path(path).name == "part-00002.jsonl":
                raise OSError("synthetic publication failure")
            return real_write(path, data)
        with patch.object(module, "write_new", side_effect=fail_during_publish):
            with self.assertRaises(OSError):
                module.split_bundle(source, out, 4)
        self.assertTrue(out.is_dir())
        self.assertFalse((out / "manifest.json").exists())
        self.assertEqual((out / "part-00001.jsonl").read_bytes(), b'1\n2\n')
        self.assertEqual(source.read_bytes(), b'1\n2\n3\n')

    def load_module(self):
        self.assertTrue(SCRIPT.is_file(), "CLI implementation is missing")
        spec = importlib.util.spec_from_file_location("shards_for_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_join_uses_validated_bytes_even_if_parts_change_after_validation(self):
        module = self.load_module()
        manifest = self.split()
        real_verify = module.verify_bundle
        def mutate_after_verify(*args, **kwargs):
            result = real_verify(*args, **kwargs)
            part = manifest.parent / self.manifest(manifest)["parts"][0]["filename"]
            part.write_bytes(b'9\n9\n')
            return result
        out = self.base / "joined"
        with patch.object(module, "verify_bundle", side_effect=mutate_after_verify):
            module.join_bundle(manifest, out)
        self.assertEqual(out.read_bytes(), b'1\n2\n3\n')


if __name__ == "__main__":
    unittest.main()
