from tools import validate
from tools.registry.fetch import AssetCache

from .conftest import asset_url, fixture_manifest, make_package, plugin_toml, release_toml

LANGUAGETOOL = fixture_manifest("languagetool")


def languagetool_base(registry, downloader):
    package = make_package(LANGUAGETOOL)
    downloader.add(asset_url("languagetool", "0.1.0"), package)
    registry.add("languagetool", plugin_toml("languagetool", releases=release_toml("languagetool", "0.1.0", package)))
    return package


def add_release(registry, downloader, manifest_text, version, base_releases):
    package = make_package(manifest_text)
    downloader.add(asset_url("languagetool", version), package)
    registry.add(
        "languagetool",
        plugin_toml("languagetool", releases=base_releases + release_toml("languagetool", version, package)),
    )


def pr(base, head, cache, author="Wavesonics", permission="read"):
    return validate.validate_pr(head.root, base.root, cache, author, permission)


def test_whole_repo_reports_review_status(registry, downloader, cache):
    languagetool_base(registry, downloader)
    report = validate.validate_repo(registry.root, cache)
    assert report.errors == []
    assert report.needs_review
    assert "New HTTP host: `api.languagetool.org`" in report.render()


def test_new_release_without_new_permissions_auto_merges(registry, downloader, cache, tmp_path):
    first = languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    add_release(head, downloader, LANGUAGETOOL.replace('version = "0.1.0"', 'version = "0.2.0"'), "0.2.0",
                release_toml("languagetool", "0.1.0", first))
    report = pr(registry, head, cache)
    assert report.errors == []
    assert not report.needs_review
    assert report.auto_merge


def test_new_release_with_new_operation_needs_review(registry, downloader, cache, tmp_path):
    first = languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    text = LANGUAGETOOL.replace('version = "0.1.0"', 'version = "0.2.0"').replace(
        "[permissions]\n", '[permissions]\noperations = ["scene.read"]\n'
    )
    add_release(head, downloader, text, "0.2.0", release_toml("languagetool", "0.1.0", first))
    report = pr(registry, head, cache)
    assert report.errors == []
    assert report.needs_review
    assert not report.auto_merge
    assert "New operation: `scene.read`" in report.render()


def test_description_edit_by_non_maintainer_fails(registry, downloader, cache, tmp_path):
    languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    (head.root / "plugins/languagetool/description/en.md").write_text("Better words.\n")
    report = pr(registry, head, cache, author="someone")
    assert any("someone is not a maintainer of languagetool" in e for e in report.errors)
    assert not report.auto_merge

    report = pr(registry, head, cache, author="someone", permission="admin")
    assert report.errors == []
    assert report.auto_merge


def test_maintainer_change_needs_admin(registry, downloader, cache, tmp_path):
    first = languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    head.add("languagetool", plugin_toml("languagetool", maintainers='["Wavesonics", "friend"]',
                                         releases=release_toml("languagetool", "0.1.0", first)))
    assert any("only a repository admin" in e for e in pr(registry, head, cache).errors)
    assert pr(registry, head, cache, permission="admin").errors == []


def test_new_plugin_needs_review(registry, downloader, cache, tmp_path):
    languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    head.add("simple-grammar", plugin_toml("simple-grammar", official="false", maintainers='["newcomer"]'))
    report = pr(registry, head, cache, author="newcomer")
    assert report.errors == []
    assert "simple-grammar is a new plugin" in report.reviews
    assert not report.auto_merge

    head.add("simple-grammar", plugin_toml("simple-grammar", maintainers='["newcomer"]'))
    assert any("only a repository admin may list an official plugin" in e
               for e in pr(registry, head, cache, author="newcomer").errors)


def test_source_change_and_repinning_need_review(registry, downloader, cache, tmp_path):
    first = languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    toml = plugin_toml("languagetool", releases=release_toml("languagetool", "0.1.0", first,
                                                             published="2026-10-23T00:00:00Z"))
    head.add("languagetool", toml.replace("auto_update = true", "auto_update = false"))
    report = pr(registry, head, cache)
    assert "languagetool changes the pin of release 0.1.0" in report.reviews

    head.add("languagetool", plugin_toml("languagetool").replace("repo_id = 1404820522", "repo_id = 5"))
    report = pr(registry, head, cache)
    assert "languagetool changes its source" in report.reviews
    assert "languagetool removes release 0.1.0" in report.reviews


def test_yank_by_maintainer_auto_merges(registry, downloader, cache, tmp_path):
    first = languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    head.add("languagetool", plugin_toml("languagetool", releases=release_toml(
        "languagetool", "0.1.0", first, yanked="true", yank_reason='"Crashes on start"')))
    report = pr(registry, head, cache)
    assert report.errors == [] and report.auto_merge


def test_changes_outside_plugins_need_review(registry, downloader, cache, tmp_path):
    languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    (head.root / "README.md").write_text("Hello\n")
    report = pr(registry, head, cache, permission="admin")
    assert report.needs_review
    assert "README.md" in report.reviews[0]
    assert not report.auto_merge


def test_subject_must_match_a_declared_kind(registry, downloader, cache):
    package = make_package(LANGUAGETOOL)
    downloader.add(asset_url("languagetool", "0.1.0"), package)
    registry.add("languagetool", plugin_toml("languagetool", subject="names",
                                             releases=release_toml("languagetool", "0.1.0", package)))
    report = validate.validate_repo(registry.root, cache)
    assert any("subject 'names' is in the action drawer" in e for e in report.errors)


def test_main_exit_code_and_outputs(registry, downloader, tmp_path, capsys):
    languagetool_base(registry, downloader)
    out = tmp_path / "out"
    code = validate.main(["--root", str(registry.root), "--cache", str(tmp_path / "c"),
                          "--github-output", str(out)], downloader)
    assert code == 0
    assert "Review needed: yes" in capsys.readouterr().out
    assert out.read_text() == "needs_review=true\nauto_merge=false\n"

    (registry.root / "plugins/languagetool/plugin.toml").write_text("[plugin]\n")
    assert validate.main(["--root", str(registry.root), "--no-download"], downloader) == 1


def test_bad_download_is_an_error(registry, downloader, tmp_path):
    languagetool_base(registry, downloader)
    downloader.files.clear()
    report = validate.validate_repo(registry.root, AssetCache(tmp_path / "c", downloader))
    assert any("release 0.1.0" in e and "404" in e for e in report.errors)


def test_description_edit_fetches_at_most_one_package(registry, downloader, cache, tmp_path):
    pins = ""
    for version in ("0.1.0", "0.2.0", "0.3.0"):
        package = make_package(LANGUAGETOOL.replace('version = "0.1.0"', f'version = "{version}"'))
        downloader.add(asset_url("languagetool", version), package)
        pins += release_toml("languagetool", version, package)
    registry.add("languagetool", plugin_toml("languagetool", releases=pins))
    head = registry.copy(tmp_path / "head")
    (head.root / "plugins/languagetool/description/en.md").write_text("Better words.\n")

    report = pr(registry, head, cache)
    assert report.errors == [] and report.auto_merge
    assert len(downloader.calls) <= 1


def test_maintainer_is_read_from_a_base_copy_that_fails_other_checks(registry, downloader, cache, tmp_path):
    languagetool_base(registry, downloader)
    toml = (registry.root / "plugins/languagetool/plugin.toml").read_text()
    (registry.root / "plugins/languagetool/plugin.toml").write_text(toml.replace('subject = "grammar"', 'subject = "gone"'))
    head = registry.copy(tmp_path / "head")
    (head.root / "plugins/languagetool/plugin.toml").write_text(toml)

    report = pr(registry, head, cache)
    assert report.errors == []
    assert report.auto_merge

    assert any("not a maintainer" in e for e in pr(registry, head, cache, author="someone").errors)


def test_unreadable_base_copy_needs_an_admin(registry, downloader, cache, tmp_path):
    languagetool_base(registry, downloader)
    head = registry.copy(tmp_path / "head")
    (registry.root / "plugins/languagetool/plugin.toml").write_text("[plugin\n")
    report = pr(registry, head, cache)
    assert any("does not load, so its maintainers are unknown" in e for e in report.errors)
    assert pr(registry, head, cache, permission="admin").errors == []
