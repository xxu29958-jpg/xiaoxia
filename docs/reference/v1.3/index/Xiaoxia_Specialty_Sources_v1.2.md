# 小夏专项来源索引 v1.2

日期：2026-11-04。正式论文、技术报告、模型卡、官方实现与仓库静态证据分别列出；不把来源条目数当成论文数。

## 演唱／戏曲正式会议论文

### S01 · FT-GAN: Fine-Grained Tune Modeling for Chinese Opera Synthesis

- 类型／版本：AAAI 2024, 38(17), 19697–19705
- [原文／官方资料](https://ojs.aaai.org/index.php/AAAI/article/download/29943/31649#page=1)
- [正式出版记录](https://ojs.aaai.org/index.php/AAAI/article/view/29943)
- 已读位置：Dataset Construction；Method: Conditioner, Mel Predictor, Discriminator, Losses, Speech Pre-Training；Experiments；Extensive Experiments；Tables 1–4
- 复核：Table 3, w/o Vowel Morphing row, Pro column: pronunciation CMOS -0.43 relative to full model; not naturalness MOS.

### S02 · Improving Chinese Pop Song and Hokkien Gezi Opera Singing Voice Synthesis by Enhancing Local Modeling

- 类型／版本：EMNLP 2023 main, 3302–3312
- [原文／官方资料](https://aclanthology.org/2023.emnlp-main.200.pdf)
- [正式出版记录](https://aclanthology.org/2023.emnlp-main.200/)
- 已读位置：2.1–2.3 Methods；3 Experiments；4.1–4.4 Results；Limitations；Tables 1–4

### S03 · Peking Opera Synthesis via Duration Informed Attention Network

- 类型／版本：Interspeech 2020, 1226–1230
- [原文／官方资料](https://arxiv.org/pdf/2008.03029)
- [正式出版记录](https://www.isca-archive.org/interspeech_2020/wu20d_interspeech.html)
- 已读位置：2.1–2.3 Methods；3.1 Data；3.2 Duration Prediction；3.3.1–3.3.2 Opera Synthesis；4 Conclusions；Table 1

### S04 · Towards Chinese Yue Opera Singing Voice Synthesis: A Benchmark with Dataset, Data Augmentation and Baseline Model

- 类型／版本：Interspeech 2026, 1596–1600
- [原文／官方资料](https://www.isca-archive.org/interspeech_2026/bai26_interspeech.pdf)
- [正式出版记录](https://www.isca-archive.org/interspeech_2026/bai26_interspeech.html)
- 已读位置：2.1–2.3 YOAT Dataset and Augmentation；3.1–3.2 YueOpera-Singer；4.1–4.4 Experiments；5.1–5.3 Results and Analysis；Tables 1–3

### S05 · TCSinger: Zero-Shot Singing Voice Synthesis with Style Transfer and Multi-Level Style Control

- 类型／版本：EMNLP 2024 main, 1960–1975
- [原文／官方资料](https://aclanthology.org/2024.emnlp-main.117.pdf)
- [正式出版记录](https://aclanthology.org/2024.emnlp-main.117/)
- 已读位置：3.1–3.5 Methods；4.1–4.3 Experiments and Ablations；6 Limitations；Appendix A.3–A.7；Appendix B；Appendix C.1；Tables 1–5 and 7
- 复核：Appendix B Table 7 prints total 294h, while dataset rows and Section 4.1 list 36+30+85+85+18=254h. Aggregate omitted from research card because the paper is internally inconsistent; retained dataset names and 40 held-out singers.

### S06 · TechSinger: Technique Controllable Multilingual Singing Voice Synthesis via Flow Matching

- 类型／版本：AAAI 2025, 39(22), 23978–23986, Technical Track on Natural Language Processing I
- [原文／官方资料](https://arxiv.org/pdf/2502.12572)
- [正式出版记录](https://ojs.aaai.org/index.php/AAAI/article/view/34571)
- 已读位置：TechSinger: Overview, FMPP, CFG Postnet, Technique Predictor, Technique Detector, Training and Inference；Experiments: Setup, Main Results, Ablation Study；Tables 1–5
- 复核：Table 4 whole-system F1 0.770 measures the audio-to-phoneme technique detector over six techniques other than rule-determined glissando. Table 3 prompt-to-technique predictor with FLAN-T5-Large has F1 0.818. Official AAAI proceedings record and issue directory confirm formal publication on 2025-04-11.

### S07 · GTSinger: A Global Multi-Technique Singing Corpus with Realistic Music Scores for All Singing Tasks

- 类型／版本：NeurIPS 2024, Datasets and Benchmarks Track
- [原文／官方资料](https://proceedings.neurips.cc/paper_files/paper/2024/file/023d2c1a17cf35b11a0cbb43a0677c91-Paper-Datasets_and_Benchmarks_Track.pdf)
- [正式出版记录](https://papers.nips.cc/paper_files/paper/2024/hash/023d2c1a17cf35b11a0cbb43a0677c91-Abstract-Datasets_and_Benchmarks_Track.html)
- 已读位置：3.1–3.6 Dataset Description；4.1–4.4 Benchmarks；5 Conclusion and Discussion；Tables 2–6

## 专业模型与官方实现

### MB01 · SoulX-Singer: Towards High-Quality Zero-Shot Singing Voice Synthesis

- 类型／版本：2026
- [原文／官方资料](https://arxiv.org/html/2602.07803v1)
- 已读位置：2.2 model architecture, conditioning and inference; 3.1-3.3 evaluation

### MB02 · SoulX-Singer official repository

- 类型／版本：2026
- [原文／官方资料](https://github.com/Soul-AILab/SoulX-Singer)
- 已读位置：Overview; News; Quick Start; Roadmap; License

### MB03 · SoulX-Singer preprocessing and MIDI editing

- 类型／版本：2026
- [原文／官方资料](https://github.com/Soul-AILab/SoulX-Singer/blob/main/preprocess/README.md)
- 已读位置：Full preprocessing workflow, dependencies and MIDI conversion

### MB04 · SoulX-Singer SVC inference example

- 类型／版本：2026
- [原文／官方资料](https://github.com/Soul-AILab/SoulX-Singer/blob/main/example/infer_svc.sh)
- 已读位置：Full script: waveform and F0 inputs; fp16 and pitch flags

### MB05 · SoulX-Singer SVC WebUI guidance

- 类型／版本：2026
- [原文／官方资料](https://github.com/Soul-AILab/SoulX-Singer/blob/main/webui_svc.py)
- 已读位置：Input/reference advice and long-range timbre stability tips, lines 69-82

### MB06 · SoulX-Singer pinned dependencies

- 类型／版本：2026
- [原文／官方资料](https://github.com/Soul-AILab/SoulX-Singer/blob/main/requirements.txt)
- 已读位置：Full dependency file; torch 2.2.0, SageAttention, NeMo

### MB07 · Zero-shot Voice Conversion with Diffusion Transformers

- 类型／版本：2024
- [原文／官方资料](https://arxiv.org/html/2411.09943v1)
- 已读位置：Sections 3.2-3.5; 4.1 model configuration

### MB08 · Seed-VC repository and archive status

- 类型／版本：2025
- [原文／官方资料](https://github.com/Plachtaa/seed-vc)
- 已读位置：Model table, CLI, platform support, realtime benchmark; archived Nov 21 2025

### MB09 · Seed-VC code license

- 类型／版本：2025
- [原文／官方资料](https://github.com/Plachtaa/seed-vc/blob/main/LICENSE)
- 已读位置：GPL-3.0 header and licensing text

### MB10 · Seed-VC official model-card license

- 类型／版本：2025
- [原文／官方资料](https://huggingface.co/Plachta/Seed-VC/blob/main/README.md)
- 已读位置：GPL-3.0 metadata

### MB11 · DiffSinger: Singing Voice Synthesis via Shallow Diffusion Mechanism

- 类型／版本：AAAI 2022
- [原文／官方资料](https://arxiv.org/abs/2105.02446)
- 已读位置：Author README verifies AAAI-2022 and links paper; no claim of full paper reading by this child

### MB12 · OpenVPI DiffSinger

- 类型／版本：current main
- [原文／官方资料](https://github.com/openvpi/DiffSinger)
- 已读位置：Features, deployment links, Apache2.0; BestPractices language/phoneme/data/voicebank configuration

### MB13 · OpenVPI DiffSinger Getting Started

- 类型／版本：current main
- [原文／官方资料](https://github.com/openvpi/DiffSinger/blob/main/docs/GettingStarted.md)
- 已读位置：Full document: DS inference, training and ONNX export

### MB14 · OpenUtau DiffSinger support

- 类型／版本：2025
- [原文／官方资料](https://github.com/openutau/OpenUtau/wiki/DiffSinger-support)
- 已读位置：CPU default, Windows DirectML, pitch/energy/breathiness/tension curves

### MB15 · ACE-Step 1.5: Pushing the Boundaries of Open-Source Music Generation

- 类型／版本：2026
- [原文／官方资料](https://arxiv.org/html/2602.00744v3)
- 已读位置：Sections 3.1-3.4; generation/editing conditional architecture

### MB16 · ACE-Step 1.5 model variants and XL release

- 类型／版本：2026
- [原文／官方资料](https://github.com/ace-step/ACE-Step-1.5)
- 已读位置：News; model zoo; XL date Apr 2 2026; feature differences; MIT

### MB17 · ACE-Step GPU Compatibility Guide

- 类型／版本：current main
- [原文／官方资料](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/GPU_COMPATIBILITY.md)
- 已读位置：Full GPU tier table, offload, quantization, profiling and memory caveats

### MB18 · ACE-Step Installation Guide

- 类型／版本：current main
- [原文／官方资料](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INSTALL.md)
- 已读位置：System requirements; Windows portable CUDA12.8; model tiers

### MB19 · ACE-Step REST API

- 类型／版本：current main
- [原文／官方资料](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/API.md)
- 已读位置：Task submit, polling, artifact retrieval, repaint conditions

### MB20 · ACE-Step 1.5 official model card

- 类型／版本：2026
- [原文／官方资料](https://huggingface.co/ACE-Step/Ace-Step1.5)
- 已读位置：License MIT, checkpoint variants

### MB21 · PyTorch 2.7 Release

- 类型／版本：2025
- [原文／官方资料](https://pytorch.org/blog/pytorch-2-7/)
- 已读位置：Blackwell support and CUDA12.8 prebuilt wheels

## 统一模型与本地候选

### U01 · UniVoice: A Unified Model for Speech and Singing Voice Generation

- 类型／版本：2026 arXiv
- [原文／官方资料](https://arxiv.org/abs/2606.05852)
- 已读位置：§3 conditions, DiT and null melody; §4 benchmark; §5 experiments and ablations; §6 limitations

### U02 · Qwen3-Omni Technical Report

- 类型／版本：2025 arXiv
- [原文／官方资料](https://arxiv.org/html/2509.17765)
- 已读位置：§2.1 overview, §2.4 Talker, streaming inference and experimental setup

### U03 · Qwen3.5-9B official model card

- 类型／版本：2026
- [原文／官方资料](https://huggingface.co/Qwen/Qwen3.5-9B)
- 已读位置：Model overview, serving, context, thinking options and quantization links

## 运行基座官方资料

### F1 · LangGraph persistence

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.langchain.com/oss/python/langgraph/persistence)

### F2 · LangGraph checkpointers

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.langchain.com/oss/python/langgraph/checkpointers)

### F3 · LangGraph fault tolerance

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)

### F4 · Temporal activity idempotency

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.temporal.io/activity-definition)

### F4a · Temporal Windows local dev

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.temporal.io/develop/python/set-up-your-local-python)

### F5 · SQLite WAL

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://www.sqlite.org/wal.html)

### F6 · SQLite Online Backup

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://www.sqlite.org/backup.html)

### F7 · LiveKit voice quickstart

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.livekit.io/agents/start/voice-ai/)

### F8 · LiveKit turns and interruptions

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.livekit.io/agents/logic/turns/)

### F9 · LiveKit local media server

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.livekit.io/transport/self-hosting/local/)

### F10 · Pipecat transports

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.pipecat.ai/pipecat/learn/transports)

### F10a · Pipecat quickstart

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.pipecat.ai/pipecat/get-started/quickstart)

### F10b · Pipecat C++ Windows client

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://github.com/pipecat-ai/pipecat-client-cxx/blob/main/README.md)

### F11 · Pipecat interruptions

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.pipecat.ai/pipecat/fundamentals/interruptions)

### F12 · AstrBot Windows install

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.astrbot.app/deploy/astrbot/package.html)

### F13 · AstrBot OneBot QQ

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.astrbot.app/platform/aiocqhttp.html)

### F14 · AstrBot personal WeChat

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.astrbot.app/platform/weixin_oc.html)

### F14a · AstrBot WeChat official account

- 类型／版本：official_documentation_or_repository
- [原文／官方资料](https://docs.astrbot.app/platform/weixin-official-account.html)

## 项目代码快照

固定 HEAD：`8c63aff5a18fcccd624ad0f29ee70f303e15975b`。

- [GOAL.md](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/GOAL.md)
- [pyproject.toml](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/pyproject.toml)
- [data/plugins/astrbot_plugin_ada_companion/main.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/main.py)
- [data/plugins/astrbot_plugin_ada_companion/shared_core.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/shared_core.py)
- [data/plugins/astrbot_plugin_ada_companion/services.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/services.py)
- [data/plugins/astrbot_plugin_ada_companion/policy.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/policy.py)
- [data/plugins/astrbot_plugin_ada_companion/lifecycle.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/lifecycle.py)
- [data/plugins/astrbot_plugin_ada_companion/local_voice.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/local_voice.py)
- [data/plugins/astrbot_plugin_ada_companion/response_media.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/response_media.py)
- [services/voice/server_singing.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/services/voice/server_singing.py)
- [services/voice/resident_pipeline.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/services/voice/resident_pipeline.py)
- [services/runtime.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/services/runtime.py)
- [docs/voice-api.md](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/docs/voice-api.md)
- [setup.ps1](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/setup.ps1)
- [xiaoxia.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/xiaoxia.py)
- [astrbot/core/config/default.py](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/astrbot/core/config/default.py)
- [data/plugins/astrbot_plugin_ada_companion/_conf_schema.json](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_ada_companion/_conf_schema.json)
- [data/plugins/astrbot_plugin_codex_oauth_plug/_conf_schema.json](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_codex_oauth_plug/_conf_schema.json)
- [data/plugins/astrbot_plugin_codex_oauth_plug/README.md](https://github.com/xxu29958-jpg/xiaoxia/blob/8c63aff5a18fcccd624ad0f29ee70f303e15975b/data/plugins/astrbot_plugin_codex_oauth_plug/README.md)
