import struct

import pytest

from tools.registry import schema, semver, spdx
from tools.registry.schema import check_subjects, load_plugin_dir, load_subjects, parse_plugin

from .conftest import REPO_ROOT, make_package, plugin_toml, release_toml, fixture_manifest


def errors_for(registry, plugin_id, toml, **kwargs):
    d = registry.add(plugin_id, toml, **kwargs)
    subjects, _ = load_subjects(registry.root / "subjects.toml")
    plugin, errors = load_plugin_dir(d, subjects)
    return plugin, errors


def test_the_repository_itself_is_valid():
    subjects, errors = load_subjects(REPO_ROOT / "subjects.toml")
    assert errors == []
    dirs = schema.plugin_dirs(REPO_ROOT)
    assert dirs
    for d in dirs:
        _, errors = load_plugin_dir(d, subjects)
        assert errors == [], d.name


def test_good_plugin_with_releases(registry):
    package = make_package(fixture_manifest("harper-grammar"))
    releases = release_toml("harper-grammar", "0.1.0", package) + release_toml(
        "harper-grammar", "0.2.0", package, yanked="true", yank_reason='"Broken on Android"'
    )
    plugin, errors = errors_for(registry, "harper-grammar", plugin_toml("harper-grammar", releases=releases,
                                homepage='"https://writewithharper.com"'))
    assert errors == []
    assert plugin.newest().version == "0.2.0"
    assert plugin.newest().yanked
    assert plugin.predecessor("0.2.0").version == "0.1.0"
    assert plugin.tag_prefix == "harper-grammar-v"


def test_plugin_without_releases_is_valid(registry):
    plugin, errors = errors_for(registry, "simple-grammar", plugin_toml("simple-grammar"))
    assert errors == []
    assert plugin.releases == []
    assert plugin.newest() is None


def test_tag_prefix_defaults_to_v():
    text = plugin_toml("x").replace('tag_prefix = "x-v"\n', "")
    plugin, errors = parse_plugin(text, "plugin.toml")
    assert errors == []
    assert plugin.tag_prefix == "v"


PACKAGE = make_package(fixture_manifest("harper-grammar"))


@pytest.mark.parametrize(
    "toml, message",
    [
        (plugin_toml("p", colour='"red"'), "unknown key `colour`"),
        (plugin_toml("p", license=None), "missing required key `license`"),
        (plugin_toml("p", maintainers=None), "missing required key `maintainers`"),
        (plugin_toml("p", maintainers="[]"), "at least one GitHub login"),
        (plugin_toml("p", maintainers='["not a login"]'), "is not a GitHub login"),
        (plugin_toml("p", official='"yes"'), "`official` must be bool"),
        (plugin_toml("p", license='"Apache 2"'), "not a known SPDX expression"),
        (plugin_toml("p", homepage='"http://example.com"'), "homepage must be an https URL"),
        (plugin_toml("p", id='"Bad_Id"'), "id must be lowercase"),
        (plugin_toml("p").replace("repo_id = 1404820522", "repo_id = 0"), "numeric id"),
        (plugin_toml("p").replace("repo_id = 1404820522", "repo_id = true"), "`repo_id` must be int"),
        (plugin_toml("p").replace('github = "Darkrock-Studios/hammer-plugins"', 'github = "nope"'), "owner/repo"),
        (plugin_toml("p") + "\n[extra]\nx = 1\n", "unknown table `extra`"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, yanked="true")), "yank_reason is required"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, yank_reason='"x"')), "only allowed when yanked"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, sha256='"ABC"')), "sha256 must be 64"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, size="0")), "size must be positive"),
        (plugin_toml("p", releases=release_toml("p", "1.0", PACKAGE)), "not a semantic version"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, published="2026-10-22T14:03:00")),
         "needs a time zone"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, published='"2026-10-22"')),
         "`published` must be datetime"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE, sha=None, checksum='"x"')),
         "unknown key `checksum`"),
        (plugin_toml("p", releases=release_toml("p", "0.2.0", PACKAGE) + release_toml("p", "0.1.0", PACKAGE)),
         "must be higher than the release before it"),
        (plugin_toml("p", releases=release_toml("p", "0.1.0", PACKAGE) + release_toml("p", "0.1.0", PACKAGE)),
         "must be higher than the release before it"),
        (plugin_toml("p", releases=release_toml(
            "p", "0.1.0", PACKAGE, url='"https://github.com/someone/else/releases/download/p-v0.1.0/p.hammerplugin"')),
         "url must start with"),
        (plugin_toml("p", releases=release_toml(
            "p", "0.1.0", PACKAGE,
            url='"https://github.com/Darkrock-Studios/hammer-plugins/releases/download/p-v0.2.0/p.hammerplugin"')),
         "url must start with"),
        (plugin_toml("p", releases=release_toml(
            "p", "0.1.0", PACKAGE,
            url='"https://github.com/Darkrock-Studios/hammer-plugins/releases/download/p-v0.1.0/other.zip"')),
         "does not match source.asset"),
        ("[plugin\n", "does not parse"),
    ],
)
def test_bad_plugin_toml(registry, toml, message):
    _, errors = errors_for(registry, "p", toml)
    assert any(message in e for e in errors), errors


def test_id_must_equal_directory(registry):
    _, errors = errors_for(registry, "other", plugin_toml("p"))
    assert any("must equal the directory name" in e for e in errors)


def test_subject_must_exist(registry):
    _, errors = errors_for(registry, "p", plugin_toml("p", subject="cooking"))
    assert any("subject 'cooking' is not in subjects.toml" in e for e in errors)


def test_description_is_required(registry):
    d = registry.add("p", plugin_toml("p"))
    (d / "description" / "en.md").unlink()
    _, errors = load_plugin_dir(d, load_subjects(registry.root / "subjects.toml")[0])
    assert any("description/en.md: missing" in e for e in errors)


@pytest.mark.parametrize(
    "text, ok",
    [
        ("Plain *Markdown* with a [link](https://example.com) and <https://example.com>.", True),
        ("Code `<b>` and\n\n```\n<div>\n```\n", True),
        ("Some <b>bold</b> text.", False),
        ("<!-- a comment -->", False),
        ('<img src="x.png">', False),
    ],
)
def test_description_has_no_raw_html(registry, text, ok):
    _, errors = errors_for(registry, "p", plugin_toml("p"), description=text)
    assert (not any("raw HTML" in e for e in errors)) == ok, errors


def test_unknown_files_are_rejected(registry):
    d = registry.add("p", plugin_toml("p"))
    (d / "notes.txt").write_text("x")
    (d / "description" / "english.txt").write_text("x")
    _, errors = load_plugin_dir(d, load_subjects(registry.root / "subjects.toml")[0])
    assert any("notes.txt: not allowed" in e for e in errors)
    assert any("english.txt: description files" in e for e in errors)


def webp(width: int, height: int, padding: int = 0) -> bytes:
    bits = (width - 1) | ((height - 1) << 14)
    payload = b"\x2f" + bits.to_bytes(4, "little") + b"\0" * (10 + padding)
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", width, height) + b"\x08\x06\0\0\0"


def test_screenshots_and_icon_within_limits(registry):
    d = registry.add("p", plugin_toml("p"))
    (d / "screenshots").mkdir()
    for i in range(6):
        (d / "screenshots" / f"{i}.webp").write_bytes(webp(1600, 900))
    (d / "icon.png").write_bytes(png(256, 256))
    _, errors = load_plugin_dir(d, load_subjects(registry.root / "subjects.toml")[0])
    assert errors == []


def test_screenshots_and_icon_over_limits(registry):
    d = registry.add("p", plugin_toml("p"))
    (d / "screenshots").mkdir()
    for i in range(7):
        (d / "screenshots" / f"{i}.webp").write_bytes(webp(1200, 800))
    (d / "screenshots" / "0.webp").write_bytes(webp(1601, 900))
    (d / "screenshots" / "1.webp").write_bytes(webp(800, 600, padding=500_000))
    (d / "screenshots" / "2.webp").write_bytes(b"not an image" * 4)
    (d / "icon.png").write_bytes(png(512, 512))
    _, errors = load_plugin_dir(d, load_subjects(registry.root / "subjects.toml")[0])
    text = "\n".join(errors)
    assert "at most 6 screenshots" in text
    assert "1601 px wide" in text
    assert "more than 500000" in text
    assert "2.webp: not a WebP image" in text
    assert "must be 256 x 256" in text


def test_subjects_good_and_bad():
    assert check_subjects({"grammar": {"name": "Grammar", "drawer": "diagnostic", "call": "310"}}) == []
    errors = check_subjects(
        {
            "grammar": {"name": "Grammar", "drawer": "diagnostic", "call": "210"},
            "spelling": {"name": "Spelling", "drawer": "diagnostic", "call": "311"},
            "style": {"name": "Style", "drawer": "diagnostic", "call": "320"},
            "voice": {"name": "Voice", "drawer": "diagnostic", "call": "320"},
            "misc": {"name": "Misc", "drawer": "drawer", "call": "500"},
            "Bad": {"name": "Bad", "drawer": "export", "call": "110", "extra": 1},
        }
    )
    text = "\n".join(errors)
    assert "call 210 is not in the diagnostic drawer" in text
    assert "three digits ending in 0" in text
    assert "call 320 is also used by [style]" in text
    assert "drawer must be one of" in text
    assert "unknown key `extra`" in text
    assert "key must be lowercase" in text


def test_semver():
    assert semver.compare("1.0.0", "1.0.0") == 0
    assert semver.compare("0.10.0", "0.9.0") == 1
    assert semver.compare("1.0.0-rc.1", "1.0.0") == -1
    assert semver.compare("1.0.0-alpha", "1.0.0-alpha.1") == -1
    assert semver.compare("1.0.0-alpha.beta", "1.0.0-beta") == -1
    assert semver.compare("1.0.0-2", "1.0.0-11") == -1
    assert semver.compare("1.0.0-alpha", "1.0.0-1") == 1
    assert semver.compare("1.0.0+build.1", "1.0.0") == 0
    assert sorted(["0.2.0", "0.10.0", "0.9.1"], key=semver.sort_key) == ["0.2.0", "0.9.1", "0.10.0"]
    assert not semver.is_valid("1.0")
    assert not semver.is_valid("01.0.0")
    assert not semver.is_valid("v1.0.0")


@pytest.mark.parametrize(
    "expression, ok",
    [
        ("MIT", True),
        ("Apache-2.0", True),
        ("MIT OR Apache-2.0", True),
        ("(MIT OR Apache-2.0) AND CC-BY-SA-4.0", True),
        ("GPL-2.0-or-later WITH Classpath-exception-2.0", True),
        ("LicenseRef-Proprietary", True),
        ("Apache 2.0", False),
        ("MIT OR", False),
        ("(MIT", False),
        ("mit", False),
        ("", False),
    ],
)
def test_spdx(expression, ok):
    assert spdx.is_valid(expression) == ok
