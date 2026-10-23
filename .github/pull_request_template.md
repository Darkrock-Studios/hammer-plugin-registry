<!-- One plugin per pull request, please. -->

**Plugin:** `plugins/<id>/`

**What this changes:**

- [ ] A new plugin
- [ ] A release pinned by hand
- [ ] A yank
- [ ] The description, screenshots or icon
- [ ] The source, maintainers or something else

**For a new plugin:**

- [ ] `id` matches the package's manifest and the directory name.
- [ ] `repo_id` is the source repository's id (`gh api repos/<owner>/<repo> --jq .id`).
- [ ] Each release has exactly one asset matching `asset`, tagged `tag_prefix` + version.
- [ ] `python -m tools.validate` passes.

Every plugin is reviewed before it goes on the shelf. New releases are verified to come from the
plugin's own repository, and any new permission is reviewed.
