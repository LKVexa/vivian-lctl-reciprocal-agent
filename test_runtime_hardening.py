"""Regression checks for pinned single-snapshot policy admission."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import runtime


ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / 'agent.json').read_text(encoding='utf-8'))
AGENT_ID = CONFIG['agent_id']
PINS = CONFIG['policy_sha256']


class SnapshotAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = tuple(Path(self.temp.name) / (AGENT_ID + suffix)
                           for suffix in ('.lctlc', '.brimg', '.brir'))
        for path in self.paths:
            shutil.copyfile(ROOT / path.name, path)

    def test_manifest_pins_bind_all_loaded_snapshots_and_receipt(self):
        program = runtime.load_program(*self.paths, expected_hashes=PINS)
        self.assertEqual(program.source_sha256, PINS['source_sha256'])
        self.assertEqual(program.image_sha256, PINS['image_sha256'])
        self.assertEqual(program.brir_sha256, PINS['brir_sha256'])
        with patch.object(runtime, '_bounded_read', side_effect=AssertionError('Execution reopened policy')):
            receipt = runtime.run_program(program, [0] * 16)
        for key in PINS:
            self.assertEqual(receipt[key], PINS[key])

    def test_replacement_before_image_snapshot_fails_pinned_admission(self):
        # This models replacement after an earlier caller check but before the
        # actual image read. A refreshed unsigned checksum cannot bypass pins.
        original_read = runtime._bounded_read
        source, image, _ = self.paths
        replaced = False

        def racing_read(path, maximum):
            nonlocal replaced
            snapshot = original_read(path, maximum)
            if path == source and not replaced:
                changed = bytearray(original_read(image, runtime.MAX_IMAGE_BYTES))
                changed[88] ^= 1
                changed[32:64] = hashlib.sha256(changed[80:]).digest()
                image.write_bytes(changed)
                replaced = True
            return snapshot

        with patch.object(runtime, '_bounded_read', side_effect=racing_read):
            with self.assertRaisesRegex(runtime.PolicyError, 'image_sha256'):
                runtime.load_program(*self.paths, expected_hashes=PINS)
        self.assertTrue(replaced)

    def test_replacement_after_snapshot_does_not_change_executed_identity(self):
        # No pathname is re-opened after its hash is checked. Changing every
        # backing file after its one read cannot alter the admitted snapshot.
        original_read = runtime._bounded_read
        reads = []

        def replace_after_read(path, maximum):
            snapshot = original_read(path, maximum)
            reads.append(path)
            path.write_bytes(b'replaced after bounded snapshot')
            return snapshot

        with patch.object(runtime, '_bounded_read', side_effect=replace_after_read):
            program = runtime.load_program(*self.paths, expected_hashes=PINS)
        self.assertEqual(reads, list(self.paths))
        receipt = runtime.run_program(program, [0] * 16)
        self.assertTrue(receipt['halted'])
        self.assertEqual(receipt['image_sha256'], PINS['image_sha256'])

    def test_source_image_and_brir_tampering_each_fail_distribution_hashes(self):
        keys = ('source_sha256', 'image_sha256', 'brir_sha256')
        for path, key in zip(self.paths, keys):
            with self.subTest(component=key):
                original = path.read_bytes()
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(runtime.PolicyError, key):
                    runtime.load_program(*self.paths, expected_hashes=PINS)
                path.write_bytes(original)

    def test_oversized_components_are_read_with_limit_plus_one_only(self):
        limits = (runtime.MAX_SOURCE_BYTES, runtime.MAX_IMAGE_BYTES, runtime.MAX_SOURCE_BYTES)
        real_open = Path.open

        class CheckedReader:
            def __init__(self, handle, path, calls):
                self.handle, self.path, self.calls = handle, path, calls

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.handle.close()

            def read(self, size=-1):
                if size < 0:
                    raise AssertionError('Unbounded policy read')
                self.calls.append((self.path, size))
                return self.handle.read(size)

        for oversized, limit in zip(self.paths, limits):
            with self.subTest(component=oversized.suffix):
                original = oversized.read_bytes()
                oversized.write_bytes(b'x' * (limit + 8192))
                calls = []

                def bounded_open(path, *args, **kwargs):
                    return CheckedReader(real_open(path, *args, **kwargs), path, calls)

                with patch.object(Path, 'open', bounded_open):
                    with self.assertRaisesRegex(runtime.PolicyError, 'resource bound'):
                        runtime.load_program(*self.paths, expected_hashes=PINS)
                self.assertIn((oversized, limit + 1), calls)
                self.assertTrue(all(size <= limits[self.paths.index(path)] + 1 for path, size in calls))
                oversized.write_bytes(original)

    def test_explicit_or_pinned_brir_is_required(self):
        self.paths[2].unlink()
        with self.assertRaises(runtime.PolicyError):
            runtime.load_program(*self.paths)
        with self.assertRaises(runtime.PolicyError):
            runtime.load_program(*self.paths[:2], expected_hashes=PINS)

    def test_generic_unpinned_missing_brir_remains_optional(self):
        self.paths[2].unlink()
        program = runtime.load_program(*self.paths[:2])
        self.assertIsNone(program.brir_sha256)
        self.assertTrue(runtime.run_program(program, [0] * 16)['halted'])

    def test_malformed_pins_fail_before_opening_files(self):
        bad = [True, [], {}, {'image_sha256': PINS['image_sha256']},
               dict(PINS, unknown='a' * 64), dict(PINS, image_sha256='not a digest'),
               dict(PINS, brir_sha256=True)]
        for values in bad:
            with self.subTest(pins=values):
                with patch.object(runtime, '_bounded_read', side_effect=AssertionError('Read malformed contract')):
                    with self.assertRaises(runtime.PolicyError):
                        runtime.load_program(*self.paths, expected_hashes=values)


if __name__ == '__main__':
    unittest.main()
