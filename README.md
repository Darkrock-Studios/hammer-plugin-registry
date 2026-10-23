# hammer-plugin-registry

The registry behind [plugins.hammer.ink](https://plugins.hammer.ink), the library of plugins for
[Hammer](https://github.com/Wavesonics/hammer-editor). It records which plugins exist, who maintains
them, and which of their releases are pinned, and publishes all of it as `index.json` on the `index`
branch, which the library reads.

The library never hosts plugins. Each release stays on its author's GitHub Releases; the registry pins
it by URL, size and SHA-256.

Every plugin is reviewed before it goes on the shelf. New releases are verified to come from the
plugin's own repository, and any new permission is reviewed.

## Layout

```
plugins/<id>/plugin.toml          what the package cannot say about itself, and the pinned releases
plugins/<id>/description/en.md    the long description, in Markdown without raw HTML
plugins/<id>/screenshots/*.webp   optional: at most 6, each at most 1600 px wide and 500 KB
plugins/<id>/icon.png             optional: 256 x 256
subjects.toml                     the subjects plugins are filed under, which give call numbers
tools/                            the validator, the update bot and the index builder
```

Everything else about a plugin (its name, short description, languages, what it adds to Hammer, and
its permissions) comes from the `manifest.toml` in its package, never from the registry.

## Submitting a plugin

1. Publish your plugin as a GitHub release of its own repository. The tag is a prefix and the
   version, such as `v1.0.0` or `my-plugin-v1.0.0` when one repository holds several plugins, and the
   release has exactly one asset matching the name you give below, such as `my-plugin.hammerplugin`.
   The version must equal the `version` in the package's manifest.
2. Fork this repository and add `plugins/<id>/`, where `<id>` is your manifest's `id`:

   ```toml
   [plugin]
   id = "my-plugin"
   official = false
   license = "MIT"                       # an SPDX identifier or expression
   language = "Rust"                     # optional: what it is written in
   subject = "grammar"                   # a key of subjects.toml
   homepage = "https://example.com"      # optional
   issues = "https://github.com/you/my-plugin/issues"   # optional
   maintainers = ["your-github-login"]

   [source]
   github = "you/my-plugin"
   repo_id = 123456789                   # gh api repos/you/my-plugin --jq .id
   asset = "my-plugin.hammerplugin"      # a glob; exactly one asset of a release must match
   tag_prefix = "v"                      # optional, "v" by default
   auto_update = true                    # optional, false by default
   ```

   and `plugins/<id>/description/en.md`, a few short paragraphs on what the plugin does. With
   `auto_update = true` you can leave out `[[releases]]`, and the bot pins your releases.
3. Run `python -m tools.validate` and open a pull request.

Pick the subject by what your plugin adds: its drawer (`export`, `action`, `diagnostic` or `command`)
must be one your manifest declares. If no subject fits, propose one in `subjects.toml` in the same pull
request. Only a repository admin may set `official`.

`maintainers` are the GitHub logins who may change the plugin's directory. The numeric `repo_id` ties
the plugin to its repository for good, so a renamed or recycled account cannot take it over.

## How releases are picked up

Every hour, the update bot looks at each plugin with `auto_update = true`. It lists the releases of
the repository with that `repo_id`, skips drafts and pre-releases, and takes those whose tag starts
with `tag_prefix` and whose version is newer than the newest pin. For each, it downloads the asset,
records its size and SHA-256, and checks that the package's manifest has the plugin's `id`, the tag's
version and a known `api`. Then it appends a `[[releases]]` entry:

- If the release asks for no new permission, the bot commits it straight to `main`.
- If it does, the bot opens a pull request labelled `needs-review` from the branch
  `registry-bot/<id>`, with the permission changes in its description. While that pull request is
  open, later releases are added to it rather than to a new one.

A plugin's first release is compared with a plugin that may do nothing, so a first release that asks
for any permission is reviewed too. Without `auto_update`, add `[[releases]]` entries yourself; the
validator downloads and checks each one.

## What needs a person

The validator runs on every pull request. It labels a pull request `needs-review`, which keeps it
from merging automatically until a person has reviewed it, when the pull request:

- adds a plugin, or removes one;
- pins a release that asks for a new operation, a new HTTP host, a host the writer chooses
  (`userHosts`), or a higher memory limit than the release before it;
- changes a plugin's source (`github`, `repo_id`, `asset` or `tag_prefix`);
- changes or removes a pinned release, other than yanking it;
- changes anything outside a plugin's directory, such as `subjects.toml`.

Changing `official` or `maintainers` needs a repository admin, and any other change to an existing
plugin needs one of its maintainers or an admin. A pull request from a maintainer, or an admin, that
needs no review (a description or screenshot edit, a yank, or a release with no new permission) is
merged automatically once validation passes.

If a reviewer closes one of the bot's pull requests without merging it, the bot stops proposing
releases of that plugin, and says so in every run's log, until a person acts. Either reopen the pull
request, and the bot updates it with every pending release, or pin the rejected versions yourself with
`yanked = true` and a `yank_reason` (see below), and the bot moves on to the releases after them.

## Yanking a release

To withdraw a release, set `yanked = true` and say why in its entry:

```toml
[[releases]]
version = "1.2.0"
# ...
yanked = true
yank_reason = "Crashes when a project has no scenes."
```

Keep the entry: a yanked release stays in the index so the library can say why it is gone, but it is
no longer offered for download. A maintainer's yank is merged automatically.

To report a plugin that is malicious or broken, [open an issue](../../issues/new?template=report-plugin.yml).

## Tools

The tools need Python 3.12 and nothing else.

```sh
python -m tools.validate                # every plugin, downloading and checking every release
python -m tools.validate --no-download  # the files only
python -m tools.build_index --out index.json
python -m tools.update_bot --dry-run    # what the bot would pin
```

The tests need pytest:

```sh
pip install -r requirements-dev.txt
python -m pytest
```

## Repository setup

The workflows expect:

- `needs-review` and `report` labels;
- "Allow auto-merge" on, and branch protection on `main` requiring the `Validate / validate` check;
- "Allow GitHub Actions to create and approve pull requests" on, for the bot's pull requests;
- branch protection on `main` that lets GitHub Actions push: the bot commits releases with no new
  permission straight to `main` through the API, as `github-actions[bot]`, so add it to the bypass
  list of any rule that requires pull requests or status checks, or the bot's commits fail every hour;
- optionally, a `LIBRARY_REFRESH_URL` variable and a `LIBRARY_REFRESH_SECRET` secret, so publishing
  the index tells the library to refresh.

`tests.yml` runs the tools' tests on every pull request and push to `main`. `validate.yml` runs on
`pull_request_target` so it can label and merge pull requests from forks. It
runs the base branch's tools and reads the pull request only as data. Pushes and merges made with the
workflow token start no other workflow, so the bot starts `publish-index.yml` itself, and that
workflow also runs hourly to catch up after automatic merges.

## License

The tools are under the [0BSD license](LICENSE). Each plugin is under its own license, given in its
`plugin.toml`.
