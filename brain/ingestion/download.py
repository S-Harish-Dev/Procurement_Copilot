"""Download the public corpus listed in data/manifest.json.

First download of a source records its sha256/size/date in the manifest (commit
that change). Later downloads must match the recorded checksum, so an upstream
edit (the FAR is being rewritten) is detected instead of silently changing evals.

    uv run python -m ingestion.download [--refresh <id>]
"""

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime

import httpx

from common.settings import get_settings

USER_AGENT = "procurement-copilot-corpus-fetch/0.1 (local research build)"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refresh", action="append", default=[], help="source id whose recorded checksum may be replaced"
    )
    args = parser.parse_args()

    data_dir = get_settings().data_dir
    manifest_path = data_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    failed = False

    with httpx.Client(follow_redirects=True, timeout=120, headers={"User-Agent": USER_AGENT}) as client:
        for src in manifest["sources"]:
            dest = data_dir / "raw" / src["id"] / src["file"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                print(f"downloading {src['id']} …")
                resp = client.get(src["url"])
                resp.raise_for_status()
                dest.write_bytes(resp.content)

            digest = hashlib.sha256(dest.read_bytes()).hexdigest()
            if src["sha256"] is None or src["id"] in args.refresh:
                src.update(
                    sha256=digest,
                    bytes=dest.stat().st_size,
                    retrieved_at=datetime.now(UTC).date().isoformat(),
                )
                print(f"recorded {src['id']}: {digest[:12]}… ({src['bytes']:,} bytes)")
            elif src["sha256"] != digest:
                print(
                    f"CHECKSUM MISMATCH {src['id']}: manifest {src['sha256'][:12]}… got {digest[:12]}…; "
                    f"re-run with --refresh {src['id']} if the upstream change is intended",
                    file=sys.stderr,
                )
                failed = True
            else:
                print(f"ok {src['id']}")

    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
