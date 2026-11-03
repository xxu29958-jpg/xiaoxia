"""Prepare the exact Fish source used by the configured local speech worker."""

import argparse
import json
import os
from pathlib import Path

from .sources import archive_files, fetch_archive, install_source


def prepare(root: Path, archive: Path | None = None) -> dict:
    specifications = json.loads(
        (root / "vendor/sources.json").read_text(encoding="utf-8")
    )
    specification = specifications["fish_speech"]
    configuration = Path(os.environ.get("XIAOXIA_CONFIG", root / "xiaoxia.local.json"))
    local = (
        json.loads(configuration.read_text(encoding="utf-8-sig"))
        if configuration.exists()
        else {}
    )
    target = root / local.get("fish_repo", specification["install_path"])
    data = archive.read_bytes() if archive else fetch_archive(specification)
    files = archive_files(data, specification)
    status = install_source(
        target,
        files,
        allow_create=target.resolve().is_relative_to(
            (root / ".runtime/sources").resolve()
        ),
    )
    return {
        "status": status,
        "revision": specification["revision"],
        "files": len(files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, help="Use an offline copy of the exact pinned archive"
    )
    args = parser.parse_args()
    print(json.dumps(prepare(Path(__file__).resolve().parents[2], args.archive)))


if __name__ == "__main__":
    main()
