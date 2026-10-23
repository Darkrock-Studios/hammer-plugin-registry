"""plugin.toml, subjects.toml and the files of a plugin's directory."""

import datetime
import fnmatch
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import images, semver, spdx

PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
LOCALE_FILE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\.md$")
SUBJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")

DRAWER_HUNDREDS = {"export": "1", "action": "2", "diagnostic": "3", "command": "4"}

MAX_SCREENSHOTS = 6
MAX_SCREENSHOT_BYTES = 500_000
MAX_SCREENSHOT_WIDTH = 1600
ICON_SIZE = (256, 256)

# key: (type, required)
_PLUGIN_KEYS = {
    "id": (str, True),
    "official": (bool, True),
    "license": (str, True),
    "language": (str, False),
    "subject": (str, True),
    "homepage": (str, False),
    "issues": (str, False),
    "maintainers": (list, True),
}
_SOURCE_KEYS = {
    "github": (str, True),
    "repo_id": (int, True),
    "asset": (str, True),
    "tag_prefix": (str, False),
    "auto_update": (bool, False),
}
_RELEASE_KEYS = {
    "version": (str, True),
    "url": (str, True),
    "sha256": (str, True),
    "size": (int, True),
    "published": (datetime.datetime, True),
    "yanked": (bool, False),
    "yank_reason": (str, False),
}
_SUBJECT_KEYS = {"name": (str, True), "drawer": (str, True), "call": (str, True)}


@dataclass
class Release:
    version: str
    url: str
    sha256: str
    size: int
    published: datetime.datetime
    yanked: bool = False
    yank_reason: str | None = None

    @property
    def published_iso(self) -> str:
        return self.published.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Plugin:
    id: str
    official: bool
    license: str
    subject: str
    maintainers: list[str]
    github: str
    repo_id: int
    asset: str
    tag_prefix: str = "v"
    auto_update: bool = False
    language: str | None = None
    homepage: str | None = None
    issues: str | None = None
    releases: list[Release] = field(default_factory=list)

    def sorted_releases(self) -> list[Release]:
        """Oldest first."""
        return sorted(self.releases, key=lambda r: semver.sort_key(r.version))

    def newest(self) -> Release | None:
        ordered = self.sorted_releases()
        return ordered[-1] if ordered else None

    def predecessor(self, version: str) -> Release | None:
        before = [r for r in self.sorted_releases() if semver.compare(r.version, version) < 0]
        return before[-1] if before else None

    def asset_matches(self, name: str) -> bool:
        return fnmatch.fnmatchcase(name, self.asset)

    def download_prefix(self, version: str) -> str:
        return f"https://github.com/{self.github}/releases/download/{self.tag_prefix}{version}/"


def _check_table(table, spec: dict, where: str) -> list[str]:
    if not isinstance(table, dict):
        return [f"{where} must be a table"]
    errors = [f"{where}: unknown key `{key}`" for key in table if key not in spec]
    for key, (kind, required) in spec.items():
        if key not in table:
            if required:
                errors.append(f"{where}: missing required key `{key}`")
            continue
        value = table[key]
        # bool is a subclass of int; a bool where an int is expected is still wrong.
        wrong = not isinstance(value, kind) or (kind is int and isinstance(value, bool))
        if wrong:
            errors.append(f"{where}: `{key}` must be {kind.__name__}, found {type(value).__name__}")
    return errors


def _https(url) -> bool:
    if not isinstance(url, str):
        return False
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def load_subjects(path: Path) -> tuple[dict, list[str]]:
    where = path.name
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, [f"{where}: missing"]
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return {}, [f"{where}: does not parse: {e}"]
    return data, check_subjects(data, where)


def check_subjects(data: dict, where: str = "subjects.toml") -> list[str]:
    errors = []
    calls: dict[str, str] = {}
    for key, table in data.items():
        at = f"{where} [{key}]"
        if not SUBJECT_KEY.match(key):
            errors.append(f"{at}: key must be lowercase letters, digits and hyphens")
        table_errors = _check_table(table, _SUBJECT_KEYS, at)
        errors += table_errors
        if table_errors:
            continue
        drawer, call = table["drawer"], table["call"]
        if drawer not in DRAWER_HUNDREDS:
            errors.append(f"{at}: drawer must be one of {', '.join(DRAWER_HUNDREDS)}")
            continue
        if not re.fullmatch(r"[1-4][0-9]0", call):
            errors.append(f"{at}: call must be three digits ending in 0, such as \"310\"")
        elif call[0] != DRAWER_HUNDREDS[drawer]:
            errors.append(f"{at}: call {call} is not in the {drawer} drawer ({DRAWER_HUNDREDS[drawer]}00)")
        if call in calls:
            errors.append(f"{at}: call {call} is also used by [{calls[call]}]")
        calls[call] = key
    return errors


def parse_plugin(text: str, where: str) -> tuple[Plugin | None, list[str]]:
    """Parses and schema-checks plugin.toml text. Returns the plugin only when it has no errors."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return None, [f"{where}: does not parse: {e}"]

    errors = [f"{where}: unknown table `{key}`" for key in data if key not in ("plugin", "source", "releases")]
    for key in ("plugin", "source"):
        if key not in data:
            errors.append(f"{where}: missing [{key}]")
    if errors:
        return None, errors

    p, s = data["plugin"], data["source"]
    errors += _check_table(p, _PLUGIN_KEYS, f"{where} [plugin]")
    errors += _check_table(s, _SOURCE_KEYS, f"{where} [source]")
    raw_releases = data.get("releases", [])
    if not isinstance(raw_releases, list):
        errors.append(f"{where}: releases must be an array of tables ([[releases]])")
        raw_releases = []
    for i, r in enumerate(raw_releases):
        errors += _check_table(r, _RELEASE_KEYS, f"{where} [[releases]] #{i + 1}")
    if errors:
        return None, errors

    if not PLUGIN_ID.match(p["id"]):
        errors.append(f"{where}: id must be lowercase letters, digits, `-` and `_`, starting with a letter or digit")
    if not spdx.is_valid(p["license"]):
        errors.append(f"{where}: license {p['license']!r} is not a known SPDX expression")
    for key in ("homepage", "issues"):
        if key in p and not _https(p[key]):
            errors.append(f"{where}: {key} must be an https URL")
    if not p["maintainers"]:
        errors.append(f"{where}: maintainers must list at least one GitHub login")
    for login in p["maintainers"]:
        if not isinstance(login, str) or not GITHUB_LOGIN.match(login):
            errors.append(f"{where}: maintainer {login!r} is not a GitHub login")
    if not GITHUB_REPO.match(s["github"]):
        errors.append(f"{where}: source.github must be owner/repo")
    if s["repo_id"] <= 0:
        errors.append(f"{where}: source.repo_id must be the repository's numeric id")
    if not s["asset"] or "/" in s["asset"]:
        errors.append(f"{where}: source.asset must be a file name glob")
    if errors:
        return None, errors

    plugin = Plugin(
        id=p["id"],
        official=p["official"],
        license=p["license"],
        subject=p["subject"],
        maintainers=list(p["maintainers"]),
        github=s["github"],
        repo_id=s["repo_id"],
        asset=s["asset"],
        tag_prefix=s.get("tag_prefix", "v"),
        auto_update=s.get("auto_update", False),
        language=p.get("language"),
        homepage=p.get("homepage"),
        issues=p.get("issues"),
    )

    previous: str | None = None
    for i, r in enumerate(raw_releases):
        at = f"{where} [[releases]] #{i + 1}"
        version = r["version"]
        if not semver.is_valid(version):
            errors.append(f"{at}: version {version!r} is not a semantic version")
            continue
        if previous is not None and semver.compare(version, previous) <= 0:
            errors.append(f"{at}: version {version} must be higher than the release before it ({previous})")
        previous = version
        if not SHA256.match(r["sha256"]):
            errors.append(f"{at}: sha256 must be 64 lowercase hex digits")
        if r["size"] <= 0:
            errors.append(f"{at}: size must be positive")
        if r["published"].tzinfo is None:
            errors.append(f"{at}: published needs a time zone, such as 2026-10-22T14:03:00Z")
        yanked = r.get("yanked", False)
        if yanked and not r.get("yank_reason", "").strip():
            errors.append(f"{at}: yank_reason is required when yanked")
        if not yanked and "yank_reason" in r:
            errors.append(f"{at}: yank_reason is only allowed when yanked")
        errors += check_release_url(plugin, version, r["url"], at)
        plugin.releases.append(
            Release(
                version=version,
                url=r["url"],
                sha256=r["sha256"],
                size=r["size"],
                published=r["published"],
                yanked=yanked,
                yank_reason=r.get("yank_reason"),
            )
        )
    if errors:
        return None, errors
    return plugin, []


def check_release_url(plugin: Plugin, version: str, url: str, where: str) -> list[str]:
    """The URL must be an asset of the tag tag_prefix + version in the source repository."""
    prefix = plugin.download_prefix(version)
    if not url.lower().startswith(prefix.lower()) or url[: len("https://github.com/")] != "https://github.com/":
        return [f"{where}: url must start with {prefix}"]
    name = urllib.parse.unquote(url[len(prefix):])
    if not name or "/" in name or "?" in name or "#" in name:
        return [f"{where}: url must end with the asset's file name"]
    if not plugin.asset_matches(name):
        return [f"{where}: asset {name!r} does not match source.asset {plugin.asset!r}"]
    return []


def strip_code(markdown: str) -> str:
    out, fence = [], None
    for line in markdown.splitlines():
        stripped = line.lstrip()
        marker = stripped[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
            continue
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        out.append(re.sub(r"`+[^`]*`+", "", line))
    return "\n".join(out)


_RAW_HTML = re.compile(r"<(?!https?://|mailto:)[A-Za-z!/?]")


def check_plugin_files(plugin_dir: Path, where: str) -> list[str]:
    """Everything in a plugin's directory besides plugin.toml's contents."""
    errors = []
    allowed = {"plugin.toml", "description", "screenshots", "icon.png"}
    for entry in sorted(plugin_dir.iterdir()):
        if entry.name not in allowed:
            errors.append(f"{where}/{entry.name}: not allowed in a plugin directory")

    desc_dir = plugin_dir / "description"
    if not (desc_dir / "en.md").is_file():
        errors.append(f"{where}/description/en.md: missing")
    if desc_dir.is_dir():
        for entry in sorted(desc_dir.iterdir()):
            at = f"{where}/description/{entry.name}"
            if not entry.is_file() or not LOCALE_FILE.match(entry.name):
                errors.append(f"{at}: description files are <language tag>.md")
                continue
            try:
                text = entry.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                errors.append(f"{at}: not UTF-8")
                continue
            if not text.strip():
                errors.append(f"{at}: empty")
            if _RAW_HTML.search(strip_code(text)):
                errors.append(f"{at}: raw HTML is not allowed")

    shots_dir = plugin_dir / "screenshots"
    if shots_dir.exists():
        shots = sorted(shots_dir.iterdir()) if shots_dir.is_dir() else []
        if len(shots) > MAX_SCREENSHOTS:
            errors.append(f"{where}/screenshots: at most {MAX_SCREENSHOTS} screenshots")
        for shot in shots:
            at = f"{where}/screenshots/{shot.name}"
            if not shot.is_file() or shot.suffix != ".webp":
                errors.append(f"{at}: screenshots must be .webp files")
                continue
            data = shot.read_bytes()
            if len(data) > MAX_SCREENSHOT_BYTES:
                errors.append(f"{at}: {len(data)} bytes, more than {MAX_SCREENSHOT_BYTES}")
            size = images.webp_size(data)
            if size is None:
                errors.append(f"{at}: not a WebP image")
            elif size[0] > MAX_SCREENSHOT_WIDTH:
                errors.append(f"{at}: {size[0]} px wide, more than {MAX_SCREENSHOT_WIDTH}")

    icon = plugin_dir / "icon.png"
    if icon.exists():
        size = images.png_size(icon.read_bytes()) if icon.is_file() else None
        if size is None:
            errors.append(f"{where}/icon.png: not a PNG image")
        elif size != ICON_SIZE:
            errors.append(f"{where}/icon.png: {size[0]} x {size[1]}, must be 256 x 256")
    return errors


def load_plugin_dir(plugin_dir: Path, subjects: dict, where: str | None = None) -> tuple[Plugin | None, list[str]]:
    """Loads and checks one plugin directory without touching the network."""
    where = where or f"plugins/{plugin_dir.name}"
    toml_path = plugin_dir / "plugin.toml"
    if not toml_path.is_file():
        return None, [f"{where}/plugin.toml: missing"]
    try:
        text = toml_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, [f"{where}/plugin.toml: not UTF-8"]
    plugin, errors = parse_plugin(text, f"{where}/plugin.toml")
    if plugin is not None:
        if plugin.id != plugin_dir.name:
            errors.append(f"{where}/plugin.toml: id {plugin.id!r} must equal the directory name")
        if plugin.subject not in subjects:
            errors.append(f"{where}/plugin.toml: subject {plugin.subject!r} is not in subjects.toml")
    errors += check_plugin_files(plugin_dir, where)
    return (plugin if not errors else None), errors


def plugin_dirs(root: Path) -> list[Path]:
    plugins = root / "plugins"
    if not plugins.is_dir():
        return []
    return sorted(p for p in plugins.iterdir() if p.is_dir())
