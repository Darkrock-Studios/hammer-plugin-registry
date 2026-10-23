import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import pytest

from tools.registry.fetch import AssetCache, FetchError

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
GITHUB = "Darkrock-Studios/hammer-plugins"
REPO_ID = 1404820522


def fixture_manifest(name: str, version: str | None = None) -> str:
    text = (FIXTURES / f"{name}.manifest.toml").read_text(encoding="utf-8")
    if version is not None:
        text = text.replace('version = "0.1.0"', f'version = "{version}"', 1).replace(
            'version = "1.0.0"', f'version = "{version}"', 1
        )
    return text


def make_package(manifest_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.toml", manifest_text)
        archive.writestr("plugin.wasm", b"\0asm\x01\0\0\0")
    return buffer.getvalue()


def asset_url(plugin_id: str, version: str) -> str:
    return f"https://github.com/{GITHUB}/releases/download/{plugin_id}-v{version}/{plugin_id}.hammerplugin"


class FakeDownloader:
    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.calls: list[str] = []

    def add(self, url: str, data: bytes) -> None:
        self.files[url] = data

    def __call__(self, url: str, max_bytes: int) -> bytes:
        self.calls.append(url)
        if url not in self.files:
            raise FetchError(f"{url}: 404")
        return self.files[url]


def release_toml(plugin_id: str, version: str, package: bytes, **overrides) -> str:
    fields = {
        "version": f'"{version}"',
        "url": f'"{asset_url(plugin_id, version)}"',
        "sha256": f'"{hashlib.sha256(package).hexdigest()}"',
        "size": str(len(package)),
        "published": "2026-10-22T14:03:00Z",
        "yanked": "false",
    }
    fields.update(overrides)
    return "\n[[releases]]\n" + "".join(f"{k} = {v}\n" for k, v in fields.items() if v is not None)


def plugin_toml(plugin_id: str, subject: str = "grammar", releases: str = "", **plugin_overrides) -> str:
    plugin = {
        "id": f'"{plugin_id}"',
        "official": "true",
        "license": '"MIT"',
        "language": '"Rust"',
        "subject": f'"{subject}"',
        "maintainers": '["Wavesonics"]',
    }
    plugin.update(plugin_overrides)
    return (
        "[plugin]\n"
        + "".join(f"{k} = {v}\n" for k, v in plugin.items() if v is not None)
        + f'\n[source]\ngithub = "{GITHUB}"\nrepo_id = {REPO_ID}\nasset = "{plugin_id}.hammerplugin"\n'
        + f'tag_prefix = "{plugin_id}-v"\nauto_update = true\n'
        + releases
    )


class Registry:
    """A registry checkout in a temporary directory."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "subjects.toml", root / "subjects.toml")
        (root / "plugins").mkdir(exist_ok=True)

    def add(self, plugin_id: str, toml: str, description: str = "A plugin.\n") -> Path:
        d = self.root / "plugins" / plugin_id
        (d / "description").mkdir(parents=True, exist_ok=True)
        (d / "plugin.toml").write_text(toml, encoding="utf-8")
        (d / "description" / "en.md").write_text(description, encoding="utf-8")
        return d

    def copy(self, dest: Path) -> "Registry":
        shutil.copytree(self.root, dest)
        return Registry(dest)


@pytest.fixture
def registry(tmp_path) -> Registry:
    return Registry(tmp_path / "registry")


@pytest.fixture
def downloader() -> FakeDownloader:
    return FakeDownloader()


@pytest.fixture
def cache(tmp_path, downloader) -> AssetCache:
    return AssetCache(tmp_path / "cache", downloader)
