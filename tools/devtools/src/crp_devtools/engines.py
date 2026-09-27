"""Install pinned analysis engines into ``.local/engines`` (trusted tooling, not customer data).

PMD is downloaded from its official GitHub release and verified against a pinned SHA-256 before
extraction. The ESLint analyzer is the pinned pnpm workspace package ``engines/eslint-runner``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from crp_devtools.infra import InfraError


@dataclass(frozen=True, slots=True)
class PmdRelease:
    version: str
    url: str
    sha256: str

    @property
    def directory(self) -> str:
        return f"pmd-bin-{self.version}"


# Pinned 2026-09-26: 7.27.0 (released 2026-08-28). 7.28.0 was one day old and not adopted yet.
PMD = PmdRelease(
    version="7.27.0",
    url="https://github.com/pmd/pmd/releases/download/pmd_releases%2F7.27.0/pmd-dist-7.27.0-bin.zip",
    sha256="4ae396ffaf2b0d3ef0b73a10b2925e77066f73d57a4ce9078c60e7302bcddec9",
)


def pmd_home(engines_dir: Path) -> Path:
    return engines_dir / PMD.directory


def install_pmd(engines_dir: Path) -> Path:
    home = pmd_home(engines_dir)
    if (home / "bin" / "pmd").is_file():
        return home
    engines_dir.mkdir(parents=True, exist_ok=True)
    archive = engines_dir / f"pmd-dist-{PMD.version}-bin.zip.part"
    digest = hashlib.sha256()
    with httpx.stream("GET", PMD.url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        with archive.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                digest.update(chunk)
                handle.write(chunk)
    if digest.hexdigest() != PMD.sha256:
        archive.unlink(missing_ok=True)
        raise InfraError(
            f"PMD {PMD.version} checksum mismatch: expected {PMD.sha256}, got {digest.hexdigest()}"
        )
    staging = engines_dir / f".{PMD.directory}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            target = (staging / info.filename).resolve()
            if staging.resolve() not in target.parents and target != staging.resolve():
                raise InfraError(f"unexpected path in PMD archive: {info.filename}")
        zf.extractall(staging)  # noqa: S202 - every member path was validated above
    extracted = staging / PMD.directory
    (extracted / "bin" / "pmd").chmod(0o755)
    shutil.rmtree(home, ignore_errors=True)
    extracted.rename(home)
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink(missing_ok=True)
    return home


# ---------------------------------------------------------------------------------------------
# Opengrep and Trivy (adopted 2026-09-26, ADR 0007). Signatures were verified with cosign at
# adoption time; the SHA-256 pins below guarantee that later installs get the same bytes.


@dataclass(frozen=True, slots=True)
class BinaryRelease:
    name: str
    version: str
    url: str
    sha256: str
    signer_identity: str
    signature_url: str | None = None
    certificate_url: str | None = None
    bundle_url: str | None = None


OPENGREP = BinaryRelease(
    name="opengrep",
    version="1.30.0",
    url="https://github.com/opengrep/opengrep/releases/download/v1.30.0/opengrep_osx_arm64",
    sha256="0f5bc3dec09d995c61331a4017b856ede508f90d95b018d95f1dc6166be89fdd",
    signer_identity="https://github.com/opengrep/opengrep/.github/workflows/rolling-release.yml@refs/heads/main",
    signature_url="https://github.com/opengrep/opengrep/releases/download/v1.30.0/opengrep_osx_arm64.sig",
    certificate_url="https://github.com/opengrep/opengrep/releases/download/v1.30.0/opengrep_osx_arm64.cert",
)

# 0.69.3 is the release Aqua listed as verified-safe after the March 2026 compromise
# (GHSA-69fq-xp46-6x23); its Rekor timestamp (2026-03-03) predates the 2026-03-19 attack.
TRIVY = BinaryRelease(
    name="trivy",
    version="0.69.3",
    url="https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_macOS-ARM64.tar.gz",
    sha256="a2f2179afd4f8bb265ca3c7aefb56a666bc4a9a411663bc0f22c3549fbc643a5",
    signer_identity="https://github.com/aquasecurity/trivy/.github/workflows/reusable-release.yaml@refs/tags/v0.69.3",
    bundle_url="https://github.com/aquasecurity/trivy/releases/download/v0.69.3/trivy_0.69.3_macOS-ARM64.tar.gz.sigstore.json",
)


def _download(url: str, target: Path, expected_sha256: str | None) -> None:
    digest = hashlib.sha256()
    part = target.with_suffix(target.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=300) as response:
        response.raise_for_status()
        with part.open("wb") as handle:
            for chunk in response.iter_bytes(1024 * 1024):
                digest.update(chunk)
                handle.write(chunk)
    if expected_sha256 is not None and digest.hexdigest() != expected_sha256:
        part.unlink(missing_ok=True)
        raise InfraError(
            f"checksum mismatch for {url}: expected {expected_sha256}, got {digest.hexdigest()}"
        )
    part.rename(target)


def _cosign_verify(release: BinaryRelease, artifact: Path, staging: Path) -> str:
    cosign = shutil.which("cosign")
    if cosign is None:
        return "skipped (cosign not installed; SHA-256 pin enforced)"
    args = [
        cosign,
        "verify-blob",
        str(artifact),
        "--certificate-identity",
        release.signer_identity,
        "--certificate-oidc-issuer",
        "https://token.actions.githubusercontent.com",
    ]
    if release.bundle_url:
        bundle = staging / "bundle.json"
        _download(release.bundle_url, bundle, None)
        args += ["--bundle", str(bundle)]
    elif release.signature_url and release.certificate_url:
        sig, cert = staging / "artifact.sig", staging / "artifact.cert"
        _download(release.signature_url, sig, None)
        _download(release.certificate_url, cert, None)
        args += ["--signature", str(sig), "--certificate", str(cert)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
    if result.returncode != 0:
        raise InfraError(
            f"cosign verification failed for {release.name}: {result.stderr.strip()[-400:]}"
        )
    return "verified with cosign"


def install_binary(
    engines_dir: Path, release: BinaryRelease, *, verify_signatures: bool
) -> tuple[Path, str]:
    home = engines_dir / f"{release.name}-{release.version}"
    binary = home / release.name
    if binary.is_file() and (home / "VERSION").is_file():
        return home, "already installed"
    staging = engines_dir / f".{release.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    artifact = staging / release.url.rsplit("/", 1)[-1]
    try:
        _download(release.url, artifact, release.sha256)
        status = (
            _cosign_verify(release, artifact, staging) if verify_signatures else "sha256 verified"
        )
        home.mkdir(parents=True, exist_ok=True)
        if artifact.name.endswith(".tar.gz"):
            with tarfile.open(artifact) as archive:
                member = archive.getmember(release.name)
                if not member.isfile():
                    raise InfraError(f"unexpected {release.name} archive layout")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise InfraError(f"cannot read {release.name} from archive")
                binary.write_bytes(extracted.read())
        else:
            shutil.copyfile(artifact, binary)
        binary.chmod(0o755)
        (home / "VERSION").write_text(release.version + "\n")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return home, status


def warm_opengrep(home: Path) -> None:
    """Unpack the self-extracting binary once into ``<home>/cache`` (used via XDG_CACHE_HOME)."""
    temp = home / ".warm-home"
    temp.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(temp),
        "XDG_CACHE_HOME": str(home / "cache"),
        "TMPDIR": str(temp),
    }
    subprocess.run(  # noqa: S603 - fixed, checksum-verified executable
        [str(home / "opengrep"), "--version"], env=env, capture_output=True, timeout=600, check=True
    )
    shutil.rmtree(temp, ignore_errors=True)


def download_trivy_db(home: Path, cache_dir: Path) -> dict[str, object]:
    """Fetch the vulnerability DB (data only) for offline scans; returns its metadata."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp = cache_dir / ".home"
    temp.mkdir(exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(temp), "TMPDIR": str(temp)}
    result = subprocess.run(  # noqa: S603
        [
            str(home / "trivy"),
            "--cache-dir",
            str(cache_dir),
            "image",
            "--download-db-only",
            "--no-progress",
            "--disable-telemetry",
            "--skip-version-check",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    shutil.rmtree(temp, ignore_errors=True)
    if result.returncode != 0:
        raise InfraError(f"Trivy DB download failed: {result.stderr.strip()[-400:]}")
    metadata: dict[str, object] = json.loads((cache_dir / "db" / "metadata.json").read_text())
    return metadata
