"""Download the pinned runtime assets; model weights stay outside Git."""

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.runtime import CONFIG, path  # noqa: E402


def selected_models(root: Path, only: str | None, engines: set[str]) -> list[dict]:
    items = json.loads((root / "models.manifest.json").read_text(encoding="utf-8"))
    for manifest in sorted((root / "models-manifests").glob("*.json")):
        items.extend(json.loads(manifest.read_text(encoding="utf-8")))
    if only and only not in {item["component"] for item in items}:
        raise ValueError(f"Unknown model component: {only}")
    engine_components = {
        "cosy_models": "cosy",
        "index_models": "index",
        "index_aux": "index",
        "fish_models": "fish",
    }
    return [
        item
        for item in items
        if (
            item["component"] == only
            if only
            else engine_components.get(item["component"]) in engines | {None}
        )
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only", help="One component, for example index_models or recognition_models"
    )
    parser.add_argument(
        "--verify", action="store_true", help="Only verify installed files"
    )
    parser.add_argument(
        "--include-cosy",
        action="store_true",
        help="Also install CosyVoice3 when another speech engine is selected",
    )
    parser.add_argument(
        "--include-index",
        action="store_true",
        help="Also install IndexTTS-2 when another speech engine is selected",
    )
    parser.add_argument(
        "--include-fish",
        action="store_true",
        help="Also install pinned Fish S2 weights",
    )
    args = parser.parse_args()
    engines = {CONFIG["speech_engine"]}
    engines.update(
        engine
        for engine in ("cosy", "index", "fish")
        if getattr(args, "include_" + engine)
    )
    items = selected_models(ROOT, args.only, engines)
    proxy = CONFIG["download_proxy"]
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy} if proxy else {})
    )
    for item in items:
        target = path(item["component"]) / item["file"]
        if target.exists():
            with target.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest == item["sha256"]:
                print("Ready: " + str(target), flush=True)
                continue
        if args.verify:
            raise RuntimeError("Missing or different model: " + str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".download")
        print("Downloading: " + item["component"] + "/" + item["file"], flush=True)
        if item.get("source_file"):
            shutil.copyfile(ROOT / item["source_file"], partial)
            with partial.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256")
        else:
            digest = hashlib.sha256()
            with (
                opener.open(item["url"], timeout=120) as response,
                partial.open("wb") as output,
            ):
                while chunk := response.read(4 * 1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise RuntimeError(
                "Downloaded model differs from the pinned release: " + item["file"]
            )
        partial.replace(target)
    print("Requested models ready.")


if __name__ == "__main__":
    main()
