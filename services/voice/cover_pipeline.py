"""Convert complete performances while preserving their timing and accompaniment."""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import librosa
import numpy as np
import requests
import soundfile as sf
import torch
import yaml
from ml_collections import ConfigDict
from scipy.ndimage import uniform_filter1d

from services.runtime import path

ASSETS = path("separator_models")
SOUL = path("soul_repo")


class CoverPipeline:
    """Use the resident voice converter for songs and opera of arbitrary length."""

    def __init__(self, seed, seed_args):
        self.seed = seed
        self.seed_args = seed_args
        self.separator = None
        self.config = None
        self.gateway = requests.Session()
        self.gateway.trust_env = False

    def _wait_for_speech(self, checkpoint=None):
        """Give the conversational voice the GPU between singing windows."""
        if checkpoint:
            checkpoint()
        try:
            while (
                self.gateway.get("http://127.0.0.1:6190/health", timeout=2)
                .json()
                .get("speech_active")
            ):
                if checkpoint:
                    checkpoint()
                time.sleep(0.1)
        except requests.RequestException:
            pass

    def convert(
        self,
        source: Path,
        output: Path,
        *,
        acapella=False,
        pitch=0,
        start_seconds=0.0,
        duration_seconds=None,
        checkpoint=None,
    ):
        """Separate, convert overlapping vocal windows and mix a complete track.

        Args:
            source: A complete source performance, decoded by librosa.
            output: Directory for stems, the converted track and timings.
            acapella: True when the source already contains only singing.
            pitch: Explicit vocal pitch shift in semitones, normally zero.
            start_seconds: Start of an explicitly selected excerpt.
            duration_seconds: Excerpt duration, or None to retain all remaining audio.

        Returns:
            Paths, exact durations and measured processing times.
        """
        check = checkpoint or (lambda *args: None)
        check("loading")
        started = time.perf_counter()
        output.mkdir(parents=True, exist_ok=True)
        rate = 44100
        mix = self._load_source(source, rate, start_seconds, duration_seconds)
        samples = mix.shape[-1]
        torch.cuda.reset_peak_memory_stats()
        vocals, backing, separation_seconds = self._separate(mix, rate, acapella, check)
        # Separated stems can exceed full scale even when the input mix does not.
        # Preserve these peaks until the final mix gain, instead of clipping them.
        sf.write(output / "source-vocals.wav", vocals.T, rate, subtype="FLOAT")
        sf.write(output / "instrumental.wav", backing.T, rate, subtype="FLOAT")

        converted, windows, conversion_seconds = self._convert_vocals(
            vocals, output, rate, pitch, check
        )
        self._write_mix(converted, vocals.mean(axis=0), backing, output, rate)
        report = {
            "engine": "Mel-Band Roformer + Seed-VC" if not acapella else "Seed-VC",
            "source": str(source),
            "start_seconds": start_seconds,
            "requested_duration_seconds": duration_seconds,
            "reference": self.seed_args["target"],
            "output": str(output / "xiaxia-cover.wav"),
            "audio_seconds": samples / rate,
            "pitch_shift": pitch,
            "separation_seconds": round(separation_seconds, 3),
            "conversion_seconds": round(conversion_seconds, 3),
            "generation_seconds": round(time.perf_counter() - started, 3),
            "gpu_peak_gib": round(torch.cuda.max_memory_allocated() / 1024**3, 3),
            "windows": windows,
            "evaluation": "Complete technical output; perceptual quality is not established.",
        }
        (output / "result.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report

    @staticmethod
    def _load_source(source, rate, start_seconds, duration_seconds):
        mix, _ = librosa.load(
            source, sr=rate, mono=False, offset=start_seconds, duration=duration_seconds
        )
        if mix.ndim == 1:
            mix = np.stack([mix, mix])
        else:
            mix = mix[:2]
        samples = mix.shape[-1]
        if not samples:
            raise ValueError("The selected audio interval is empty")
        return mix

    def _separate(self, mix, rate, acapella, check):
        if acapella:
            vocals = mix
            backing = np.zeros_like(mix)
            separation_seconds = 0.0
        else:
            sys.path.insert(0, str(SOUL))
            from preprocess.tools.vocal_separation.modules.bs_roformer import (
                MelBandRoformer,
            )
            from preprocess.tools.vocal_separation.utils.model_utils import demix

            separated_at = time.perf_counter()
            if self.separator is None:
                self.config = ConfigDict(
                    yaml.load(
                        (ASSETS / "config_karaoke_becruily.yaml").read_text(),
                        Loader=yaml.FullLoader,
                    )
                )
                model, inference = self.config["model"], self.config["inference"]
                if not isinstance(model, ConfigDict) or not isinstance(
                    inference, ConfigDict
                ):
                    raise ValueError(
                        "Separator model and inference settings must be mappings"
                    )
                self.separator = MelBandRoformer(**model.to_dict())
                state = torch.load(
                    ASSETS / "mel_band_roformer_karaoke_becruily.ckpt",
                    map_location="cpu",
                    weights_only=True,
                )
                self.separator.load_state_dict(state)
                self.separator.eval().half()
                inference.num_overlap = 4
                inference.chunk_size = 5 * rate
            check("separating")
            self.separator.to("cuda")
            hook = self.separator.register_forward_pre_hook(
                lambda *_: self._wait_for_speech(check)
            )
            try:
                assert self.config is not None
                stems = demix(
                    self.config,
                    self.separator,
                    mix,
                    torch.device("cuda"),
                    "mel_band_roformer",
                )
            finally:
                hook.remove()
                self.separator.cpu()
                torch.cuda.empty_cache()
            vocals = stems["Vocals"]
            backing = stems["Instrumental"]
            separation_seconds = time.perf_counter() - separated_at
        return vocals, backing, separation_seconds

    def _convert_vocals(self, vocals, output, rate, pitch, check):
        samples = vocals.shape[-1]
        # Bound the F0 extractor as well as diffusion; the upstream CLI only chunks
        # the latter. Overlap keeps sustained vowels intact at window boundaries.
        window, overlap = 18 * rate, rate // 2
        step = window - overlap
        converted = np.zeros(samples, dtype=np.float32)
        weights = np.zeros(samples, dtype=np.float32)
        converted_at = time.perf_counter()
        mono = vocals.mean(axis=0)
        windows = []
        total_windows = (max(0, samples - window) + step - 1) // step + 1
        for number, start in enumerate(range(0, samples, step)):
            check("converting", number, total_windows)
            self._wait_for_speech(check)
            end = min(start + window, samples)
            folder = output / "windows" / f"{number:04d}"
            folder.mkdir(parents=True, exist_ok=True)
            source_window = folder / "source.wav"
            sf.write(source_window, mono[start:end], rate, subtype="FLOAT")
            torch.manual_seed(42)
            at = time.perf_counter()
            args = dict(
                self.seed_args,
                source=str(source_window),
                output=str(folder),
                semi_tone_shift=pitch,
            )
            self.seed.main(SimpleNamespace(**args))
            wave, _ = sf.read(next(folder.glob("vc_*.wav")), dtype="float32")
            wave = np.pad(wave, (0, max(0, end - start - len(wave))))[: end - start]
            weight = np.ones(len(wave), dtype=np.float32)
            fade = min(overlap, len(wave))
            if start:
                weight[:fade] = np.linspace(0, 1, fade)
            if end < samples:
                weight[-fade:] = np.linspace(1, 0, fade)
            converted[start:end] += wave * weight
            weights[start:end] += weight
            windows.append(
                {
                    "start": start / rate,
                    "end": end / rate,
                    "seconds": round(time.perf_counter() - at, 3),
                }
            )
            check("converting", number + 1, total_windows)
            if end == samples:
                break
        check("mixing")
        converted /= np.maximum(weights, 1e-8)
        # Conversion can invent breaths in instrumental-only passages. Follow the
        # source vocal's continuous level so pauses stay pauses, including long ones.
        level = np.sqrt(np.maximum(uniform_filter1d(mono * mono, rate // 50), 0))
        threshold = max(float(np.percentile(level, 95)) * 0.03, 1e-6)
        vocal_gain = np.minimum(1, (level / threshold) ** 2)
        converted *= uniform_filter1d(vocal_gain, rate // 50)
        conversion_seconds = time.perf_counter() - converted_at
        return converted, windows, conversion_seconds

    @staticmethod
    def _write_mix(converted, mono, backing, output, rate):
        original_rms = float(np.sqrt(np.mean(mono.astype(np.float64) ** 2)))
        converted_rms = float(np.sqrt(np.mean(converted.astype(np.float64) ** 2)))
        if converted_rms:
            converted *= original_rms / converted_rms
        result = backing + converted[None, :]
        peak = float(np.abs(result).max())
        if peak > 0.98:
            result *= 0.98 / peak
        sf.write(output / "xiaxia-vocals.wav", converted, rate, subtype="FLOAT")
        sf.write(output / "xiaxia-cover.wav", result.T, rate, subtype="PCM_16")
