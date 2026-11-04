"""TypeScript/Vue compiler facts from supplied text; application source stays inert."""

import json
import subprocess
from pathlib import Path

EXTENSIONS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue"}


def analyze(sources: dict[str, bytes], resources: list[str]) -> dict:
    if not sources:
        return {}
    helper = Path(__file__).with_name("frontend_ast.cjs")
    result = subprocess.run(
        ["node", str(helper)],
        input=json.dumps(
            {
                "sources": {p: b.decode("utf-8-sig") for p, b in sources.items()},
                "resources": resources,
            }
        ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"Frontend map failed: {result.stderr}")
    return json.loads(result.stdout)
