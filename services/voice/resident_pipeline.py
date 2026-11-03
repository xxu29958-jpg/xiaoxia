"""Reuse the score singer and voice converter for consecutive local requests."""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from services.runtime import path, voice_profile

ROOT = path("voice_state")
SOUL = path("soul_repo")


class SingingPipeline:
    """Synthesize an aligned score, then apply the existing Xiaxia reference."""

    def __init__(self):
        started = time.perf_counter()
        from services.voice.seed_runtime import BASE, configure

        seed_config = configure()
        import inference as seed
        import soundfile as sf
        import torch

        self.torch = torch
        self.sf = sf
        self.seed = seed
        profile, reference = voice_profile()
        self.seed_args = {
            "target": str(reference),
            "diffusion_steps": 32,
            "length_adjust": 1.0,
            "inference_cfg_rate": 0.7,
            "f0_condition": True,
            "auto_f0_adjust": False,
            "semi_tone_shift": 0,
            "checkpoint": str(BASE),
            "config": str(seed_config),
            "fp16": True,
        }
        self.seed_models = seed.load_models(SimpleNamespace(**self.seed_args))
        seed.load_models = lambda args: self.seed_models
        # The cover mixer and speech endpoint apply the final playback gain.
        seed.torchaudio.save = lambda path, wave, rate: sf.write(
            path, wave.detach().cpu().numpy().T, rate, subtype="FLOAT"
        )

        sys.path.insert(0, str(SOUL))
        from cli.inference import build_model, process
        from soulxsinger.utils.file_utils import load_config

        self.soul_process = process
        self.soul_config = load_config(
            str(SOUL / "soulxsinger/config/soulxsinger.yaml")
        )
        self.soul_config.infer.n_steps = 32
        self.soul_config.infer.cfg = 3.0
        self.soul_model = build_model(
            str(path("soul_models") / "model.pt"),
            self.soul_config,
            "cuda",
            use_fp16=True,
        )
        self.load_seconds = time.perf_counter() - started

    def generate(self, score: Path, output: Path):
        """Return the actual complete-request duration after loading the models."""
        started = time.perf_counter()
        self.soul_model.to("cuda")
        self.torch.manual_seed(42)
        self.torch.cuda.reset_peak_memory_stats()
        performance = output / "performance"
        performance.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(score, performance / "score.json")
        if score.with_name("notes.json").exists():
            shutil.copyfile(score.with_name("notes.json"), performance / "notes.json")
        call = SimpleNamespace(
            device="cuda",
            prompt_wav_path=str(SOUL / "example/audio/zh_prompt.mp3"),
            prompt_metadata_path=str(SOUL / "example/audio/zh_prompt.json"),
            target_metadata_path=str(performance / "score.json"),
            phoneset_path=str(SOUL / "soulxsinger/utils/phoneme/phone_set.json"),
            save_dir=str(performance),
            control="score",
            auto_shift=False,
            pitch_shift=0,
            use_fp16=True,
        )
        self.soul_process(call, self.soul_config, self.soul_model)
        self.torch.cuda.synchronize()
        svs_seconds = time.perf_counter() - started
        self.soul_model.to("cpu")
        self.torch.cuda.empty_cache()
        source = performance / "generated.wav"
        convert_started = time.perf_counter()
        self.torch.manual_seed(42)
        self.seed.main(
            SimpleNamespace(
                source=str(source), output=str(output / "converted"), **self.seed_args
            )
        )
        self.torch.cuda.synchronize()
        path = next((output / "converted").glob("vc_*.wav"))
        wave, rate = self.sf.read(path)
        conversion_seconds = time.perf_counter() - convert_started
        elapsed = time.perf_counter() - started
        report = {
            "engine": "SoulX-Singer-SVS + Seed-VC 44.1k",
            "model_reuse": True,
            "reference": self.seed_args["target"],
            "checkpoint": self.seed_args["checkpoint"],
            "elapsed_seconds": round(elapsed, 3),
            "svs_seconds": round(svs_seconds, 3),
            "conversion_seconds": round(conversion_seconds, 3),
            "gpu_peak_gib": round(self.torch.cuda.max_memory_allocated() / 1024**3, 3),
            "samples": {
                "custom": {
                    "source": str(source),
                    "output": str(path),
                    "audio_seconds": len(wave) / rate,
                }
            },
            "evaluation": "Technical trial; perceptual quality not established.",
        }
        (output / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--score", type=Path, default=SOUL / "original-score-a/score.json"
    )
    parser.add_argument("--label", default="resident-benchmark")
    parser.add_argument("--requests", type=int, default=3)
    args = parser.parse_args()
    total_started = time.perf_counter()
    engine = SingingPipeline()
    reports = [
        engine.generate(args.score, ROOT / args.label / f"request-{number + 1}")
        for number in range(args.requests)
    ]
    report = {
        "load_seconds": round(engine.load_seconds, 3),
        "requests": reports,
        "process_seconds": round(time.perf_counter() - total_started, 3),
    }
    (ROOT / args.label / "benchmark.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "load_seconds": report["load_seconds"],
                "request_seconds": [r["elapsed_seconds"] for r in reports],
                "process_seconds": report["process_seconds"],
            }
        ),
        flush=True,
    )
