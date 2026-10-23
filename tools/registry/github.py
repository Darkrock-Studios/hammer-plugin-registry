"""The GitHub calls the update bot makes, behind an interface that tests replace."""

import base64
import email.message
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .fetch import USER_AGENT


@dataclass
class Asset:
    name: str
    url: str
    size: int


@dataclass
class GitHubRelease:
    tag: str
    draft: bool
    prerelease: bool
    published_at: str | None
    assets: list[Asset] = field(default_factory=list)


@dataclass
class PullRequest:
    number: int
    open: bool
    merged: bool
    title: str
    body: str
    url: str


class GitHub(Protocol):
    def list_releases(self, repo_id: int) -> list[GitHubRelease]:
        """Every release of the repository with this numeric id."""

    def branch_sha(self, branch: str) -> str:
        """The commit a branch of the registry points at."""

    def create_or_reset_branch(self, branch: str, sha: str) -> None:
        """Points a registry branch at a commit, creating it if needed."""

    def put_file(self, path: str, content: bytes, message: str, branch: str, blob_sha: str | None) -> None:
        """Commits one file to a registry branch. blob_sha is the file's current git blob id."""

    def find_prs(self, head_branch: str) -> list[PullRequest]:
        """The registry's pull requests from this branch, open and closed."""

    def create_pr(self, head: str, base: str, title: str, body: str) -> int:
        """Opens a pull request on the registry and returns its number."""

    def update_pr(self, number: int, title: str, body: str) -> None:
        """Changes a registry pull request's title and body."""

    def add_labels(self, number: int, labels: list[str]) -> None:
        """Labels a registry pull request."""


class GitHubError(Exception):
    pass


class RestGitHub:
    """GitHub's REST API over urllib. `repo` is the registry, as owner/name."""

    def __init__(self, token: str, repo: str, api: str = "https://api.github.com"):
        self.token = token
        self.repo = repo
        self.api = api.rstrip("/")

    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[object, email.message.Message]:
        url = path if path.startswith("https://") else f"{self.api}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                raw = response.read()
                # The message itself, not a dict: its get() ignores case, and HTTP/2 lowercases names.
                return (json.loads(raw) if raw else None), response.headers
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise GitHubError(f"{method} {url}: {e.code} {detail}") from None

    def _paged(self, path: str) -> list:
        items, url = [], path
        while url:
            page, headers = self._request("GET", url)
            items.extend(page)
            match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
            url = match.group(1) if match else None
        return items

    def list_releases(self, repo_id: int) -> list[GitHubRelease]:
        return [
            GitHubRelease(
                tag=r["tag_name"],
                draft=r["draft"],
                prerelease=r["prerelease"],
                published_at=r.get("published_at"),
                assets=[Asset(a["name"], a["browser_download_url"], a["size"]) for a in r.get("assets", [])],
            )
            for r in self._paged(f"/repositories/{int(repo_id)}/releases?per_page=100")
        ]

    def branch_sha(self, branch: str) -> str:
        ref, _ = self._request("GET", f"/repos/{self.repo}/git/ref/heads/{urllib.parse.quote(branch)}")
        return ref["object"]["sha"]

    def create_or_reset_branch(self, branch: str, sha: str) -> None:
        try:
            self._request("POST", f"/repos/{self.repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        except GitHubError as e:
            if ": 422 " not in str(e):
                raise
            self._request(
                "PATCH",
                f"/repos/{self.repo}/git/refs/heads/{urllib.parse.quote(branch)}",
                {"sha": sha, "force": True},
            )

    def put_file(self, path: str, content: bytes, message: str, branch: str, blob_sha: str | None) -> None:
        body = {"message": message, "content": base64.b64encode(content).decode(), "branch": branch}
        if blob_sha:
            body["sha"] = blob_sha
        self._request("PUT", f"/repos/{self.repo}/contents/{urllib.parse.quote(path)}", body)

    def find_prs(self, head_branch: str) -> list[PullRequest]:
        owner = self.repo.split("/")[0]
        query = urllib.parse.urlencode({"head": f"{owner}:{head_branch}", "state": "all", "per_page": 100})
        return [
            PullRequest(
                number=p["number"],
                open=p["state"] == "open",
                merged=p.get("merged_at") is not None,
                title=p.get("title") or "",
                body=p.get("body") or "",
                url=p.get("html_url") or "",
            )
            for p in self._paged(f"/repos/{self.repo}/pulls?{query}")
        ]

    def create_pr(self, head: str, base: str, title: str, body: str) -> int:
        pr, _ = self._request(
            "POST", f"/repos/{self.repo}/pulls", {"head": head, "base": base, "title": title, "body": body}
        )
        return pr["number"]

    def update_pr(self, number: int, title: str, body: str) -> None:
        self._request("PATCH", f"/repos/{self.repo}/pulls/{number}", {"title": title, "body": body})

    def add_labels(self, number: int, labels: list[str]) -> None:
        self._request("POST", f"/repos/{self.repo}/issues/{number}/labels", {"labels": labels})
