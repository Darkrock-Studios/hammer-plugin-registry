"""Downloading release assets, with a cache keyed by sha256."""

import hashlib
import urllib.request
from pathlib import Path
from typing import Callable

USER_AGENT = "hammer-plugin-registry"
# The largest package Hammer accepts: plugin.wasm at the 1024 MiB memory limit plus 32 MiB, and 64 MiB of
# resources, with room for the rest.
MAX_PACKAGE_BYTES = 1200 * 1024 * 1024

Downloader = Callable[[str, int], bytes]


class FetchError(Exception):
    pass


def http_download(url: str, max_bytes: int) -> bytes:
    """Downloads without credentials: release assets of public repositories need none, and GitHub
    redirects them to another host."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read(max_bytes + 1)
    except OSError as e:
        raise FetchError(f"{url}: {e}") from None
    if len(data) > max_bytes:
        raise FetchError(f"{url}: larger than {max_bytes} bytes")
    return data


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AssetCache:
    def __init__(self, directory: Path | None, downloader: Downloader = http_download):
        self.directory = directory
        self.downloader = downloader

    def _cached(self, sha256: str) -> bytes | None:
        if self.directory is None:
            return None
        path = self.directory / sha256
        if path.is_file():
            data = path.read_bytes()
            if sha256_of(data) == sha256:
                return data
            path.unlink()
        return None

    def _store(self, data: bytes) -> str:
        digest = sha256_of(data)
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / digest).write_bytes(data)
        return digest

    def fetch(self, url: str, sha256: str, size: int) -> bytes:
        """A pinned asset, checked against its sha256 and size."""
        cached = self._cached(sha256)
        if cached is not None and len(cached) == size:
            return cached
        data = self.downloader(url, max(size, 0))
        if len(data) != size:
            raise FetchError(f"{url}: size is {len(data)}, pinned {size}")
        digest = sha256_of(data)
        if digest != sha256:
            raise FetchError(f"{url}: sha256 is {digest}, pinned {sha256}")
        self._store(data)
        return data

    def fetch_new(self, url: str, max_bytes: int = MAX_PACKAGE_BYTES) -> tuple[bytes, str]:
        """An asset not pinned yet. Returns its bytes and sha256."""
        data = self.downloader(url, max_bytes)
        return data, self._store(data)
