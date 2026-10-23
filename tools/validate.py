"""Validates the registry, or a pull request against its base.

    python -m tools.validate                      # the whole repository
    python -m tools.validate --root ../pr --base-root . --author LOGIN --author-permission admin

In pull request mode, --root is the pull request's tree and --base-root the base branch's. Exits 1 when
anything is invalid. Whether a person must review is printed, and written to --github-output as
needs_review and auto_merge.
"""

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .registry import manifest as manifest_mod
from .registry import permissions, releases, schema
from .registry.fetch import AssetCache, Downloader, http_download

SOURCE_FIELDS = ("github", "repo_id", "asset", "tag_prefix")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    reviews: list[str] = field(default_factory=list)
    diffs: list[str] = field(default_factory=list)
    auto_merge: bool = False

    @property
    def needs_review(self) -> bool:
        return bool(self.reviews)

    def render(self) -> str:
        out = []
        if self.errors:
            out.append(f"## {len(self.errors)} problem(s)\n")
            out += [f"- {e}" for e in self.errors]
            out.append("")
        if self.diffs:
            out.append("## Permissions\n")
            out += [d + "\n" for d in self.diffs]
        out.append(f"Review needed: {'yes' if self.needs_review else 'no'}")
        out += [f"- {r}" for r in self.reviews]
        if self.auto_merge:
            out.append("Auto-merge: yes")
        return "\n".join(out) + "\n"


def _load_all(root: Path, report: Report, where_prefix: str = "") -> tuple[dict, dict[str, schema.Plugin]]:
    subjects, errors = schema.load_subjects(root / "subjects.toml")
    report.errors += [where_prefix + e for e in errors]
    plugins: dict[str, schema.Plugin] = {}
    plugins_root = root / "plugins"
    if plugins_root.is_dir():
        for entry in sorted(plugins_root.iterdir()):
            if not entry.is_dir():
                report.errors.append(f"plugins/{entry.name}: only plugin directories belong in plugins/")
    for plugin_dir in schema.plugin_dirs(root):
        plugin, errors = schema.load_plugin_dir(plugin_dir, subjects)
        report.errors += errors
        if plugin is not None:
            plugins[plugin.id] = plugin
    return subjects, plugins


def _check_releases(
    plugin: schema.Plugin,
    subjects: dict,
    cache: AssetCache,
    report: Report,
    versions_to_diff: list[str],
    also_fetch: tuple[str, ...] = (),
) -> None:
    """Fetches and checks the newest release, each release to diff and the one before it, and also_fetch."""
    where = f"plugins/{plugin.id}"
    newest = plugin.newest()
    needed = set(versions_to_diff) | set(also_fetch)
    if newest is not None:
        needed.add(newest.version)
    for version in versions_to_diff:
        before = plugin.predecessor(version)
        if before is not None:
            needed.add(before.version)
    summaries, errors = releases.load_manifests(plugin, cache, where, needed)
    report.errors += errors
    if newest is not None and newest.version in summaries:
        drawer = subjects.get(plugin.subject, {}).get("drawer")
        declared = manifest_mod.drawers(summaries[newest.version])
        if drawer and drawer not in declared:
            report.errors.append(
                f"{where}: subject {plugin.subject!r} is in the {drawer} drawer, but release "
                f"{newest.version} declares no {drawer} ({', '.join(sorted(declared)) or 'nothing'})"
            )
    for version in versions_to_diff:
        if version not in summaries:
            continue
        before = plugin.predecessor(version)
        if before is not None and before.version not in summaries:
            continue
        d = permissions.diff(summaries.get(before.version) if before else None, summaries[version],
                             before.version if before else None, version)
        report.diffs.append(d.render(plugin.id))
        if d.needs_review:
            report.reviews.append(f"{plugin.id} {version} asks for new permissions")


def validate_repo(root: Path, cache: AssetCache, download: bool = True) -> Report:
    report = Report()
    subjects, plugins = _load_all(root, report)
    if download:
        for plugin in plugins.values():
            newest = plugin.newest()
            _check_releases(plugin, subjects, cache, report, [newest.version] if newest else [])
    return report


def _tree(root: Path) -> dict[str, str]:
    files = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if rel.parts[0] == ".git" or not path.is_file():
            continue
        files[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def _release_fields(release: schema.Release) -> tuple:
    return release.url, release.sha256, release.size, release.published


def validate_pr(
    root: Path,
    base_root: Path,
    cache: AssetCache,
    author: str,
    author_permission: str,
    download: bool = True,
) -> Report:
    report = Report()
    is_admin = author_permission == "admin"
    subjects, plugins = _load_all(root, report)

    head_tree, base_tree = _tree(root), _tree(base_root)
    changed = {p for p in head_tree.keys() | base_tree.keys() if head_tree.get(p) != base_tree.get(p)}
    changed_ids = sorted({p.split("/")[1] for p in changed if p.startswith("plugins/") and p.count("/") >= 2})
    outside = sorted(p for p in changed if not (p.startswith("plugins/") and p.count("/") >= 2))
    if outside:
        report.reviews.append("changes outside a plugin directory: " + ", ".join(outside))

    maintained_all = bool(changed_ids)
    for plugin_id in changed_ids:
        where = f"plugins/{plugin_id}"
        head_dir, base_dir = root / "plugins" / plugin_id, base_root / "plugins" / plugin_id
        head = plugins.get(plugin_id)

        base = None
        if not base_dir.is_dir():
            report.reviews.append(f"{plugin_id} is a new plugin")
            maintained_all = False
            if head is not None and head.official and not is_admin:
                report.errors.append(f"{where}: only a repository admin may list an official plugin")
        else:
            # Parsed on its own, not with load_plugin_dir: the base copy may fail checks that involve the
            # rest of the tree (a subject since removed, say) and still name its maintainers.
            try:
                base, _ = schema.parse_plugin((base_dir / "plugin.toml").read_text(encoding="utf-8"), where)
            except (OSError, UnicodeDecodeError):
                base = None
            if base is None:
                maintained_all = False
                if not is_admin:
                    report.errors.append(
                        f"{where}: plugin.toml on the base branch does not load, so its maintainers are "
                        "unknown; a repository admin must make this change"
                    )
            elif author not in base.maintainers:
                maintained_all = False
                if not is_admin:
                    report.errors.append(f"{where}: {author} is not a maintainer of {plugin_id}")
            if not head_dir.is_dir():
                report.reviews.append(f"{plugin_id} is removed")

        if head is None:
            continue
        repinned: list[str] = []
        if base is not None:
            if (head.official, head.maintainers) != (base.official, base.maintainers) and not is_admin:
                report.errors.append(f"{where}: only a repository admin may change official or maintainers")
            if any(getattr(head, f) != getattr(base, f) for f in SOURCE_FIELDS):
                report.reviews.append(f"{plugin_id} changes its source")
            head_releases = {r.version: r for r in head.releases}
            for old in base.releases:
                new = head_releases.get(old.version)
                if new is None:
                    report.reviews.append(f"{plugin_id} removes release {old.version}")
                elif _release_fields(new) != _release_fields(old):
                    report.reviews.append(f"{plugin_id} changes the pin of release {old.version}")
                    repinned.append(old.version)
        base_versions = {r.version for r in base.releases} if base else set()
        added = [r.version for r in head.sorted_releases() if r.version not in base_versions]
        if download:
            _check_releases(head, subjects, cache, report, added, tuple(repinned))

    report.auto_merge = not report.errors and not report.reviews and bool(changed) and (
        is_admin or maintained_all
    )
    return report


def main(argv: list[str] | None = None, downloader: Downloader = http_download) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.validate", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=Path("."), help="the tree to validate")
    parser.add_argument("--base-root", type=Path, help="the base branch's tree; turns on pull request mode")
    parser.add_argument("--author", default="", help="the pull request's author")
    parser.add_argument("--author-permission", default="none", help="the author's permission on the repository")
    parser.add_argument("--cache", type=Path, default=Path(".cache/assets"), help="where downloads are kept")
    parser.add_argument("--no-download", action="store_true", help="check files only, not release packages")
    parser.add_argument("--github-output", type=Path, help="append needs_review and auto_merge to this file")
    args = parser.parse_args(argv)

    cache = AssetCache(args.cache, downloader)
    if args.base_root is not None:
        report = validate_pr(
            args.root, args.base_root, cache, args.author, args.author_permission, not args.no_download
        )
    else:
        report = validate_repo(args.root, cache, not args.no_download)

    sys.stdout.write(report.render())
    output = args.github_output or (Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None)
    if output is not None:
        with output.open("a", encoding="utf-8") as f:
            f.write(f"needs_review={'true' if report.needs_review else 'false'}\n")
            f.write(f"auto_merge={'true' if report.auto_merge else 'false'}\n")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
