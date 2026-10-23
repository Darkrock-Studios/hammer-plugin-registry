"""Builds index.json from the registry.

    python -m tools.build_index --out index.json [--commit SHA]

Every pinned release is downloaded (or read from --cache), checked against its sha256 and size, and its
manifest copied into the index. Exits 1, writing nothing, when anything fails.
"""

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

from .registry import releases, schema
from .registry.fetch import AssetCache, Downloader, http_download

SCHEMA_VERSION = 1


def _plugin_entry(root: Path, plugin: schema.Plugin, manifests: dict[str, dict]) -> dict:
    plugin_dir = root / "plugins" / plugin.id
    rel = f"plugins/{plugin.id}"
    description = {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((plugin_dir / "description").glob("*.md"))
    }
    screenshots_dir = plugin_dir / "screenshots"
    screenshots = (
        [f"{rel}/screenshots/{p.name}" for p in sorted(screenshots_dir.glob("*.webp"))]
        if screenshots_dir.is_dir()
        else []
    )
    entry = {
        "id": plugin.id,
        "official": plugin.official,
        "license": plugin.license,
        "language": plugin.language,
        "subject": plugin.subject,
        "homepage": plugin.homepage,
        "issues": plugin.issues,
        "maintainers": plugin.maintainers,
        "source": {"github": plugin.github, "repoId": plugin.repo_id},
        "description": description,
        "icon": f"{rel}/icon.png" if (plugin_dir / "icon.png").is_file() else None,
        "screenshots": screenshots,
        "releases": [],
    }
    for release in reversed(plugin.sorted_releases()):
        item = {
            "version": release.version,
            "url": release.url,
            "sha256": release.sha256,
            "size": release.size,
            "published": release.published_iso,
            "yanked": release.yanked,
        }
        if release.yanked:
            item["yankReason"] = release.yank_reason
        item["manifest"] = manifests[release.version]
        entry["releases"].append(item)
    return entry


def build(root: Path, cache: AssetCache, commit: str, now: datetime.datetime | None = None) -> tuple[dict, list[str]]:
    errors = []
    subjects, subject_errors = schema.load_subjects(root / "subjects.toml")
    errors += subject_errors
    entries = []
    for plugin_dir in schema.plugin_dirs(root):
        plugin, plugin_errors = schema.load_plugin_dir(plugin_dir, subjects)
        errors += plugin_errors
        if plugin is None:
            continue
        manifests, release_errors = releases.load_manifests(plugin, cache, f"plugins/{plugin.id}")
        errors += release_errors
        if not release_errors:
            entries.append(_plugin_entry(root, plugin, manifests))
    now = now or datetime.datetime.now(datetime.UTC)
    index = {
        "schema": SCHEMA_VERSION,
        "generated": now.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit,
        "subjects": {key: {"name": s["name"], "drawer": s["drawer"], "call": s["call"]} for key, s in subjects.items()},
        "plugins": entries,
    }
    return index, errors


def _head_commit(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def main(argv: list[str] | None = None, downloader: Downloader = http_download) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.build_index", description=__doc__.split("\n")[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--out", type=Path, default=Path("index.json"))
    parser.add_argument("--cache", type=Path, default=Path(".cache/assets"))
    parser.add_argument("--commit", help="the registry commit the index is built from; default HEAD")
    args = parser.parse_args(argv)

    commit = args.commit or _head_commit(args.root)
    index, errors = build(args.root, AssetCache(args.cache, downloader), commit)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    args.out.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {args.out}: {len(index['plugins'])} plugins, "
          f"{sum(len(p['releases']) for p in index['plugins'])} releases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
