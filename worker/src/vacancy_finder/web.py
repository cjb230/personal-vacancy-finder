"""A small results page: open jobs ranked by how well they match, with buttons to mark each
one interested, not interested or applied. Served by the worker on WEB_PORT (8080), which
docker-compose publishes on 127.0.0.1 only.

Run on its own with: python -m vacancy_finder.web
"""

import html
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import psycopg

log = logging.getLogger(__name__)

PAGE_SIZE = 100
DECISIONS = {"interested": "Interested", "not_interested": "Not interested", "applied": "Applied"}
VIEWS = {
    "match": ("Matches", "s.verdict = 'match' AND r.decision IS NULL"),
    "maybe": ("Maybes", "s.verdict = 'maybe' AND r.decision IS NULL"),
    "interested": ("Interested", "r.decision = 'interested'"),
    "applied": ("Applied", "r.decision = 'applied'"),
    "not_interested": ("Not interested", "r.decision = 'not_interested'"),
    "no": ("Filtered out", "s.verdict = 'no' AND r.decision IS NULL"),
}

QUERY = """
SELECT j.id, j.title, c.name, s.verdict, s.score, s.role_level, s.reasons, r.decision,
       j.first_seen_at,
       (SELECT p.url FROM postings p WHERE p.job_id = j.id AND p.closed_at IS NULL
        ORDER BY p.last_seen_at DESC LIMIT 1) AS url,
       (SELECT string_agg(DISTINCT p.location_text, ' | ') FROM postings p
        WHERE p.job_id = j.id AND p.closed_at IS NULL) AS locations,
       (SELECT string_agg(DISTINCT so.name, ', ') FROM postings p JOIN sources so ON so.id = p.source_id
        WHERE p.job_id = j.id AND p.closed_at IS NULL) AS sources
FROM jobs j
JOIN job_scores s ON s.job_id = j.id
LEFT JOIN job_reviews r ON r.job_id = j.id
LEFT JOIN companies c ON c.id = j.company_id
WHERE j.status = 'open' AND {where}
  AND (%(q)s = '' OR j.title ILIKE %(like)s OR c.name ILIKE %(like)s)
ORDER BY s.score DESC, j.first_seen_at DESC
LIMIT %(limit)s OFFSET %(offset)s
"""

COUNTS = """
SELECT {cases} FROM jobs j JOIN job_scores s ON s.job_id = j.id
LEFT JOIN job_reviews r ON r.job_id = j.id WHERE j.status = 'open'
""".format(cases=", ".join(f"count(*) FILTER (WHERE {where})" for _, where in VIEWS.values()))

STYLE = """
:root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e5e5ea; --accent:#0a66c2;
        --good:#1a7f37; --warn:#9a6700; --bad:#b42318; --chip:#f2f2f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161618; --fg:#f2f2f7; --muted:#a1a1a6; --line:#2c2c2e; --accent:#4c9aff;
          --good:#3fb950; --warn:#d29922; --bad:#f85149; --chip:#2c2c2e; } }
* { box-sizing: border-box; }
body { margin:0; font:15px/1.45 -apple-system, system-ui, sans-serif; background:var(--bg);
       color:var(--fg); }
header { padding:16px 20px 8px; border-bottom:1px solid var(--line); }
h1 { font-size:20px; margin:0 0 10px; }
nav a { display:inline-block; margin:0 6px 8px 0; padding:4px 10px; border-radius:14px;
        background:var(--chip); color:var(--fg); text-decoration:none; font-size:14px; }
nav a.on { background:var(--accent); color:#fff; }
form.search { display:inline; } form.search input { padding:4px 8px; border:1px solid var(--line);
  border-radius:6px; background:var(--bg); color:var(--fg); }
main { padding:8px 20px 40px; max-width:1100px; }
.job { padding:12px 0; border-bottom:1px solid var(--line); }
.top { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
.score { font-weight:600; min-width:34px; }
.match .score { color:var(--good); } .maybe .score { color:var(--warn); } .no .score { color:var(--bad); }
.title a { color:var(--accent); font-weight:600; text-decoration:none; }
.meta { color:var(--muted); font-size:13px; margin:2px 0 4px 46px; }
.reasons { margin:0 0 6px 46px; font-size:13px; }
.reasons span { display:inline-block; background:var(--chip); border-radius:10px; padding:1px 8px;
                margin:2px 4px 2px 0; }
.actions { margin-left:46px; } .actions button { font-size:12px; margin-right:4px; padding:3px 8px;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); cursor:pointer; }
.actions button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.pager { margin-top:16px; } .empty { color:var(--muted); padding:24px 0; }
@media (max-width:600px) { .meta, .reasons, .actions { margin-left:0; } }
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render(view: str, q: str, page: int) -> str:
    title, where = VIEWS[view]
    with connect() as conn:
        counts = conn.execute(COUNTS).fetchone()
        rows = conn.execute(
            QUERY.format(where=where),
            {"q": q, "like": f"%{q}%", "limit": PAGE_SIZE, "offset": page * PAGE_SIZE},
        ).fetchall()

    tabs = "".join(
        f'<a class="{"on" if key == view else ""}" href="/?{urlencode({"view": key, "q": q})}">'
        f"{esc(label)} ({count})</a>"
        for (key, (label, _)), count in zip(VIEWS.items(), counts)
    )
    jobs = []
    for (job_id, job_title, company, verdict, score, level, reasons, decision, first_seen, url,
         locations, sources) in rows:
        buttons = "".join(
            f'<button name="decision" value="{key}" class="{"on" if decision == key else ""}">'
            f"{label}</button>"
            for key, label in DECISIONS.items()
        )
        clear = '<button name="decision" value="">Clear</button>' if decision else ""
        chips = "".join(f"<span>{esc(r)}</span>" for r in reasons)
        jobs.append(f"""
<div class="job {esc(verdict)}">
  <div class="top"><span class="score">{score}</span>
    <span class="title"><a href="{esc(url)}" target="_blank" rel="noopener">{esc(job_title)}</a></span>
    <span>{esc(company)}</span></div>
  <div class="meta">{esc(locations or "location not given")} · {esc(sources)} ·
    first seen {first_seen:%d %b}</div>
  <div class="reasons">{chips}</div>
  <form class="actions" method="post" action="/review">
    <input type="hidden" name="job_id" value="{job_id}">
    <input type="hidden" name="back" value="/?{esc(urlencode({"view": view, "q": q, "page": page}))}">
    {buttons}{clear}</form>
</div>""")
    pager = ""
    if len(rows) == PAGE_SIZE:
        pager = (f'<div class="pager"><a href="/?{urlencode({"view": view, "q": q, "page": page + 1})}">'
                 "Next page</a></div>")
    body = "".join(jobs) or '<div class="empty">Nothing here yet.</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vacancy finder</title><style>{STYLE}</style></head><body>
<header><h1>Vacancy finder</h1><nav>{tabs}
<form class="search" method="get"><input type="hidden" name="view" value="{esc(view)}">
<input name="q" value="{esc(q)}" placeholder="Search title or company"></form></nav></header>
<main><h2 style="font-size:16px">{esc(title)}</h2>{body}{pager}</main></body></html>"""


def save_review(job_id: int, decision: str) -> None:
    with connect() as conn:
        if decision:
            conn.execute(
                """
                INSERT INTO job_reviews (job_id, decision) VALUES (%s, %s)
                ON CONFLICT (job_id) DO UPDATE SET decision = EXCLUDED.decision, updated_at = now()
                """,
                (job_id, decision),
            )
        else:
            conn.execute("DELETE FROM job_reviews WHERE job_id = %s", (job_id,))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        params = parse_qs(url.query)
        view = params.get("view", ["match"])[0]
        view = view if view in VIEWS else "match"
        q = params.get("q", [""])[0].strip()[:100]
        try:
            page = max(0, int(params.get("page", ["0"])[0]))
        except ValueError:
            page = 0
        self._send(HTTPStatus.OK, render(view, q, page))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/review":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        form = parse_qs(self.rfile.read(length).decode())
        decision = form.get("decision", [""])[0]
        back = form.get("back", ["/"])[0]
        try:
            job_id = int(form["job_id"][0])
        except (KeyError, ValueError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if decision and decision not in DECISIONS:
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        save_review(job_id, decision)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", back if back.startswith("/") and not back.startswith("//") else "/")
        self.end_headers()

    def _send(self, status: HTTPStatus, body: str) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # quieter than the default
        log.debug("web: " + format, *args)


def serve(port: int | None = None) -> ThreadingHTTPServer:
    port = port or int(os.environ.get("WEB_PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    log.info("Results page on port %d", port)
    return server


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("WEB_PORT", "8080"))), Handler).serve_forever()
