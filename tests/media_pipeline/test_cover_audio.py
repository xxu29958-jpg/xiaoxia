"""Real WAV decoding, overlap, silence, mixing and cancellation, without model weights.

The converter is a deterministic signal fixture, not a model quality test.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf

from services.jobs.store import InterruptedJob
from services.voice.cover_pipeline import CoverPipeline

RATE = 44100


class SignalConverter:
    def __init__(self):
        self.calls = []

    def main(self, args):
        samples, rate = sf.read(args.source, dtype="float32")
        self.calls.append((len(samples), args.semi_tone_shift))
        # Include constant noise to exercise suppression in the input's long pause.
        sf.write(
            Path(args.output) / "vc_fixture.wav",
            samples * 0.7 + 0.001,
            rate,
            subtype="FLOAT",
        )


class CoverAudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.converter = SignalConverter()
        self.pipeline = CoverPipeline(self.converter, {"target": "fixture-voice.wav"})
        self.addCleanup(self.pipeline.gateway.close)
        for target, replacement in (
            ("torch.cuda.reset_peak_memory_stats", None),
            ("torch.cuda.max_memory_allocated", 0),
            ("torch.manual_seed", None),
        ):
            patched = patch(target, return_value=replacement)
            patched.start()
            self.addCleanup(patched.stop)
        # No health probe to the installed production service during a fixture test.
        patched = patch.object(
            self.pipeline.gateway,
            "get",
            return_value=SimpleNamespace(json=lambda: {"speech_active": False}),
        )
        patched.start()
        self.addCleanup(patched.stop)

    def source(self, seconds=39):
        time = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
        samples = (0.2 * np.sin(2 * np.pi * 220 * time)).astype(np.float32)
        samples[22 * RATE : 26 * RATE] = 0
        source = self.root / "input.wav"
        sf.write(source, samples, RATE, subtype="FLOAT")
        return source

    def test_full_length_overlap_pause_and_tail_are_preserved(self):
        checkpoints = []
        report = self.pipeline.convert(
            self.source(),
            self.root / "output",
            acapella=True,
            checkpoint=lambda *args: checkpoints.append(args),
        )
        output, rate = sf.read(report["output"], dtype="float32")
        self.assertEqual((len(output), rate, output.shape[1]), (39 * RATE, RATE, 2))
        self.assertEqual(
            [(w["start"], w["end"]) for w in report["windows"]],
            [(0, 18), (17.5, 35.5), (35, 39)],
        )
        self.assertEqual(
            [x for x in checkpoints if len(x) == 3][-1], ("converting", 3, 3)
        )
        self.assertLess(float(np.abs(output[23 * RATE : 25 * RATE]).max()), 1e-5)
        self.assertGreater(float(np.std(output[-RATE:])), 0.1)
        for boundary in (int(17.5 * RATE), 18 * RATE, 35 * RATE, int(35.5 * RATE)):
            self.assertLess(
                abs(float(output[boundary, 0] - output[boundary - 1, 0])), 0.02
            )
        self.assertLessEqual(float(np.abs(output).max()), 0.98)
        self.assertEqual(
            sf.info(self.root / "output/source-vocals.wav").subtype, "FLOAT"
        )
        self.assertEqual(sf.info(report["output"]).subtype, "PCM_16")

    def test_excerpt_and_window_edge_counts(self):
        source = self.source(20)
        for samples, count in ((18 * RATE, 1), (18 * RATE + 1, 2)):
            with self.subTest(samples=samples):
                progress = []
                output = self.root / str(samples)
                report = self.pipeline.convert(
                    source,
                    output,
                    acapella=True,
                    pitch=2,
                    start_seconds=1,
                    duration_seconds=samples / RATE,
                    checkpoint=lambda *args: progress.append(args),
                )
                self.assertEqual(sf.info(report["output"]).frames, samples)
                self.assertEqual(len(report["windows"]), count)
                self.assertIn(("converting", count, count), progress)
                self.assertEqual(report["start_seconds"], 1)
                self.assertTrue(all(pitch == 2 for _, pitch in self.converter.calls))

    def test_cancel_after_first_window_never_writes_complete_track(self):
        def checkpoint(stage=None, done=0, total=None):
            if stage == "converting" and done == 1:
                raise InterruptedJob("job_cancelled")

        output = self.root / "cancelled"
        with self.assertRaisesRegex(InterruptedJob, "job_cancelled"):
            self.pipeline.convert(
                self.source(), output, acapella=True, checkpoint=checkpoint
            )
        self.assertEqual(len(self.converter.calls), 1)
        self.assertFalse((output / "xiaxia-cover.wav").exists())
        self.assertFalse((output / "result.json").exists())


if __name__ == "__main__":
    unittest.main()
