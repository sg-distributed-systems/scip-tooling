#!/usr/bin/env python3
"""
Local web UI for pulling cross-repo references from Sourcegraph via the legacy
GitBlobLSIFData.references API.

Why this exists: the new usagesForSymbol GraphQL API drops cross-repo references
when the home repo has high same-repo reference volume. The legacy references API
paginates correctly, so this UI uses it.

Run:
  export SRC_ENDPOINT=https://demo.sourcegraph.com
  export SRC_ACCESS_TOKEN=sgp_...
  python3 scip_refs_ui.py            # then open http://localhost:8765

The access token stays server-side; the browser only talks to this local server.
"""
import json
import os
import sys
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ENDPOINT = os.environ.get("SRC_ENDPOINT", "").rstrip("/")
TOKEN = os.environ.get("SRC_ACCESS_TOKEN", "")
PORT = int(os.environ.get("PORT", "8765"))

if not ENDPOINT or not TOKEN:
    print("ERROR: set SRC_ENDPOINT and SRC_ACCESS_TOKEN env vars first.", file=sys.stderr)
    sys.exit(1)

QUERY = """query($repo: String!, $rev: String!, $path: String!, $line: Int!, $char: Int!, $after: String) {
  repository(name: $repo) {
    commit(rev: $rev) {
      blob(path: $path) {
        lsif {
          references(line: $line, character: $char, first: 500, after: $after) {
            nodes {
              resource { repository { name } path }
              range { start { line character } }
            }
            pageInfo { hasNextPage endCursor }
          }
        }
      }
    }
  }
}"""


def gql(variables):
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request(
        f"{ENDPOINT}/.api/graphql",
        data=body,
        headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "scip-refs-ui/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def fetch_refs(repo, rev, path, line, char, exclude):
    variables = {"repo": repo, "rev": rev, "path": path,
                 "line": line, "char": char, "after": None}
    by_repo = defaultdict(list)
    cursor = None
    pages = 0
    while True:
        pages += 1
        variables["after"] = cursor
        d = gql(variables)
        if "errors" in d and not d.get("data"):
            raise RuntimeError(json.dumps(d["errors"]))
        blob = (((d.get("data") or {}).get("repository") or {}).get("commit") or {}).get("blob")
        if not blob or not blob.get("lsif"):
            raise RuntimeError("No precise index found at that location.")
        refs = blob["lsif"]["references"]
        for n in refs["nodes"]:
            r = n["resource"]["repository"]["name"]
            if r in exclude:
                continue
            by_repo[r].append({
                "path": n["resource"]["path"],
                "line": n["range"]["start"]["line"],
                "char": n["range"]["start"]["character"],
            })
        pi = refs["pageInfo"]
        if not pi["hasNextPage"]:
            break
        cursor = pi["endCursor"]
    total = sum(len(v) for v in by_repo.values())
    return {"pages": pages, "total": total,
            "byRepo": {k: by_repo[k] for k in sorted(by_repo)},
            "endpoint": ENDPOINT}


HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SCIP Cross-Repo References</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 2rem; max-width: 1000px; margin: 0 auto; }
  h1 { font-size: 1.3rem; }
  .hint { color: #888; font-size: 0.85rem; margin-bottom: 1.5rem; }
  form { display: grid; grid-template-columns: 120px 1fr; gap: 0.6rem 1rem; align-items: center;
         background: rgba(127,127,127,0.08); padding: 1.2rem; border-radius: 8px; }
  label { font-weight: 600; text-align: right; }
  input { padding: 0.5rem; border: 1px solid rgba(127,127,127,0.4); border-radius: 5px;
          font: inherit; width: 100%; background: transparent; color: inherit; }
  .row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
  .actions { grid-column: 2; display: flex; gap: 0.8rem; align-items: center; margin-top: 0.4rem; }
  button { padding: 0.55rem 1.2rem; border: 0; border-radius: 5px; background: #0a7ea4;
           color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.5; cursor: default; }
  #status { color: #888; }
  #results { margin-top: 1.5rem; }
  .repo { margin-bottom: 1.2rem; }
  .repo h3 { font-size: 0.95rem; margin: 0 0 0.4rem; }
  .repo .count { color: #888; font-weight: normal; }
  .ref { display: block; padding: 0.2rem 0.6rem; text-decoration: none; color: #0a7ea4;
         font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem;
         border-radius: 4px; }
  .ref:hover { background: rgba(10,126,164,0.12); }
  .summary { font-weight: 600; margin-bottom: 1rem; }
  .err { color: #c0392b; white-space: pre-wrap; font-family: monospace; }
</style>
</head>
<body>
  <h1>SCIP Cross-Repo References</h1>
  <div class="hint">Uses the legacy <code>lsif.references</code> API (paginates correctly across repos).
    Point line/character at the symbol's exact occurrence. <strong>0-indexed.</strong></div>
  <form id="f">
    <label for="repo">Repo</label>
    <input id="repo" placeholder="github.com/fastapi/fastapi" value="github.com/fastapi/fastapi" required>
    <label for="rev">Revision</label>
    <input id="rev" placeholder="commit SHA or branch" value="8322a4445a3b25acd9b26b61192571b2d92f9bcd" required>
    <label for="path">File path</label>
    <input id="path" placeholder="fastapi/applications.py" value="fastapi/applications.py" required>
    <label>Line / Char</label>
    <div class="row2">
      <input id="line" type="number" placeholder="line (0-indexed)" value="47" required>
      <input id="char" type="number" placeholder="char (0-indexed)" value="6" required>
    </div>
    <label for="exclude">Exclude repos</label>
    <input id="exclude" placeholder="comma-separated, e.g. github.com/fastapi/fastapi" value="github.com/fastapi/fastapi">
    <div class="actions">
      <button type="submit" id="go">Find references</button>
      <span id="status"></span>
    </div>
  </form>
  <div id="results"></div>
<script>
const f = document.getElementById('f');
const statusEl = document.getElementById('status');
const resultsEl = document.getElementById('results');
const goBtn = document.getElementById('go');

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  goBtn.disabled = true;
  statusEl.textContent = 'Fetching…';
  resultsEl.innerHTML = '';
  const payload = {
    repo: repo.value.trim(),
    rev: rev.value.trim(),
    path: path.value.trim(),
    line: parseInt(line.value, 10),
    char: parseInt(char.value, 10),
    exclude: exclude.value.split(',').map(s => s.trim()).filter(Boolean),
  };
  try {
    const res = await fetch('/api/refs', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Request failed');
    render(data);
    statusEl.textContent = `Done (${data.pages} page${data.pages>1?'s':''} scanned)`;
  } catch (err) {
    resultsEl.innerHTML = '<div class="err">' + err.message + '</div>';
    statusEl.textContent = '';
  } finally {
    goBtn.disabled = false;
  }
});

function render(data) {
  const repos = Object.keys(data.byRepo);
  let html = `<div class="summary">${data.total} references across ${repos.length} repositor${repos.length===1?'y':'ies'}</div>`;
  for (const repo of repos) {
    const refs = data.byRepo[repo];
    html += `<div class="repo"><h3>${repo} <span class="count">(${refs.length})</span></h3>`;
    for (const r of refs) {
      const url = `${data.endpoint}/${repo}/-/blob/${r.path}?L${r.line+1}:${r.char+1}`;
      html += `<a class="ref" href="${url}" target="_blank">${r.path}:${r.line+1}:${r.char+1}</a>`;
    }
    html += '</div>';
  }
  resultsEl.innerHTML = html;
}
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path != "/api/refs":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length))
        try:
            result = fetch_refs(
                payload["repo"], payload["rev"], payload["path"],
                int(payload["line"]), int(payload["char"]),
                set(payload.get("exclude", [])),
            )
            body = json.dumps(result).encode()
            self.send_response(200)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"SCIP refs UI → http://localhost:{PORT}  (endpoint: {ENDPOINT})")
    print("Press Ctrl+C to stop.")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
