"""Download the pinned Temporal CLI for Linux amd64 and verify its SHA-256 (image build step)."""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import urllib.request


def main() -> int:
    version, expected, target = sys.argv[1], sys.argv[2], sys.argv[3]
    url = (
        f"https://github.com/temporalio/cli/releases/download/v{version}/"
        f"temporal_cli_{version}_linux_amd64.tar.gz"
    )
    with urllib.request.urlopen(url, timeout=300) as response:
        data = response.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        print(f"temporal checksum mismatch: expected {expected}, got {digest}", file=sys.stderr)
        return 1
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        member = archive.extractfile(archive.getmember("temporal"))
        if member is None:
            print("temporal binary missing from archive", file=sys.stderr)
            return 1
        with open(target, "wb") as out:  # noqa: PTH123 - minimal build script
            out.write(member.read())
    os.chmod(target, 0o755)  # noqa: PTH101, S103 - executable binary, root-owned image path
    return 0


if __name__ == "__main__":
    sys.exit(main())
