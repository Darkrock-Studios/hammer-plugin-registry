import datetime
import json

from tools import build_index
from tools.registry.fetch import AssetCache

from .conftest import asset_url, fixture_manifest, make_package, plugin_toml, release_toml

NOW = datetime.datetime(2026, 10, 22, 14, 10, tzinfo=datetime.UTC)


def harper_registry(registry, downloader):
    old = make_package(fixture_manifest("harper-grammar", "0.1.0"))
    new = make_package(fixture_manifest("harper-grammar", "0.2.0"))
    downloader.add(asset_url("harper-grammar", "0.1.0"), old)
    downloader.add(asset_url("harper-grammar", "0.2.0"), new)
    releases = release_toml("harper-grammar", "0.1.0", old) + release_toml(
        "harper-grammar", "0.2.0", new, published="2026-10-23T09:30:00+02:00"
    )
    d = registry.add(
        "harper-grammar",
        plugin_toml("harper-grammar", releases=releases, homepage='"https://writewithharper.com"'),
        description="Grammar from Harper.\n",
    )
    (d / "description" / "fr.md").write_text("Grammaire.\n", encoding="utf-8")
    registry.add("simple-grammar", plugin_toml("simple-grammar", language='"C"'))
    return old, new


def test_index(registry, downloader, cache):
    old, new = harper_registry(registry, downloader)
    index, errors = build_index.build(registry.root, cache, "abc123", NOW)
    assert errors == []
    assert index["schema"] == 1
    assert index["generated"] == "2026-10-22T14:10:00Z"
    assert index["commit"] == "abc123"
    assert index["subjects"]["grammar"] == {"name": "Grammar", "drawer": "diagnostic", "call": "310"}
    assert [p["id"] for p in index["plugins"]] == ["harper-grammar", "simple-grammar"]

    harper = index["plugins"][0]
    assert harper["source"] == {"github": "Darkrock-Studios/hammer-plugins", "repoId": 1404820522}
    assert harper["homepage"] == "https://writewithharper.com"
    assert harper["description"] == {"en": "Grammar from Harper.\n", "fr": "Grammaire.\n"}
    assert harper["icon"] is None
    assert harper["screenshots"] == []
    assert [r["version"] for r in harper["releases"]] == ["0.2.0", "0.1.0"]

    newest = harper["releases"][0]
    assert newest["published"] == "2026-10-23T07:30:00Z"
    assert newest["size"] == len(new)
    assert newest["yanked"] is False
    assert newest["manifest"] == {
        "name": "Harper grammar",
        "description": "Grammar and style checks from [Harper](https://writewithharper.com/), underlined as you "
        "write. Thorough, but large: it needs about 100 MB of memory.",
        "api": 1,
        "languages": ["en"],
        "exporters": [],
        "actions": [],
        "diagnostics": [{"name": "grammar", "label": "Harper grammar"}],
        "commands": [],
        "permissions": {"operations": [], "http": [], "userHosts": None},
        "limits": {"memory": 256},
    }

    simple = index["plugins"][1]
    assert simple["releases"] == []
    assert simple["homepage"] is None
    json.dumps(index)


def test_downloads_are_cached(registry, downloader, tmp_path):
    harper_registry(registry, downloader)
    cache = AssetCache(tmp_path / "cache", downloader)
    build_index.build(registry.root, cache, "abc", NOW)
    build_index.build(registry.root, cache, "abc", NOW)
    assert len(downloader.calls) == 2


def test_hash_mismatch_fails(registry, downloader, cache):
    harper_registry(registry, downloader)
    downloader.add(asset_url("harper-grammar", "0.2.0"), make_package(fixture_manifest("harper-grammar", "0.2.0") + "\n"))
    _, errors = build_index.build(registry.root, cache, "abc", NOW)
    assert any("release 0.2.0" in e and ("sha256 is" in e or "size is" in e) for e in errors)


def test_manifest_version_must_match(registry, downloader, cache):
    package = make_package(fixture_manifest("harper-grammar", "0.3.0"))
    downloader.add(asset_url("harper-grammar", "0.2.0"), package)
    registry.add("harper-grammar", plugin_toml("harper-grammar", releases=release_toml("harper-grammar", "0.2.0", package)))
    _, errors = build_index.build(registry.root, cache, "abc", NOW)
    assert any("manifest version is '0.3.0', expected '0.2.0'" in e for e in errors)


def test_main_writes_index_and_fails_without_writing(registry, downloader, tmp_path):
    harper_registry(registry, downloader)
    out = tmp_path / "index.json"
    args = ["--root", str(registry.root), "--out", str(out), "--cache", str(tmp_path / "c"), "--commit", "abc"]
    assert build_index.main(args, downloader) == 0
    assert json.loads(out.read_text())["plugins"][0]["releases"][0]["version"] == "0.2.0"

    out.unlink()
    downloader.files.clear()
    assert build_index.main(args[:-4] + ["--cache", str(tmp_path / "empty"), "--commit", "abc"], downloader) == 1
    assert not out.exists()
