"""Permission diff between two releases' manifests."""

from dataclasses import dataclass, field

from .manifest import DEFAULT_MEMORY_MIB

# What a plugin with no release yet is compared against: it may do nothing.
BASELINE = {
    "permissions": {"operations": [], "http": [], "userHosts": None},
    "limits": {"memory": DEFAULT_MEMORY_MIB},
}


@dataclass
class PermissionDiff:
    from_version: str | None
    to_version: str
    new_operations: list[str] = field(default_factory=list)
    new_http: list[str] = field(default_factory=list)
    user_hosts_added: dict | None = None
    memory: tuple[int, int] | None = None

    @property
    def needs_review(self) -> bool:
        return bool(self.new_operations or self.new_http or self.user_hosts_added is not None or self.memory)

    def lines(self) -> list[str]:
        out = [f"New operation: `{op}`" for op in self.new_operations]
        out += [f"New HTTP host: `{host}`" for host in self.new_http]
        if self.user_hosts_added is not None:
            setting = self.user_hosts_added.get("setting")
            where = f" (setting `{setting}`)" if setting else ""
            out.append(f"Sends to a host the user chooses{where}")
        if self.memory:
            out.append(f"Memory limit raised from {self.memory[0]} MiB to {self.memory[1]} MiB")
        return out

    def render(self, plugin_id: str) -> str:
        """Markdown for a PR body or a job summary."""
        before = self.from_version or "nothing (first release)"
        head = f"**{plugin_id}** {self.to_version}, compared to {before}:"
        if not self.needs_review:
            return f"{head} no new permissions."
        return "\n".join([head, ""] + [f"- {line}" for line in self.lines()])


def diff(old: dict | None, new: dict, from_version: str | None, to_version: str) -> PermissionDiff:
    """Compares manifest summaries (as manifest.summarize returns). `old` None means no previous release."""
    old = old or BASELINE
    old_perms, new_perms = old["permissions"], new["permissions"]
    result = PermissionDiff(from_version=from_version, to_version=to_version)
    old_ops = set(old_perms["operations"])
    result.new_operations = [op for op in new_perms["operations"] if op not in old_ops]
    old_hosts = {h.lower() for h in old_perms["http"]}
    result.new_http = [h for h in new_perms["http"] if h.lower() not in old_hosts]
    if old_perms["userHosts"] is None and new_perms["userHosts"] is not None:
        result.user_hosts_added = new_perms["userHosts"]
    old_mem, new_mem = old["limits"]["memory"], new["limits"]["memory"]
    if new_mem > old_mem:
        result.memory = (old_mem, new_mem)
    return result
