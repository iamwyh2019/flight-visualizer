"""Download the adsb.lol daily archive from GitHub Releases and extract one trace.

The archives are GitHub Release assets, one release per UTC day, split into tar
parts (`...prod-0.tar.aa`, `.tar.ab`, ...). For Stage 1 Step 1 we use the
"full download, then delete" strategy:

  1. Resolve the release for the target UTC date (prefer prod, fall back staging).
  2. Stream-download every tar part to a temp dir (reporting byte progress).
  3. Read the concatenated parts as a single non-seekable tar stream and extract
     ONLY the member ending in `trace_full_{icao24}.json` (never untar the world).
  4. Delete the raw multi-GB parts; keep only the tiny trace file.

An optional GITHUB_TOKEN raises the API rate limit (60 -> 5000 req/hr); not required.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import BinaryIO, Callable

import httpx

EmitFn = Callable[..., None]

_API = "https://api.github.com"


class Cancelled(Exception):
    """Raised when a run is aborted (e.g. the web client disconnected)."""


def _repo_for_year(year: int) -> str:
    return f"adsblol/globe_history_{year}"


def _tag(d: date, channel: str) -> str:
    return f"v{d.year:04d}.{d.month:02d}.{d.day:02d}-planes-readsb-{channel}-0"


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


class _MultiPartReader:
    """A minimal read-only, non-seekable file object that concatenates files."""

    def __init__(self, paths: list[Path]):
        self._paths = paths
        self._idx = 0
        self._fh: BinaryIO | None = open(paths[0], "rb") if paths else None

    def read(self, size: int = -1) -> bytes:
        if self._fh is None:
            return b""
        if size is None or size < 0:
            chunks = []
            while True:
                c = self.read(1024 * 1024)
                if not c:
                    break
                chunks.append(c)
            return b"".join(chunks)
        buf = b""
        while len(buf) < size and self._fh is not None:
            chunk = self._fh.read(size - len(buf))
            if chunk:
                buf += chunk
            else:
                self._fh.close()
                self._idx += 1
                self._fh = (
                    open(self._paths[self._idx], "rb")
                    if self._idx < len(self._paths)
                    else None
                )
        return buf

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None


def _tar_assets(release: dict) -> list[dict]:
    # Matches both split parts (".tar.aa"/".tar.ab", 2024+) and a single ".tar"
    # asset (2023 archives). Sorted so split parts reassemble in order.
    return sorted(
        (a for a in release.get("assets", []) if ".tar" in a["name"]),
        key=lambda a: a["name"],
    )


def _resolve_release(client: httpx.Client, repo: str, d: date, emit: EmitFn) -> dict:
    """Return a release for the date that actually has tar assets, prod then staging.

    A prod release that exists but carries no tar parts (a known adsb.lol data gap)
    is treated as a miss so we fall back to staging instead of giving up.
    """
    for channel in ("prod", "staging"):
        tag = _tag(d, channel)
        url = f"{_API}/repos/{repo}/releases/tags/{tag}"
        resp = client.get(url, headers=_headers())
        if resp.status_code == 200:
            release = resp.json()
            if _tar_assets(release):
                emit("log", message=f"Matched release {tag}")
                return release
            emit("log", message=f"{channel} release {tag} has no tar assets, trying next")
            continue
        if resp.status_code == 404:
            emit("log", message=f"No {channel} release for {d.isoformat()} ({tag})")
            continue
        resp.raise_for_status()
    raise FileNotFoundError(f"No usable prod or staging archive for {d.isoformat()} in {repo}")


def fetch_traces(
    d: date,
    icao24s: list[str],
    work_dir: str | Path,
    emit: EmitFn,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[str, bytes]:
    """Download the day's archive ONCE and extract every requested aircraft's trace.

    All same-day aircraft are pulled in a single tar scan, so N flights on one day
    cost one download instead of N. Returns {icao24_lower: raw_bytes}; aircraft not
    present in the archive are simply absent from the result (logged by the caller).

    Raises FileNotFoundError if the release for the date doesn't exist at all.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    repo = _repo_for_year(d.year)
    # Map each target file suffix -> its icao24 so one scan can collect them all.
    targets = {f"trace_full_{i.lower()}.json": i.lower() for i in icao24s}
    part_paths: list[Path] = []

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        release = _resolve_release(client, repo, d, emit)
        assets = _tar_assets(release)
        if not assets:
            raise FileNotFoundError(f"Release {release.get('tag_name')} has no tar assets")

        total_bytes = sum(a["size"] for a in assets)
        emit(
            "log",
            message=f"Downloading {len(assets)} part(s), "
            f"{total_bytes / 1e9:.2f} GB total",
        )

        downloaded = 0
        try:
            for asset in assets:
                dest = work_dir / asset["name"]
                part_paths.append(dest)
                with client.stream(
                    "GET", asset["browser_download_url"], headers=_headers()
                ) as resp:
                    resp.raise_for_status()
                    with open(dest, "wb") as out:
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            if cancelled():
                                raise Cancelled()
                            out.write(chunk)
                            downloaded += len(chunk)
                            emit(
                                "progress",
                                downloaded=downloaded,
                                total=total_bytes,
                                phase="download",
                            )

            emit(
                "log",
                message=f"Download complete; scanning tar for {len(targets)} trace(s)...",
            )
            found = _extract_members(part_paths, targets, emit)
        finally:
            # Always delete the multi-GB raw parts.
            for p in part_paths:
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            emit("log", message="Deleted raw archive parts")

    return found


def _extract_members(
    part_paths: list[Path], targets: dict[str, str], emit: EmitFn
) -> dict[str, bytes]:
    """Stream the tar once and collect every matching member; stop when all found."""
    import tarfile

    remaining = dict(targets)  # suffix -> icao
    found: dict[str, bytes] = {}
    reader = _MultiPartReader(part_paths)
    scanned = 0
    try:
        # mode 'r|' = streaming, non-seekable, uncompressed tar.
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if not remaining:
                    break  # all targets collected; no need to scan further
                scanned += 1
                if scanned % 50000 == 0:
                    emit("log", message=f"Scanned {scanned} archive members...")
                if not member.isfile():
                    continue
                for suffix in list(remaining):
                    if member.name.endswith(suffix):
                        icao = remaining.pop(suffix)
                        emit("log", message=f"Found trace: {member.name}")
                        f = tar.extractfile(member)
                        if f is not None:
                            found[icao] = f.read()
                        break
    finally:
        reader.close()
    return found
