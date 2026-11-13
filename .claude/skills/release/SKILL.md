---
name: release
description: Cut a new Domestia integration release - bump the semver version in custom_components/domestia/manifest.json, write the CHANGELOG entry, commit, and push to main to trigger the release workflow.
disable-model-invocation: true
argument-hint: "[patch|minor|major]"
allowed-tools: Bash(git *) Bash(gh *) Bash(jq *) Bash(uv run *) Bash(bash .github/scripts/release-notes.sh *)
---

# Release

Releases are triggered by pushing a changed `version` in `custom_components/domestia/manifest.json` to `main` (see "Releasing" in AGENTS.md). `release.yml` refuses to run without a matching `## <version>` section in `CHANGELOG.md`, runs the integration checks, then publishes a GitHub release that the Home Assistant Marketplace offers as an update.

Bump argument: `$ARGUMENTS` (empty, or one of `patch`, `minor`, `major`).

## 1. Preconditions

Stop and tell the user if any of these fail:

- Current branch is `main` and up to date with `origin/main` (`git fetch`, then compare).
- The working tree is clean, except for changes the user explicitly wants in this release. If there are uncommitted changes, ask whether to include them in the release commit.
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy` and `uv run pytest` pass.

## 2. Find what changed

The last released version is the `version` in `manifest.json` (`jq -r .version custom_components/domestia/manifest.json`); releases are tagged with the bare version (e.g. `2.0.0`). List commits since that tag with `git log <tag>..HEAD --oneline`, and read the diffs where a message is unclear. If there are no changes since the tag, stop: there is nothing to release.

## 3. Choose the bump (semantic versioning)

- **patch**: bug fixes, log, translation or documentation changes, internal refactors. No change users must act on.
- **minor**: new backwards-compatible behavior, e.g. a new optional setup field, a new platform or a new language.
- **major**: breaking changes, e.g. a raised minimum Home Assistant version in `hacs.json`, a removed setting, or anything that changes the entity `unique_id` or device identifiers (see "Identity" in AGENTS.md) and so breaks existing entities.

If `$ARGUMENTS` is `patch`, `minor` or `major`, use it. Otherwise propose a bump with a one-line justification and confirm it with the user (AskUserQuestion) before continuing. If an explicit argument looks too small for the changes (e.g. `patch` with a breaking change), say so and ask.

Compute the new version from the current one (`2.0.0` -> patch `2.0.1`, minor `2.1.0`, major `3.0.0`; lower components reset to 0).

## 4. Edit files

- `custom_components/domestia/manifest.json`: set `"version": "<new>"`.
- `CHANGELOG.md`: add `## <new>` directly above the previous release. Match the existing style: bullet points written for users describing behavior they will notice, not commit summaries. Wrap at about 80 columns. If a change needs user action, say what to do.

Check the notes render with `bash .github/scripts/release-notes.sh <new>`. If a setup field changed in this release, check it was updated everywhere listed under "Changing setup fields" in AGENTS.md, and flag anything missing.

## 5. Confirm, commit, push

Show the user the new version and the changelog section, and ask for confirmation before pushing. Pushing to `main` publishes a GitHub release that users are offered as an update, which cannot be cleanly undone.

On confirmation:

1. Commit with a message like `Release <new>` (plus a short summary line of the main change), following the commit attribution rules in the session.
2. `git push origin main`.

## 6. Watch the release

Find the triggered run with `gh run list --workflow release.yml --limit 1`, then follow it with `gh run watch <id> --exit-status`. Report the result and, on success, the GitHub release URL (`gh release view <new> --json url`). On failure, show the failing step's log (`gh run view <id> --log-failed`) and do not retry or force-push without asking.

Remind the user that Home Assistant offers the update under **Settings → Updates** once the release is published (a manual install is updated by copying the folder again).
