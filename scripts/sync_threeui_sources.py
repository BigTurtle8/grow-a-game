from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / "frontend"
BUNDLES = (
    "https://threeui.com/source-code/crt.json",
    "https://threeui.com/source-code/get-started-button.json",
)


def main() -> None:
    written: dict[str, str] = {}
    for url in BUNDLES:
        response = httpx.get(url, timeout=30, follow_redirects=True)
        response.raise_for_status()
        bundle = response.json()
        for item in bundle["files"]:
            path = item["path"]
            source = item["code"]
            digest = hashlib.sha256(source.encode()).hexdigest()
            if digest != item["sha256"]:
                raise RuntimeError(
                    f"Hash mismatch for {path}: expected {item['sha256']}, got {digest}"
                )
            previous = written.get(path)
            if previous and previous != digest:
                raise RuntimeError(f"Conflicting registered sources for {path}")
            written[path] = digest
            target = DESTINATION / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source)
            print(f"{digest}  {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
