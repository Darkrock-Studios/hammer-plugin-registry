"""Pins new releases of every plugin with auto_update = true.

    GITHUB_TOKEN=... GITHUB_REPOSITORY=owner/hammer-plugin-registry python -m tools.update_bot

Lists each source repository's releases by its numeric id, skips drafts and pre-releases, and takes
those whose tag starts with tag_prefix and whose version is newer than the newest pin. Each is
downloaded, hashed and its manifest checked. A plugin whose new releases ask for no new permission is
committed straight to main; one that does gets a pull request labelled needs-review, with the diff.

Each plugin has one review branch, registry-bot/<id>. While its pull request is open, the bot updates it
with every pending release instead of opening another. If one is closed without merging, the bot leaves
the plugin alone until a person reopens it, or pins the rejected versions as yanked.
"""

import argparse
import datetime
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .registry import manifest, permissions, releases, schema, semver
from .registry.fetch import AssetCache, http_download
from .registry.github import GitHub, GitHubRelease, PullRequest, RestGitHub

REVIEW_LABEL = "needs-review"
BRANCH_PREFIX = "registry-bot/"
_VERSIONS_MARKER = re.compile(r"<!-- registry-bot versions: ([^>]*?) -->")


@dataclass
class Pending:
    plugin: schema.Plugin
    pins: list[schema.Release] = field(default_factory=list)
    diffs: list[permissions.PermissionDiff] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return any(d.needs_review for d in self.diffs)

    @property
    def versions(self) -> str:
        return ", ".join(r.version for r in self.pins)


def _parse_time(value: str | None) -> datetime.datetime:
    if not value:
        return datetime.datetime.now(datetime.UTC).replace(microsecond=0)
    return datetime.datetime.fromisoformat(value).astimezone(datetime.UTC)


def _candidates(plugin: schema.Plugin, listed: list[GitHubRelease]) -> list[tuple[str, GitHubRelease]]:
    newest = plugin.newest()
    found = []
    for release in listed:
        if release.draft or release.prerelease or not release.tag.startswith(plugin.tag_prefix):
            continue
        version = release.tag[len(plugin.tag_prefix):]
        if not semver.is_valid(version):
            continue
        if newest is None or semver.compare(version, newest.version) > 0:
            found.append((version, release))
    found.sort(key=lambda pair: semver.sort_key(pair[0]))
    return found


def plan(root: Path, github: GitHub, cache: AssetCache) -> tuple[list[Pending], list[str]]:
    errors: list[str] = []
    subjects, subject_errors = schema.load_subjects(root / "subjects.toml")
    errors += subject_errors
    listings: dict[int, list[GitHubRelease]] = {}
    pending: list[Pending] = []

    for plugin_dir in schema.plugin_dirs(root):
        plugin, plugin_errors = schema.load_plugin_dir(plugin_dir, subjects)
        errors += plugin_errors
        if plugin is None or not plugin.auto_update:
            continue
        where = f"plugins/{plugin.id}"
        if plugin.repo_id not in listings:
            try:
                listings[plugin.repo_id] = github.list_releases(plugin.repo_id)
            except Exception as e:
                errors.append(f"{where}: listing releases of repository {plugin.repo_id}: {e}")
                listings[plugin.repo_id] = []
        candidates = _candidates(plugin, listings[plugin.repo_id])
        if not candidates:
            continue

        newest = plugin.newest()
        previous_summary, previous_version = None, None
        if newest is not None:
            try:
                previous_summary = releases.load_manifest(plugin, newest, cache)
                previous_version = newest.version
            except Exception as e:
                errors.append(f"{where}: release {newest.version}: {e}")
                continue

        item = Pending(plugin)
        for version, gh_release in candidates:
            at = f"{where}: {gh_release.tag}"
            matches = [a for a in gh_release.assets if plugin.asset_matches(a.name)]
            if len(matches) != 1:
                errors.append(f"{at}: {len(matches)} assets match {plugin.asset!r}, expected exactly one")
                break
            asset = matches[0]
            url_errors = schema.check_release_url(plugin, version, asset.url, at)
            if url_errors:
                errors += url_errors
                break
            try:
                data, digest = cache.fetch_new(asset.url)
                summary = manifest.load_release(data, plugin.id, version)
            except Exception as e:
                errors.append(f"{at}: {e}")
                break
            if asset.size and asset.size != len(data):
                errors.append(f"{at}: downloaded {len(data)} bytes, GitHub lists {asset.size}")
                break
            pin = schema.Release(
                version=version,
                url=asset.url,
                sha256=digest,
                size=len(data),
                published=_parse_time(gh_release.published_at),
            )
            item.pins.append(pin)
            item.diffs.append(permissions.diff(previous_summary, summary, previous_version, version))
            previous_summary, previous_version = summary, version
        if item.pins:
            pending.append(item)
    return pending, errors


def _pr_body(item: Pending) -> str:
    plugin = item.plugin
    lines = [
        f"New releases of **{plugin.id}** from [{plugin.github}](https://github.com/{plugin.github}) "
        f"(repository id {plugin.repo_id}): {item.versions}.",
        "",
        "Each package was downloaded from its release, hashed, and its manifest checked. "
        "The releases ask for permissions the plugin did not have before, so they wait for review.",
        "",
        "## Permission changes",
        "",
    ]
    lines += [d.render(plugin.id) + "\n" for d in item.diffs]
    lines += [
        "The bot updates this pull request when more releases come out. If it is closed without merging, "
        f"the bot stops proposing releases of {plugin.id} until someone reopens it, or pins the rejected "
        "versions with `yanked = true` and a `yank_reason` so the bot moves past them.",
        "",
        f"<!-- registry-bot versions: {item.versions} -->",
    ]
    return "\n".join(lines)


def _proposed_versions(pr: PullRequest, plugin_id: str) -> list[str]:
    match = _VERSIONS_MARKER.search(pr.body)
    if match:
        listed = match.group(1)
    elif pr.title.startswith(f"Pin {plugin_id} "):
        listed = pr.title[len(f"Pin {plugin_id} "):]
    else:
        return []
    return [v.strip() for v in listed.split(",") if semver.is_valid(v.strip())]


def _rejected(plugin: schema.Plugin, prs: list[PullRequest]) -> tuple[PullRequest, list[str]] | None:
    """A closed, unmerged pull request proposing versions that are still not pinned."""
    newest = plugin.newest()
    for pr in prs:
        if pr.open or pr.merged:
            continue
        unpinned = [
            v for v in _proposed_versions(pr, plugin.id) if newest is None or semver.compare(v, newest.version) > 0
        ]
        if unpinned:
            return pr, unpinned
    return None


def _warn(message: str) -> None:
    print(f"::warning::{message}" if os.environ.get("GITHUB_ACTIONS") else message)


def apply(root: Path, pending: list[Pending], github: GitHub, base: str = "main") -> tuple[bool, list[str]]:
    """Commits or proposes each pending update. Returns whether anything was committed to base."""
    errors: list[str] = []
    committed = False
    for item in pending:
        plugin = item.plugin
        path = f"plugins/{plugin.id}/plugin.toml"
        old = (root / path).read_bytes()
        new_text = releases.append_releases(old.decode("utf-8"), item.pins)
        parsed, parse_errors = schema.parse_plugin(new_text, path)
        if parsed is None:
            errors += parse_errors
            continue
        new = new_text.encode("utf-8")
        title = f"Pin {plugin.id} {item.versions}"
        branch = f"{BRANCH_PREFIX}{plugin.id}"
        try:
            prs = github.find_prs(branch)
            rejected = _rejected(plugin, prs)
            if rejected is not None:
                pr, versions = rejected
                listed = ", ".join(versions)
                _warn(
                    f"{plugin.id}: pull request #{pr.number} ({pr.url}) proposing {listed} was closed without "
                    f"merging, so the bot will not propose {plugin.id} again until a person acts. Reopen "
                    f"#{pr.number} to have the bot update it, or pin {listed} with yanked = true and a "
                    "yank_reason so the bot moves past them."
                )
                continue
            open_pr = next((pr for pr in prs if pr.open), None)
            if item.needs_review or open_pr is not None:
                if open_pr is not None and _proposed_versions(open_pr, plugin.id) == [r.version for r in item.pins]:
                    print(f"{plugin.id}: pull request #{open_pr.number} already proposes {item.versions}")
                    continue
                github.create_or_reset_branch(branch, github.branch_sha(base))
                github.put_file(path, new, title, branch, releases.git_blob_sha(old))
                if open_pr is not None:
                    github.update_pr(open_pr.number, title, _pr_body(item))
                    number = open_pr.number
                    print(f"{plugin.id}: updated pull request #{number} to {item.versions}")
                else:
                    number = github.create_pr(branch, base, title, _pr_body(item))
                    print(f"{plugin.id}: opened pull request #{number} for review")
                github.add_labels(number, [REVIEW_LABEL])
            else:
                github.put_file(path, new, title, base, releases.git_blob_sha(old))
                committed = True
                print(f"{plugin.id}: committed {item.versions} to {base}")
        except Exception as e:
            errors.append(f"{path}: {e}")
    return committed, errors


def main(argv: list[str] | None = None, github: GitHub | None = None, cache: AssetCache | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.update_bot", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="the registry, owner/name")
    parser.add_argument("--base", default="main")
    parser.add_argument("--cache", type=Path, default=Path(".cache/assets"))
    parser.add_argument("--dry-run", action="store_true", help="report what would be pinned, change nothing")
    args = parser.parse_args(argv)

    if github is None:
        if not args.repo and not args.dry_run:
            parser.error("--repo or GITHUB_REPOSITORY is required")
        github = RestGitHub(os.environ.get("GITHUB_TOKEN", ""), args.repo)
    cache = cache or AssetCache(args.cache, http_download)

    pending, errors = plan(args.root, github, cache)
    committed = False
    if args.dry_run:
        for item in pending:
            print(f"{item.plugin.id}: would pin {item.versions}{' (needs review)' if item.needs_review else ''}")
            for d in item.diffs:
                print("  " + d.render(item.plugin.id).replace("\n", "\n  "))
    else:
        committed, apply_errors = apply(args.root, pending, github, args.base)
        errors += apply_errors
    if not pending:
        print("No new releases.")

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"committed={'true' if committed else 'false'}\n")
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
