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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--only", help="One component, for example index_models or recognition_models"
    )
    parser.add_argument(
        "--verify", action="store_true", help="Only verify installed files"
    )
    args = parser.parse_args()
    items = json.loads((ROOT / "models.manifest.json").read_text(encoding="utf-8"))
    proxy = CONFIG["download_proxy"]
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": proxy} if proxy else {})
    )
    for item in items:
        if args.only and item["component"] != args.only:
            continue
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
