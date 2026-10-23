"""Reads a .hammerplugin package and its manifest.toml."""

import io
import tomllib
import zipfile

KNOWN_APIS = frozenset({1})
DEFAULT_MEMORY_MIB = 64
MAX_MANIFEST_BYTES = 256 * 1024


class PackageError(Exception):
    pass


def read_manifest(package: bytes) -> dict:
    """Returns the parsed manifest.toml of a package, as TOML."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile as e:
        raise PackageError(f"not a zip archive: {e}") from None
    with archive:
        names = set(archive.namelist())
        if "manifest.toml" not in names:
            raise PackageError("no manifest.toml at the package root")
        if "plugin.wasm" not in names:
            raise PackageError("no plugin.wasm at the package root")
        info = archive.getinfo("manifest.toml")
        if info.file_size > MAX_MANIFEST_BYTES:
            raise PackageError(f"manifest.toml is larger than {MAX_MANIFEST_BYTES} bytes")
        raw = archive.read(info)
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise PackageError(f"manifest.toml does not parse: {e}") from None


def _list_of_str(value, where: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise PackageError(f"{where} must be an array of strings")
    return list(value)


def _tables(manifest: dict, key: str) -> list[dict]:
    value = manifest.get(key, [])
    if not isinstance(value, list) or not all(isinstance(t, dict) for t in value):
        raise PackageError(f"[[{key}]] must be an array of tables")
    return value


def summarize(manifest: dict) -> dict:
    """The manifest fields the index carries, with defaults filled in."""
    for key, kind in (("id", str), ("name", str), ("version", str), ("api", int)):
        if not isinstance(manifest.get(key), kind) or isinstance(manifest.get(key), bool):
            raise PackageError(f"manifest.toml needs `{key}` ({kind.__name__})")
    description = manifest.get("description")
    if description is not None and not isinstance(description, str):
        raise PackageError("`description` must be a string")

    permissions = manifest.get("permissions", {})
    if not isinstance(permissions, dict):
        raise PackageError("[permissions] must be a table")
    user_hosts = permissions.get("userHosts")
    if user_hosts is not None and not isinstance(user_hosts, dict):
        raise PackageError("[permissions.userHosts] must be a table")

    limits = manifest.get("limits", {})
    if not isinstance(limits, dict):
        raise PackageError("[limits] must be a table")
    memory = limits.get("memory", DEFAULT_MEMORY_MIB)
    if not isinstance(memory, int) or isinstance(memory, bool):
        raise PackageError("limits.memory must be an integer")

    return {
        "name": manifest["name"],
        "description": description,
        "api": manifest["api"],
        "languages": _list_of_str(manifest.get("languages"), "languages"),
        "exporters": [
            {"format": t.get("format"), "label": t.get("label")} for t in _tables(manifest, "exporters")
        ],
        "actions": [{"name": t.get("name"), "label": t.get("label")} for t in _tables(manifest, "actions")],
        "diagnostics": [
            {"name": t.get("name"), "label": t.get("label")} for t in _tables(manifest, "diagnostics")
        ],
        "commands": [{"name": t.get("name"), "help": t.get("help")} for t in _tables(manifest, "commands")],
        "permissions": {
            "operations": _list_of_str(permissions.get("operations"), "permissions.operations"),
            "http": _list_of_str(permissions.get("http"), "permissions.http"),
            "userHosts": dict(user_hosts) if user_hosts is not None else None,
        },
        "limits": {"memory": memory},
    }


def check_release(manifest: dict, plugin_id: str, version: str) -> list[str]:
    """Problems with a release's manifest against its registry entry."""
    problems = []
    if manifest.get("id") != plugin_id:
        problems.append(f"manifest id is {manifest.get('id')!r}, expected {plugin_id!r}")
    if manifest.get("version") != version:
        problems.append(f"manifest version is {manifest.get('version')!r}, expected {version!r}")
    api = manifest.get("api")
    if api not in KNOWN_APIS or isinstance(api, bool):
        problems.append(f"manifest api is {api!r}, known: {sorted(KNOWN_APIS)}")
    return problems


def load_release(package: bytes, plugin_id: str, version: str) -> dict:
    """Parses, checks and summarizes a release's package. Raises PackageError on any problem."""
    manifest = read_manifest(package)
    problems = check_release(manifest, plugin_id, version)
    if problems:
        raise PackageError("; ".join(problems))
    return summarize(manifest)


def drawers(summary: dict) -> set[str]:
    """The drawers a manifest summary files the plugin in."""
    kinds = {"exporters": "export", "actions": "action", "diagnostics": "diagnostic", "commands": "command"}
    return {drawer for key, drawer in kinds.items() if summary.get(key)}
