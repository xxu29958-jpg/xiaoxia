"""Load the singing converter from local, separately installed model assets."""

import os
import random
import sys

from services.runtime import path

ROOT = path("voice_state")
REPO = path("seed_repo")
MODELS = path("seed_models")
BASE = MODELS / "seed/DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"


def configure():
    """Bind Seed-VC's asset loader to the installed checkpoint directories."""
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    import hf_utils
    import numpy as np
    import torch
    import yaml

    assets = {
        ("funasr/campplus", "campplus_cn_common.bin"): MODELS
        / "common/campplus_cn_common.bin",
        ("lj1995/VoiceConversionWebUI", "rmvpe.pt"): MODELS / "common/rmvpe.pt",
    }

    def local_asset(repo_id, model_filename="pytorch_model.bin", config_filename=None):
        model = str(assets[repo_id, model_filename])
        return (
            model
            if config_filename is None
            else (model, str(assets[repo_id, config_filename]))
        )

    hf_utils.load_custom_model_from_hf = local_asset
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    torch.set_num_threads(8)
    config = yaml.safe_load(
        (MODELS / "seed/config_dit_mel_seed_uvit_whisper_base_f0_44k.yml").read_text()
    )
    config["model_params"]["vocoder"]["name"] = (MODELS / "bigvgan").as_posix()
    config["model_params"]["speech_tokenizer"]["name"] = (MODELS / "whisper").as_posix()
    config["log_dir"] = (ROOT / "runs").as_posix()
    target = ROOT / "seed-config.yml"
    target.write_text(yaml.safe_dump(config), encoding="utf-8")
    return target
