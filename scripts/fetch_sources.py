"""Download the source documents listed in sources/manifest.json.

    python3 scripts/fetch_sources.py

Files land in sources/ (git-ignored). A file already present is skipped, and
each SHA-256 is recorded in sources/checksums.json so a changed document shows.
curl is used because some state sites send an incomplete certificate chain
that curl completes from the system store and Python rejects.
"""
import hashlib
import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"


def main() -> None:
    manifest = json.loads((SOURCES / "manifest.json").read_text())
    sums_path = SOURCES / "checksums.json"
    sums = json.loads(sums_path.read_text()) if sums_path.exists() else {}
    for doc in manifest["documents"]:
        target = SOURCES / doc["file"]
        if not target.exists():
            data = subprocess.run(
                ["curl", "-fsSL", "--compressed", "--max-time", "120", "-A", "Mozilla/5.0 (procurement-rules-navigator; public research)", doc["url"]],
                check=True, capture_output=True,
            ).stdout
            if doc["format"] == "pdf" and not data.startswith(b"%PDF"):
                raise SystemExit(f"{doc['id']}: response is not a PDF")
            target.write_bytes(data)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if doc["id"] in sums and sums[doc["id"]] != digest:
            print(f"{doc['id']}: changed since the last fetch")
        sums[doc["id"]] = digest
        print(f"{doc['id']:30} {target.stat().st_size // 1024:>6} KB")
    sums_path.write_text(json.dumps(sums, indent=2) + "\n")


if __name__ == "__main__":
    main()
