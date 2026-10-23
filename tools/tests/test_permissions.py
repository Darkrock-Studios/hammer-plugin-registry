import tomllib

from tools.registry import manifest, permissions

from .conftest import fixture_manifest


def summary(text: str) -> dict:
    return manifest.summarize(tomllib.loads(text))


HARPER = fixture_manifest("harper-grammar")
LANGUAGETOOL = fixture_manifest("languagetool")


def test_no_change():
    d = permissions.diff(summary(LANGUAGETOOL), summary(LANGUAGETOOL), "0.1.0", "0.2.0")
    assert not d.needs_review
    assert d.lines() == []
    assert "no new permissions" in d.render("languagetool")


def test_new_operation():
    old = HARPER + '\n[permissions]\noperations = ["scene.read"]\n'
    new = HARPER + '\n[permissions]\noperations = ["scene.read", "entry.create"]\n'
    d = permissions.diff(summary(old), summary(new), "0.1.0", "0.2.0")
    assert d.needs_review
    assert d.new_operations == ["entry.create"]
    assert "New operation: `entry.create`" in d.render("harper-grammar")


def test_removed_operation_needs_no_review():
    old = HARPER + '\n[permissions]\noperations = ["scene.read", "entry.create"]\n'
    new = HARPER + '\n[permissions]\noperations = ["scene.read"]\n'
    assert not permissions.diff(summary(old), summary(new), "0.1.0", "0.2.0").needs_review


def test_new_http_host():
    new = LANGUAGETOOL.replace(
        'http = ["api.languagetool.org", "api.languagetoolplus.com"]',
        'http = ["api.languagetool.org", "api.languagetoolplus.com", "example.com"]',
    )
    d = permissions.diff(summary(LANGUAGETOOL), summary(new), "0.1.0", "0.2.0")
    assert d.needs_review
    assert d.new_http == ["example.com"]
    assert d.new_operations == []


def test_http_hosts_compare_without_case():
    new = LANGUAGETOOL.replace('"api.languagetool.org"', '"API.LanguageTool.org"')
    assert not permissions.diff(summary(LANGUAGETOOL), summary(new), "0.1.0", "0.2.0").needs_review


def test_user_hosts_appearing():
    old = LANGUAGETOOL.replace('[permissions.userHosts]\nsetting = "server"\n', "")
    d = permissions.diff(summary(old), summary(LANGUAGETOOL), "0.1.0", "0.2.0")
    assert d.needs_review
    assert d.user_hosts_added == {"setting": "server"}
    assert "Sends to a host the user chooses (setting `server`)" in d.render("languagetool")


def test_higher_memory():
    old = HARPER.replace("memory = 256", "memory = 128")
    d = permissions.diff(summary(old), summary(HARPER), "0.1.0", "0.2.0")
    assert d.needs_review
    assert d.memory == (128, 256)


def test_memory_from_default():
    without_limits = HARPER.replace("[limits]\nmemory = 256\n", "")
    assert summary(without_limits)["limits"]["memory"] == 64
    d = permissions.diff(summary(without_limits), summary(HARPER), "0.1.0", "0.2.0")
    assert d.memory == (64, 256)


def test_lower_memory_needs_no_review():
    new = HARPER.replace("memory = 256", "memory = 128")
    assert not permissions.diff(summary(HARPER), summary(new), "0.1.0", "0.2.0").needs_review


def test_first_release_is_compared_to_nothing():
    d = permissions.diff(None, summary(LANGUAGETOOL), None, "0.1.0")
    assert d.needs_review
    assert d.new_http == ["api.languagetool.org", "api.languagetoolplus.com"]
    assert "first release" in d.render("languagetool")
    plain = fixture_manifest("plaintext")
    assert not permissions.diff(None, summary(plain), None, "1.0.0").needs_review
