import http.client
import json

from tools.registry import github as github_mod
from tools.registry.github import RestGitHub


class FakeResponse:
    def __init__(self, body, headers: dict[str, str]):
        self._body = json.dumps(body).encode()
        self.headers = http.client.HTTPMessage()
        for name, value in headers.items():
            self.headers[name] = value

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def release(tag):
    return {"tag_name": tag, "draft": False, "prerelease": False, "published_at": None, "assets": []}


def test_pagination_follows_a_lowercase_link_header(monkeypatch):
    pages = {
        "https://api.github.com/repositories/7/releases?per_page=100": FakeResponse(
            [release("v1.0.0")], {"link": '<https://api.github.com/repositories/7/releases?page=2>; rel="next"'}
        ),
        "https://api.github.com/repositories/7/releases?page=2": FakeResponse([release("v0.9.0")], {}),
    }
    requested = []

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        return pages[request.full_url]

    monkeypatch.setattr(github_mod.urllib.request, "urlopen", fake_urlopen)
    releases = RestGitHub("token", "owner/registry").list_releases(7)
    assert [r.tag for r in releases] == ["v1.0.0", "v0.9.0"]
    assert len(requested) == 2
