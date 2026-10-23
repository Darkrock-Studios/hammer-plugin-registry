"""Release packages: fetching them and writing new pins into plugin.toml."""

import hashlib
import json

from . import manifest
from .fetch import AssetCache
from .schema import Plugin, Release


def load_manifest(plugin: Plugin, release: Release, cache: AssetCache) -> dict:
    """Downloads (or reads from the cache) a pinned release, verifies it, and returns its manifest summary.
    Raises FetchError or PackageError."""
    package = cache.fetch(release.url, release.sha256, release.size)
    return manifest.load_release(package, plugin.id, release.version)


def load_manifests(
    plugin: Plugin, cache: AssetCache, where: str, versions: set[str] | None = None
) -> tuple[dict[str, dict], list[str]]:
    """Manifest summaries of the plugin's releases, or of only `versions` when given."""
    summaries, errors = {}, []
    for release in plugin.sorted_releases():
        if versions is not None and release.version not in versions:
            continue
        try:
            summaries[release.version] = load_manifest(plugin, release, cache)
        except Exception as e:  # FetchError, PackageError
            errors.append(f"{where}: release {release.version}: {e}")
    return summaries, errors


def _toml_string(value: str) -> str:
    # JSON's string escapes are a subset of TOML's basic string escapes.
    return json.dumps(value)


def release_block(release: Release) -> str:
    lines = [
        "[[releases]]",
        f"version = {_toml_string(release.version)}",
        f"url = {_toml_string(release.url)}",
        f"sha256 = {_toml_string(release.sha256)}",
        f"size = {release.size}",
        f"published = {release.published_iso}",
        f"yanked = {'true' if release.yanked else 'false'}",
    ]
    if release.yank_reason:
        lines.append(f"yank_reason = {_toml_string(release.yank_reason)}")
    return "\n".join(lines) + "\n"


def append_releases(text: str, releases: list[Release]) -> str:
    """Appends [[releases]] tables. An array-of-tables entry at the end of a file is always valid TOML,
    whatever comes before it."""
    if text and not text.endswith("\n"):
        text += "\n"
    for release in releases:
        text += "\n" + release_block(release)
    return text


def git_blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
