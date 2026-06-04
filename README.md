# sg-distributed-systems — SCIP Indexing

This workspace contains 11 Python microservice repos that share a `core-logger`
library and all depend on `fastapi`. They're set up for **precise cross-repo
code navigation** on a Sourcegraph instance (demo.sourcegraph.com) using
[scip-python](https://github.com/sourcegraph/scip-python).

This README captures how it's wired up and the lessons learned setting it up,
so future-you doesn't relearn them the hard way.

## How indexing works here

```
╭──────────────────╮   pip install deps    ╭───────────────╮   src code-intel   ╭─────────────╮
│ service repo      │──────────────────────▶│ scip-python   │───────upload──────▶│ Sourcegraph │
│ (CI on PR merge)  │   so refs resolve     │ index.scip    │                    │   instance  │
╰──────────────────╯                        ╰───────────────╯                    ╰─────────────╯
```

- Each service repo has a GitHub Action at `.github/workflows/scip.yml` that
  generates and uploads a SCIP index **on merge to `main`**.
- `fastapi` (a repo we don't own) is indexed via Sourcegraph **auto-indexing**
  instead of CI.

## Required Sourcegraph secrets

The CI workflows need these (set at the org level so all repos inherit them):

- `SRC_ENDPOINT` = `https://demo.sourcegraph.com`
- `SRC_ACCESS_TOKEN` = a token from https://demo.sourcegraph.com/user/settings/tokens

`GITHUB_TOKEN` is provided automatically by GitHub Actions and is used to prove
the uploader has write access to the repo.

## Lessons learned

### 1. Cross-repo nav matches on `package name + version + symbol`

Sourcegraph links references across repos using the SCIP moniker, e.g.:

```
scip-python python fastapi 0.128.0 `fastapi.applications`/FastAPI#
```

For a reference in repo A to resolve to a definition in repo B, **all three
parts must match exactly** — including the version. This is the root of most
"why isn't nav working" problems.

### 2. Dependencies must be installed at index time

scip-python emits external references only for packages it can resolve. The CI
workflow does `pip install .` (plus installs `core-logger` from its repo) before
indexing, so imports like `from fastapi import FastAPI` get proper monikers
instead of being left unresolved.

### 3. Pin dependency versions to avoid version drift

Services pin `fastapi==0.128.0` in `pyproject.toml`. If left unpinned, a service
re-indexed later would emit references to whatever fastapi version was newest at
that moment, which wouldn't match the fastapi index we uploaded → broken nav.

**When you bump a pinned dependency version, you must also:**
1. Update the pin in every service's `pyproject.toml`.
2. Re-index the dependency in Sourcegraph at the new version's tag.
3. Update the Sourcegraph retention/auto-index policy to the new tag.
4. Merge the service PRs so they re-index against the new version.

There's a `# NOTE:` comment above the `dependencies` line in each
`pyproject.toml` reminding of this.

### 4. Dynamic versions break scip-python's version detection

FastAPI's `pyproject.toml` uses `dynamic = ["version"]`, so scip-python can't
read a static version and falls back to a placeholder. Fix: pass the version
explicitly in the auto-index recipe's indexer args:

```
scip-python index --project-name fastapi --project-version 0.128.0
```

### 5. Indexing a repo you don't own → use auto-indexing

`src code-intel upload` requires a GitHub token with **collaborator access** to
the target repo. You can't upload for `fastapi/fastapi`. Instead, use
Sourcegraph **auto-indexing**, which runs the indexer on Sourcegraph's own
infrastructure and bypasses that check.

Two separate pieces:
- **Configuration policy** (Site Admin → Code graph, or the repo's code graph
  settings) = *what/when* to index and retain. Target a specific **tag**
  (e.g. `0.128.0`), not a branch, so the version moniker matches.
- **Auto-index configuration** (repo's code graph page) = *how* to run the
  indexer. Sourcegraph infers a sane default; only edit it to add
  `--project-version` for dynamic-version repos.
- ⚠️ Don't click **"Infer configuration"** after customizing — it overwrites
  your edits with the inferred default.

To index a specific revision once: Precise indexes → Auto-indexing tab →
enqueue the tag/SHA.

### 6. Index upload order doesn't matter

Cross-repo references resolve at **query time**, not upload time. If a dependent
uploads before its dependency, nav silently fails until the dependency's index
lands, then starts working. No need to orchestrate ordering.

### 7. Don't manually delete old indexes

Sourcegraph keeps the tip-of-default-branch index indefinitely and expires older
ones via retention policies. Keeping old dependency indexes around is actually
*helpful* for cross-repo nav during version transitions. Tune retention policies
instead of deleting.

### 8. GitHub Action triggers on merge, not push

The workflows use `pull_request: types: [closed]` with an
`if: github.event.pull_request.merged == true` guard, and check out
`merge_commit_sha`. This means they run only when a PR is **merged** into
`main` — direct commits to `main` (e.g. admin pushes) don't trigger indexing.

### 9. scip-python 0.6.6 PathDistribution warning is harmless

You'll see `AttributeError: 'PathDistribution' object has no attribute 'name'`
followed by "Falling back to pip show approach". The index still generates
correctly. Ignore it.

### 10. `usagesForSymbol` GraphQL API has a cross-repo bug

The newer `usagesForSymbol` API drops cross-repo references when the home repo
has a high volume of same-repo references (e.g. `FastAPI`, with ~1500 internal
refs). It exhausts pagination reporting `hasNextPage: false` without ever
reaching dependent uploads. Reported to Sourcegraph engineering.

- The legacy `repository.commit.blob.lsif.references` API works correctly and is
  what the web UI uses.
- The reference panel in the UI returns same-repo results first, so for
  high-volume symbols the cross-repo refs may be buried — not a data problem.
- When querying either API, the `range` must point at the symbol's **exact**
  occurrence (0-indexed line/char, start of the identifier). An off-by-one
  range silently returns degraded results.

## Helper tools

Two scripts in this repo use the legacy `lsif.references` API to pull cross-repo
references (working around lesson #10):

- **`scip_refs.py`** — CLI. Example:
  ```bash
  python3 scip_refs.py \
    --repo github.com/fastapi/fastapi \
    --rev 8322a4445a3b25acd9b26b61192571b2d92f9bcd \
    --path fastapi/applications.py \
    --line 47 --char 6 \
    --exclude github.com/fastapi/fastapi
  ```

- **`scip_refs_ui.py`** — local web UI (stdlib only, no dependencies). Keeps your
  token server-side.
  ```bash
  python3 scip_refs_ui.py      # then open http://localhost:8765
  ```
  Pre-filled with the FastAPI example. Both require `SRC_ENDPOINT` and
  `SRC_ACCESS_TOKEN` env vars.

## Useful references

- `fastapi` 0.128.0 tag commit: `8322a4445a3b25acd9b26b61192571b2d92f9bcd`
- `FastAPI` class definition: `fastapi/applications.py` line 48 (0-indexed: 47,
  char 6)
- The canonical fastapi repo is `github.com/fastapi/fastapi` (moved from
  `tiangolo/fastapi`; PyPI metadata confirms the home page).
