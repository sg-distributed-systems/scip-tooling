#!/usr/bin/env python3
"""
Pull cross-repo references for a symbol from Sourcegraph using the legacy
GitBlobLSIFData.references API (the new usagesForSymbol API has a bug that
drops cross-repo refs when the home repo has high same-repo reference volume).

Usage:
  export SRC_ENDPOINT=https://demo.sourcegraph.com
  export SRC_ACCESS_TOKEN=sgp_...

  # Point at the symbol's occurrence (0-indexed line/character):
  python3 scip_refs.py \
    --repo github.com/fastapi/fastapi \
    --rev 8322a4445a3b25acd9b26b61192571b2d92f9bcd \
    --path fastapi/applications.py \
    --line 47 --char 6 \
    --exclude github.com/fastapi/fastapi      # optional: hide same-repo refs
"""
import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict

ENDPOINT = os.environ["SRC_ENDPOINT"].rstrip("/")
TOKEN = os.environ["SRC_ACCESS_TOKEN"]

QUERY = """query($repo: String!, $rev: String!, $path: String!, $line: Int!, $char: Int!, $after: String) {
  repository(name: $repo) {
    commit(rev: $rev) {
      blob(path: $path) {
        lsif {
          references(line: $line, character: $char, first: 500, after: $after) {
            nodes {
              resource { repository { name } path }
              range { start { line character } end { line character } }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}"""


def gql(variables):
    body = json.dumps({"query": QUERY, "variables": variables})
    res = subprocess.run(
        ["curl", "-s",
         "-H", f"Authorization: token {TOKEN}",
         "-H", "Content-Type: application/json",
         f"{ENDPOINT}/.api/graphql",
         "--data-binary", body],
        capture_output=True, text=True,
    )
    return json.loads(res.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="Repository of the symbol occurrence")
    ap.add_argument("--rev", required=True, help="Commit SHA")
    ap.add_argument("--path", required=True, help="File path within the repo")
    ap.add_argument("--line", type=int, required=True, help="0-indexed line of the occurrence")
    ap.add_argument("--char", type=int, required=True, help="0-indexed start character of the occurrence")
    ap.add_argument("--exclude", action="append", default=[],
                    help="Repo name(s) to exclude from results (repeatable)")
    args = ap.parse_args()

    variables = {
        "repo": args.repo, "rev": args.rev, "path": args.path,
        "line": args.line, "char": args.char, "after": None,
    }

    by_repo = defaultdict(list)
    cursor = None
    pages = 0
    total = 0
    while True:
        pages += 1
        variables["after"] = cursor
        d = gql(variables)
        if "errors" in d and not d.get("data"):
            print("ERRORS:", json.dumps(d["errors"], indent=2), file=sys.stderr)
            sys.exit(1)
        blob = (((d.get("data") or {}).get("repository") or {}).get("commit") or {}).get("blob")
        if not blob or not blob.get("lsif"):
            print("No precise index found at that location.", file=sys.stderr)
            sys.exit(1)
        refs = blob["lsif"]["references"]
        for n in refs["nodes"]:
            repo = n["resource"]["repository"]["name"]
            if repo in args.exclude:
                continue
            by_repo[repo].append(n)
            total += 1
        pi = refs["pageInfo"]
        print(f"  ...page {pages}: scanned {len(refs['nodes'])} (kept {total})", file=sys.stderr)
        if not pi["hasNextPage"]:
            break
        cursor = pi["endCursor"]

    print(f"\n{total} references across {len(by_repo)} repositories\n")
    for repo in sorted(by_repo):
        nodes = by_repo[repo]
        print(f"## {repo}  ({len(nodes)})")
        for n in nodes:
            r = n["range"]["start"]
            print(f"   {n['resource']['path']}:{r['line']+1}:{r['character']+1}")
        print()


if __name__ == "__main__":
    main()
