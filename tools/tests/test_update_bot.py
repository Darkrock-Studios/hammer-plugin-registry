import hashlib
import tomllib

import pytest

from tools import update_bot
from tools.registry import releases, schema
from tools.registry.github import Asset, GitHubRelease, PullRequest

from .conftest import REPO_ID, asset_url, fixture_manifest, make_package, plugin_toml, release_toml


class FakeGitHub:
    def __init__(self):
        self.releases: dict[int, list[GitHubRelease]] = {}
        self.branches = {"main": "mainsha"}
        self.files: list[tuple[str, str, str, str | None]] = []  # path, branch, content, blob sha
        self.prs: list[dict] = []
        self.listed: list[int] = []

    def list_releases(self, repo_id):
        self.listed.append(repo_id)
        return self.releases.get(repo_id, [])

    def branch_sha(self, branch):
        return self.branches[branch]

    def create_or_reset_branch(self, branch, sha):
        self.branches[branch] = sha

    def put_file(self, path, content, message, branch, blob_sha):
        assert branch in self.branches
        self.files.append((path, branch, content.decode(), blob_sha))

    def find_prs(self, head_branch):
        return [
            PullRequest(number=i + 1, open=p["state"] == "open", merged=p["state"] == "merged", title=p["title"],
                        body=p["body"], url=f"https://github.com/o/r/pull/{i + 1}")
            for i, p in enumerate(self.prs)
            if p["head"] == head_branch
        ]

    def create_pr(self, head, base, title, body):
        self.prs.append({"head": head, "base": base, "title": title, "body": body, "labels": [], "state": "open"})
        return len(self.prs)

    def update_pr(self, number, title, body):
        self.prs[number - 1].update(title=title, body=body)

    def add_labels(self, number, labels):
        self.prs[number - 1]["labels"] = sorted(set(self.prs[number - 1]["labels"]) | set(labels))


def gh_release(plugin_id, version, package_size=1, draft=False, prerelease=False, tag=None, assets=None):
    tag = tag or f"{plugin_id}-v{version}"
    if assets is None:
        assets = [
            Asset(f"{plugin_id}.hammerplugin", asset_url(plugin_id, version), package_size),
            Asset("checksums.txt", f"https://github.com/x/y/releases/download/{tag}/checksums.txt", 10),
        ]
    return GitHubRelease(tag=tag, draft=draft, prerelease=prerelease,
                         published_at="2026-10-22T14:03:00Z", assets=assets)


@pytest.fixture
def github():
    return FakeGitHub()


def publish(github, downloader, plugin_id, manifest_text, version, **kwargs):
    package = make_package(manifest_text)
    downloader.add(asset_url(plugin_id, version), package)
    github.releases.setdefault(REPO_ID, []).append(gh_release(plugin_id, version, len(package), **kwargs))
    return package


def run(registry, github, cache):
    pending, errors = update_bot.plan(registry.root, github, cache)
    committed, apply_errors = update_bot.apply(registry.root, pending, github)
    return pending, errors + apply_errors, committed


def test_first_release_without_permissions_is_committed_to_main(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission", language='"C"'))
    original = (registry.root / "plugins/plaintext/plugin.toml").read_bytes()
    package = publish(github, downloader, "plaintext", fixture_manifest("plaintext"), "1.0.0")

    pending, errors, committed = run(registry, github, cache)
    assert errors == []
    assert committed
    assert github.prs == []
    [(path, branch, content, blob)] = github.files
    assert (path, branch) == ("plugins/plaintext/plugin.toml", "main")
    assert blob == releases.git_blob_sha(original)
    plugin, parse_errors = schema.parse_plugin(content, path)
    assert parse_errors == []
    [pin] = plugin.releases
    assert pin.version == "1.0.0"
    assert pin.url == asset_url("plaintext", "1.0.0")
    assert pin.size == len(package)
    assert pin.sha256 == hashlib.sha256(package).hexdigest()
    assert pin.published_iso == "2026-10-22T14:03:00Z"
    assert not pin.yanked


def test_permission_change_opens_a_pull_request(registry, github, downloader, cache):
    old = make_package(fixture_manifest("languagetool"))
    downloader.add(asset_url("languagetool", "0.1.0"), old)
    registry.add("languagetool", plugin_toml("languagetool", releases=release_toml("languagetool", "0.1.0", old)))
    new_text = fixture_manifest("languagetool", "0.2.0").replace("[permissions]\n", '[permissions]\noperations = ["scene.read"]\n')
    publish(github, downloader, "languagetool", new_text, "0.2.0")

    pending, errors, committed = run(registry, github, cache)
    assert errors == []
    assert not committed
    [pr] = github.prs
    assert pr["head"] == "registry-bot/languagetool"
    assert pr["base"] == "main"
    assert pr["labels"] == ["needs-review"]
    assert "New operation: `scene.read`" in pr["body"]
    assert pr["title"] == "Pin languagetool 0.2.0"
    [(path, branch, content, _)] = github.files
    assert branch == "registry-bot/languagetool"
    assert [r["version"] for r in tomllib.loads(content)["releases"]] == ["0.1.0", "0.2.0"]
    assert github.branches[branch] == "mainsha"


def languagetool_with_pr(registry, github, downloader, cache):
    """languagetool 0.1.0 pinned, and 0.2.0, which asks for a new operation, proposed for review."""
    old = make_package(fixture_manifest("languagetool"))
    downloader.add(asset_url("languagetool", "0.1.0"), old)
    registry.add("languagetool", plugin_toml("languagetool", releases=release_toml("languagetool", "0.1.0", old)))
    text = fixture_manifest("languagetool", "0.2.0").replace("[permissions]\n", '[permissions]\noperations = ["scene.read"]\n')
    publish(github, downloader, "languagetool", text, "0.2.0")
    run(registry, github, cache)
    assert len(github.prs) == 1
    return text


def test_a_later_release_updates_the_open_pull_request(registry, github, downloader, cache):
    text = languagetool_with_pr(registry, github, downloader, cache)
    publish(github, downloader, "languagetool", text.replace('version = "0.2.0"', 'version = "0.3.0"'), "0.3.0")
    github.files.clear()

    pending, errors, committed = run(registry, github, cache)
    assert errors == [] and not committed
    [pr] = github.prs
    assert pr["title"] == "Pin languagetool 0.2.0, 0.3.0"
    assert "0.2.0, 0.3.0" in pr["body"]
    assert pr["labels"] == ["needs-review"]
    [(path, branch, content, _)] = github.files
    assert branch == "registry-bot/languagetool"
    assert [r["version"] for r in tomllib.loads(content)["releases"]] == ["0.1.0", "0.2.0", "0.3.0"]


def test_an_unchanged_open_pull_request_is_left_alone(registry, github, downloader, cache):
    languagetool_with_pr(registry, github, downloader, cache)
    github.files.clear()
    pending, errors, _ = run(registry, github, cache)
    assert errors == [] and len(pending) == 1
    assert github.files == [] and len(github.prs) == 1


def test_a_rejected_pull_request_stops_the_plugin(registry, github, downloader, cache, capsys):
    text = languagetool_with_pr(registry, github, downloader, cache)
    github.prs[0]["state"] = "closed"
    publish(github, downloader, "languagetool", text.replace('version = "0.2.0"', 'version = "0.3.0"'), "0.3.0")
    github.files.clear()

    pending, errors, committed = run(registry, github, cache)
    assert errors == [] and not committed
    assert github.files == [] and len(github.prs) == 1
    out = capsys.readouterr().out
    assert "pull request #1 (https://github.com/o/r/pull/1) proposing 0.2.0 was closed without merging" in out
    assert "Reopen #1" in out and "yanked = true" in out


def test_pinning_the_rejected_version_as_yanked_moves_the_bot_past_it(registry, github, downloader, cache):
    text = languagetool_with_pr(registry, github, downloader, cache)
    github.prs[0]["state"] = "closed"
    first = make_package(fixture_manifest("languagetool"))
    rejected = downloader.files[asset_url("languagetool", "0.2.0")]
    registry.add("languagetool", plugin_toml("languagetool", releases=release_toml("languagetool", "0.1.0", first)
                 + release_toml("languagetool", "0.2.0", rejected, yanked="true", yank_reason='"Rejected"')))
    publish(github, downloader, "languagetool", text.replace('version = "0.2.0"', 'version = "0.3.0"'), "0.3.0")
    github.files.clear()

    pending, errors, committed = run(registry, github, cache)
    assert errors == []
    [(_, branch, content, _)] = github.files
    assert branch == "main" and committed
    assert [r["version"] for r in tomllib.loads(content)["releases"]] == ["0.1.0", "0.2.0", "0.3.0"]


def test_skips_drafts_prereleases_other_plugins_and_old_versions(registry, github, downloader, cache):
    old = make_package(fixture_manifest("harper-grammar", "0.2.0"))
    downloader.add(asset_url("harper-grammar", "0.2.0"), old)
    registry.add("harper-grammar", plugin_toml("harper-grammar", releases=release_toml("harper-grammar", "0.2.0", old)))
    github.releases[REPO_ID] = [
        gh_release("harper-grammar", "0.1.0"),
        gh_release("harper-grammar", "0.2.0"),
        gh_release("harper-grammar", "0.3.0", draft=True),
        gh_release("harper-grammar", "0.4.0", prerelease=True),
        gh_release("languagetool", "0.5.0"),
        gh_release("harper-grammar", "x", tag="harper-grammar-vnext"),
        gh_release("harper-grammar", "1.0.0", tag="v1.0.0"),
    ]
    pending, errors, committed = run(registry, github, cache)
    assert pending == [] and errors == [] and not committed
    assert downloader.calls == []


def test_several_new_releases_are_pinned_in_order(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    publish(github, downloader, "plaintext", fixture_manifest("plaintext", "1.10.0"), "1.10.0")
    publish(github, downloader, "plaintext", fixture_manifest("plaintext", "1.9.0"), "1.9.0")
    pending, errors, committed = run(registry, github, cache)
    assert errors == []
    [(_, _, content, _)] = github.files
    assert [r["version"] for r in tomllib.loads(content)["releases"]] == ["1.9.0", "1.10.0"]


def test_bad_package_is_not_pinned(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    publish(github, downloader, "plaintext", fixture_manifest("plaintext", "1.0.1"), "1.0.0")
    pending, errors, committed = run(registry, github, cache)
    assert pending == [] and not committed
    assert any("manifest version is '1.0.1', expected '1.0.0'" in e for e in errors)


def test_asset_must_match_exactly_once(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    url = asset_url("plaintext", "1.0.0")
    github.releases[REPO_ID] = [gh_release("plaintext", "1.0.0", assets=[
        Asset("plaintext.hammerplugin", url, 1), Asset("plaintext.hammerplugin", url, 1)])]
    _, errors, _ = run(registry, github, cache)
    assert any("2 assets match" in e for e in errors)

    github.releases[REPO_ID] = [gh_release("plaintext", "1.0.0", assets=[])]
    _, errors, _ = run(registry, github, cache)
    assert any("0 assets match" in e for e in errors)


def test_renamed_repository_is_reported(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    url = "https://github.com/New-Owner/hammer-plugins/releases/download/plaintext-v1.0.0/plaintext.hammerplugin"
    github.releases[REPO_ID] = [gh_release("plaintext", "1.0.0", assets=[Asset("plaintext.hammerplugin", url, 1)])]
    pending, errors, _ = run(registry, github, cache)
    assert pending == []
    assert any("url must start with https://github.com/Darkrock-Studios/hammer-plugins/" in e for e in errors)


def test_auto_update_off_is_left_alone(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission").replace("auto_update = true", "auto_update = false"))
    publish(github, downloader, "plaintext", fixture_manifest("plaintext"), "1.0.0")
    pending, errors, _ = run(registry, github, cache)
    assert pending == [] and errors == [] and github.listed == []


def test_one_listing_per_repository(registry, github, downloader, cache):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    registry.add("simple-grammar", plugin_toml("simple-grammar"))
    run(registry, github, cache)
    assert github.listed == [REPO_ID]


def test_main_dry_run_changes_nothing(registry, github, downloader, cache, capsys):
    registry.add("plaintext", plugin_toml("plaintext", subject="submission"))
    publish(github, downloader, "plaintext", fixture_manifest("plaintext"), "1.0.0")
    assert update_bot.main(["--root", str(registry.root), "--dry-run"], github=github, cache=cache) == 0
    assert "plaintext: would pin 1.0.0" in capsys.readouterr().out
    assert github.files == []
