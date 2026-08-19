#!/usr/bin/env python
"""Re-download the ApplianceCare source manuals and verify them against the manifest.

The full PDFs are copyrighted and are NOT committed. This script reproduces them
from the manufacturers' official URLs into the git-ignored `.research/manuals/`
directory, and checks each file's SHA-256 against
`data/tau2/domains/appliance_care/manifest.json`.

A hash mismatch is meaningful: the manufacturer revised the document. That does not
automatically invalidate the benchmark, but the extract in `manuals/` and the
`manual_document_code` in `db.toml` must then be re-checked against the new revision
before the benchmark's expectations can be trusted.

    uv run python scripts/fetch_appliance_manuals.py            # fetch + verify
    uv run python scripts/fetch_appliance_manuals.py --verify   # verify only
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "tau2" / "domains" / "appliance_care" / "manifest.json"
DEST = ROOT / ".research" / "manuals"

# Some manufacturer CDNs reject a bare client. This is a plain browser UA plus the
# navigation headers those hosts expect; it is not an attempt to defeat access
# control, and every URL here is a publicly published support document.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_name(source: dict) -> str:
    return f"{source['key']}.pdf"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="verify only; do not download")
    args = ap.parse_args()

    manifest = json.loads(MANIFEST.read_text())
    DEST.mkdir(parents=True, exist_ok=True)

    failures = 0
    for src in manifest["sources"]:
        path = DEST / local_name(src)
        expected = src.get("sha256")

        if not path.exists():
            if args.verify:
                print(f"MISSING  {src['key']} (run without --verify to fetch)")
                failures += 1
                continue
            referer = "https://" + src["manual_url"].split("/")[2] + "/"
            try:
                r = requests.get(
                    src["manual_url"],
                    headers={**HEADERS, "Referer": referer},
                    timeout=120,
                    allow_redirects=True,
                )
            except requests.RequestException as e:
                print(f"ERROR    {src['key']}: {type(e).__name__}")
                failures += 1
                continue
            if r.status_code != 200 or not r.content.startswith(b"%PDF"):
                # Several manufacturer CDNs rate-limit or bot-block. Report it rather
                # than writing an HTML error page to disk as if it were a manual.
                print(
                    f"BLOCKED  {src['key']}: HTTP {r.status_code}, "
                    f"{'not a PDF' if r.status_code == 200 else 'refused'} "
                    "- download it manually from the URL in the manifest"
                )
                failures += 1
                continue
            path.write_bytes(r.content)

        actual = sha256(path)
        if expected and actual != expected:
            print(f"CHANGED  {src['key']}: manufacturer revised the document")
            print(f"         expected {expected}")
            print(f"         actual   {actual}")
            print("         re-check the extract and manual_document_code before trusting it")
            failures += 1
        else:
            print(f"OK       {src['key']}  {src['document_code']}")

    print(
        f"\n{len(manifest['sources']) - failures}/{len(manifest['sources'])} verified. "
        f"Manufacturers: {', '.join(manifest['manufacturers'])}."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
