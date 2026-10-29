"""A small results page: open jobs ranked by how well they match, with buttons to mark each
one interested, not interested or applied, and feedback notes (reject for, rate higher or
lower for, score higher or lower) kept with a snapshot of the ad for tuning later. Filters
narrow every tab to jobs first seen recently (or since a given time), above a minimum score,
or closing soon, sorted by score, newest first or closing soonest. Served by the worker on WEB_PORT (8080), which
docker-compose publishes on 127.0.0.1 only.

Run on its own with: python -m vacancy_finder.web
"""

import html
import logging
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
from zoneinfo import ZoneInfo

import psycopg

log = logging.getLogger(__name__)

PAGE_SIZE = 100
DECISIONS = {"interested": "Interested", "not_interested": "Not interested", "applied": "Applied"}
# Feedback kinds, as (label, takes a note). Notes are kept to improve the filters later;
# score_higher/score_lower only record which way the weights should move for this job.
FEEDBACK = {
    "reject": ("Reject for:", True),
    "rate_higher": ("Rate higher for:", True),
    "rate_lower": ("Rate lower for:", True),
    "score_higher": ("Score higher", False),
    "score_lower": ("Score lower", False),
}
FEEDBACK_SHORT = {"reject": "Rejected", "rate_higher": "Rate higher", "rate_lower": "Rate lower",
                  "score_higher": "Score higher", "score_lower": "Score lower"}
NOTE_MAX = 2000
VIEWS = {
    "match": ("Matches", "s.verdict = 'match' AND r.decision IS NULL"),
    "maybe": ("Maybes", "s.verdict = 'maybe' AND r.decision IS NULL"),
    "interested": ("Interested", "r.decision = 'interested'"),
    "applied": ("Applied", "r.decision = 'applied'"),
    "not_interested": ("Not interested", "r.decision = 'not_interested'"),
    "no": ("Filtered out", "s.verdict = 'no' AND r.decision IS NULL"),
}

# First-seen filter presets, in hours.
SEEN_WITHIN = {"": "Any time", "6": "Last 6 hours", "24": "Last 24 hours", "72": "Last 3 days",
               "168": "Last 7 days", "720": "Last 30 days"}
# Closing-date filter presets: within N days (an employer deadline or the advert's expiry),
# or "deadline" for any job whose advert states a deadline still to come.
CLOSES = {"": "Any time", "2": "Within 2 days", "7": "Within 7 days",
          "deadline": "Has a stated deadline"}
SORTS = {"score": ("Best score", "s.score DESC, j.first_seen_at DESC"),
         "new": ("Newest first", "j.first_seen_at DESC, s.score DESC"),
         "closing": ("Closing soonest", "CASE WHEN jc.closes_at >= now() THEN jc.closes_at END "
                                        "NULLS LAST, s.score DESC")}
# Times are shown, and "since" is read, in this zone (Postgres has the zone data).
DISPLAY_TZ = os.environ.get("DISPLAY_TZ", "Europe/Warsaw")
FILTERS = """
  AND (%(hours)s::int IS NULL OR j.first_seen_at >= now() - make_interval(hours => %(hours)s::int))
  AND (%(since)s::timestamp IS NULL OR j.first_seen_at >= %(since)s::timestamp AT TIME ZONE %(tz)s)
  AND (%(min_score)s::int IS NULL OR s.score >= %(min_score)s::int)
  AND (%(closes_days)s::int IS NULL OR (jc.closes_at >= now()
       AND jc.closes_at < now() + make_interval(days => %(closes_days)s::int)))
  AND (NOT %(deadline_only)s OR (jc.is_deadline AND jc.closes_at >= now()))
"""

QUERY = """
SELECT j.id, j.title, c.name, s.verdict, s.score, s.role_level, s.reasons, r.decision,
       j.first_seen_at AT TIME ZONE %(tz)s, jc.closes_at AT TIME ZONE %(tz)s, jc.is_deadline,
       jc.closes_at < now(),
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
LEFT JOIN job_closing jc ON jc.job_id = j.id
WHERE j.status = 'open' AND {where} {filters}
  AND (%(q)s = '' OR j.title ILIKE %(like)s OR c.name ILIKE %(like)s)
ORDER BY {order}
LIMIT %(limit)s OFFSET %(offset)s
"""

COUNTS = """
SELECT {cases} FROM jobs j JOIN job_scores s ON s.job_id = j.id
LEFT JOIN job_reviews r ON r.job_id = j.id LEFT JOIN job_closing jc ON jc.job_id = j.id
WHERE j.status = 'open' {filters}
""".format(cases=", ".join(f"count(*) FILTER (WHERE {where})" for _, where in VIEWS.values()),
           filters=FILTERS)

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
form.filters { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin:0 0 8px;
  font-size:14px; color:var(--muted); }
form.filters select, form.filters input, form.filters button { font:inherit; padding:3px 6px;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
form.filters input[type=number] { width:64px; }
form.filters a { color:var(--accent); text-decoration:none; }
main { padding:8px 20px 40px; max-width:1100px; }
.job { padding:12px 0; border-bottom:1px solid var(--line); }
.top { display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
.score { font-weight:600; min-width:34px; }
.match .score { color:var(--good); } .maybe .score { color:var(--warn); } .no .score { color:var(--bad); }
.title a { color:var(--accent); font-weight:600; text-decoration:none; }
.meta { color:var(--muted); font-size:13px; margin:2px 0 4px 46px; }
.meta .soon { color:var(--bad); font-weight:600; }
.reasons { margin:0 0 6px 46px; font-size:13px; }
.reasons span { display:inline-block; background:var(--chip); border-radius:10px; padding:1px 8px;
                margin:2px 4px 2px 0; }
.actions { margin-left:46px; } .actions button { font-size:12px; margin-right:4px; padding:3px 8px;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); cursor:pointer; }
.actions button.on { background:var(--accent); color:#fff; border-color:var(--accent); }
.feedback { margin:6px 0 0 46px; font-size:13px; }
.feedback summary { color:var(--muted); cursor:pointer; }
.feedback form { margin:4px 0; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.feedback label { min-width:110px; }
.feedback input[type=text] { flex:1; min-width:180px; max-width:520px; padding:3px 6px;
  border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); }
.feedback button, .notes button { font-size:12px; padding:2px 8px; border:1px solid var(--line);
  border-radius:6px; background:var(--bg); color:var(--fg); cursor:pointer; }
.notes { margin:4px 0 0 46px; font-size:13px; }
.notes form { display:inline; } .notes span { display:inline-block; border:1px solid var(--line);
  border-radius:10px; padding:1px 4px 1px 8px; margin:2px 4px 2px 0; }
.notes button { border:none; padding:0 4px; color:var(--muted); }
.pager { margin-top:16px; } .empty { color:var(--muted); padding:24px 0; }
@media (max-width:600px) { .meta, .reasons, .actions, .feedback, .notes { margin-left:0; } }
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"])


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render(view: str, q: str, page: int, seen: str = "", since: str = "",
           min_score: str = "", sort: str = "score", closes: str = "") -> str:
    """seen is a SEEN_WITHIN key, since a "YYYY-MM-DDTHH:MM" local time, min_score a whole
    number, closes a CLOSES key; anything not in that form is ignored."""
    title, where = VIEWS[view]
    seen = seen if seen in SEEN_WITHIN else ""
    closes = closes if closes in CLOSES else ""
    sort = sort if sort in SORTS else "score"
    try:
        since_at = datetime.fromisoformat(since) if since else None
    except ValueError:
        since_at, since = None, ""
    try:
        min_value = int(min_score) if min_score else None
    except ValueError:
        min_value, min_score = None, ""
    filters = {"hours": int(seen) if seen else None, "since": since_at, "min_score": min_value,
               "closes_days": int(closes) if closes.isdigit() else None,
               "deadline_only": closes == "deadline", "tz": DISPLAY_TZ}
    # Carried on every link and form so the filters stay put across tabs, pages and buttons.
    keep = {k: v for k, v in {"seen": seen, "since": since, "min_score": min_score,
                              "closes": closes, "sort": "" if sort == "score" else sort}.items() if v}
    with connect() as conn:
        counts = conn.execute(COUNTS, filters).fetchone()
        rows = conn.execute(
            QUERY.format(where=where, filters=FILTERS, order=SORTS[sort][1]),
            {"q": q, "like": f"%{q}%", "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, **filters},
        ).fetchall()
        notes: dict[int, list[tuple]] = {}
        for job_id, feedback_id, kind, note in conn.execute(
            "SELECT job_id, id, kind, note FROM job_feedback WHERE job_id = ANY(%s) ORDER BY id",
            ([row[0] for row in rows],),
        ):
            notes.setdefault(job_id, []).append((feedback_id, kind, note))

    tabs = "".join(
        f'<a class="{"on" if key == view else ""}" href="/?{urlencode({"view": key, "q": q, **keep})}">'
        f"{esc(label)} ({count})</a>"
        for (key, (label, _)), count in zip(VIEWS.items(), counts)
    )
    jobs = []
    for (job_id, job_title, company, verdict, score, level, reasons, decision, first_seen,
         closes_at, is_deadline, closed_already, url, locations, sources) in rows:
        buttons = "".join(
            f'<button name="decision" value="{key}" class="{"on" if decision == key else ""}">'
            f"{label}</button>"
            for key, label in DECISIONS.items()
        )
        clear = '<button name="decision" value="">Clear</button>' if decision else ""
        chips = "".join(f"<span>{esc(r)}</span>" for r in reasons)
        back = esc(f"/?{urlencode({'view': view, 'q': q, 'page': page, **keep})}#job-{job_id}")
        jobs.append(f"""
<div class="job {esc(verdict)}" id="job-{job_id}">
  <div class="top"><span class="score">{score}</span>
    <span class="title"><a href="{esc(url)}" target="_blank" rel="noopener">{esc(job_title)}</a></span>
    <span>{esc(company)}</span></div>
  <div class="meta">{esc(locations or "location not given")} · {esc(sources)} ·
    first seen {first_seen:%d %b %H:%M}{render_closing(closes_at, is_deadline, closed_already)}</div>
  <div class="reasons">{chips}</div>
  <form class="actions" method="post" action="/review">
    <input type="hidden" name="job_id" value="{job_id}">
    <input type="hidden" name="back" value="{back}">
    {buttons}{clear}</form>
{render_notes(job_id, notes.get(job_id, []), back)}{render_feedback(job_id, back)}
</div>""")
    pager = ""
    if len(rows) == PAGE_SIZE:
        pager = (f'<div class="pager"><a href="/?{urlencode({"view": view, "q": q, "page": page + 1, **keep})}">'
                 "Next page</a></div>")
    body = "".join(jobs) or '<div class="empty">Nothing here yet.</div>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vacancy finder</title><style>{STYLE}</style></head><body>
<header><h1>Vacancy finder</h1><nav>{tabs}
<form class="search" method="get"><input type="hidden" name="view" value="{esc(view)}">
{hidden(keep)}<input name="q" value="{esc(q)}" placeholder="Search title or company"></form></nav>
{render_filters(view, q, seen, since, min_score, sort, closes)}</header>
<main><h2 style="font-size:16px">{esc(title)}</h2>{body}{pager}</main></body></html>"""


def render_closing(closes_at: datetime | None, is_deadline: bool, passed: bool) -> str:
    """" · apply by 15 Nov" for an employer deadline, " · ad ends 26 Nov" for an expiry,
    highlighted when it is less than two days away. closes_at is a DISPLAY_TZ local time."""
    if closes_at is None:
        return ""
    end_of_day = (closes_at.hour, closes_at.minute) == (23, 59)
    when = f"{closes_at:%d %b}" if end_of_day else f"{closes_at:%d %b %H:%M}"
    if passed:
        return f' · <span>{"deadline passed" if is_deadline else "ad ended"} {when}</span>'
    soon = closes_at - datetime.now(ZoneInfo(DISPLAY_TZ)).replace(tzinfo=None) < timedelta(days=2)
    label = "apply by" if is_deadline else "ad ends"
    return f' · <span class="{"soon" if soon else ""}">{label} {when}</span>'


def hidden(fields: dict) -> str:
    return "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in fields.items())


def render_filters(view: str, q: str, seen: str, since: str, min_score: str, sort: str,
                   closes: str = "") -> str:
    def options(choices: dict, chosen: str) -> str:
        return "".join(f'<option value="{esc(k)}"{" selected" if k == chosen else ""}>{esc(label)}</option>'
                       for k, label in choices.items())
    clear = (f' <a href="/?{urlencode({"view": view, "q": q})}">Clear filters</a>'
             if seen or since or min_score or closes or sort != "score" else "")
    return f"""<form class="filters" method="get">{hidden({"view": view, "q": q})}
<label>First seen <select name="seen">{options(SEEN_WITHIN, seen)}</select></label>
<label>or since <input type="datetime-local" name="since" value="{esc(since)}"></label>
<label>Min score <input type="number" name="min_score" value="{esc(min_score)}"></label>
<label>Closing <select name="closes">{options(CLOSES, closes)}</select></label>
<label>Sort <select name="sort">{options({k: label for k, (label, _) in SORTS.items()}, sort)}</select></label>
<button>Apply</button>{clear}</form>"""


def render_notes(job_id: int, notes: list[tuple], back: str) -> str:
    if not notes:
        return ""
    items = "".join(
        f'<span>{esc(FEEDBACK_SHORT[kind])}{": " + esc(note) if note else ""}'
        f'<form method="post" action="/feedback/delete"><input type="hidden" name="id" value="{fid}">'
        f'<input type="hidden" name="back" value="{back}">'
        f'<button title="Remove this note">×</button></form></span>'
        for fid, kind, note in notes
    )
    return f'<div class="notes">{items}</div>'


def render_feedback(job_id: int, back: str) -> str:
    hidden = (f'<input type="hidden" name="job_id" value="{job_id}">'
              f'<input type="hidden" name="back" value="{back}">')
    fields = "".join(
        f'<form method="post" action="/feedback">{hidden}<input type="hidden" name="kind" value="{kind}">'
        f'<label for="{kind}-{job_id}">{esc(label)}</label>'
        f'<input type="text" id="{kind}-{job_id}" name="note" maxlength="{NOTE_MAX}" required>'
        f"<button>Save</button></form>"
        for kind, (label, takes_note) in FEEDBACK.items() if takes_note
    )
    scores = "".join(
        f'<button name="kind" value="{kind}">{esc(label)}</button>'
        for kind, (label, takes_note) in FEEDBACK.items() if not takes_note
    )
    return (f'<details class="feedback"><summary>Feedback</summary>{fields}'
            f'<form method="post" action="/feedback">{hidden}{scores}</form></details>')


def save_feedback(job_id: int, kind: str, note: str = "") -> None:
    """Records a note with a copy of the ad and its current score. A rejection also marks the
    job not interested, so it leaves the Matches and Maybes lists."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO job_feedback (job_id, kind, note, title, company, ad_text, postings,
                                      verdict, score, role_level, reasons)
            SELECT j.id, %(kind)s, NULLIF(%(note)s, ''), j.title, c.name,
                   (SELECT p.description FROM postings p WHERE p.job_id = j.id
                    ORDER BY p.closed_at IS NULL DESC, p.last_seen_at DESC LIMIT 1),
                   (SELECT coalesce(jsonb_agg(jsonb_build_object(
                        'source', so.key, 'url', p.url, 'title', p.title,
                        'company_name', p.company_name, 'location_text', p.location_text,
                        'work_mode', p.work_mode, 'contract_type', p.contract_type,
                        'salary_min', p.salary_min, 'salary_max', p.salary_max,
                        'salary_currency', p.salary_currency, 'salary_period', p.salary_period,
                        'language', p.language, 'posted_at', p.posted_at,
                        'closed_at', p.closed_at, 'description', p.description)
                        ORDER BY p.last_seen_at DESC), '[]')
                    FROM postings p JOIN sources so ON so.id = p.source_id WHERE p.job_id = j.id),
                   s.verdict, s.score, s.role_level, coalesce(s.reasons, '{}')
            FROM jobs j
            LEFT JOIN companies c ON c.id = j.company_id
            LEFT JOIN job_scores s ON s.job_id = j.id
            WHERE j.id = %(job_id)s
            """,
            {"job_id": job_id, "kind": kind, "note": note},
        )
        if kind == "reject":
            conn.execute(
                """
                INSERT INTO job_reviews (job_id, decision) VALUES (%s, 'not_interested')
                ON CONFLICT (job_id) DO UPDATE SET decision = EXCLUDED.decision, updated_at = now()
                """,
                (job_id,),
            )


def delete_feedback(feedback_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM job_feedback WHERE id = %s", (feedback_id,))


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
        filters = {k: params.get(k, [""])[0].strip()[:20] for k in ("seen", "since", "min_score", "sort", "closes")}
        self._send(HTTPStatus.OK, render(view, q, page, **filters))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/review", "/feedback", "/feedback/delete"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = min(int(self.headers.get("Content-Length") or 0), 20_000)
        form = parse_qs(self.rfile.read(length).decode())
        back = form.get("back", ["/"])[0]
        try:
            key = int(form["id" if path == "/feedback/delete" else "job_id"][0])
        except (KeyError, ValueError):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        if path == "/review":
            decision = form.get("decision", [""])[0]
            if decision and decision not in DECISIONS:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            save_review(key, decision)
        elif path == "/feedback":
            kind = form.get("kind", [""])[0]
            note = form.get("note", [""])[0].strip()[:NOTE_MAX]
            if kind not in FEEDBACK or FEEDBACK[kind][1] != bool(note):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            save_feedback(key, kind, note)
        else:
            delete_feedback(key)
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


def address(port: int | None = None) -> tuple[str, int]:
    # Local-only unless told otherwise: the page can change data and has no login.
    # docker-compose sets WEB_HOST=0.0.0.0 inside the container and publishes it on 127.0.0.1.
    return os.environ.get("WEB_HOST", "127.0.0.1"), port or int(os.environ.get("WEB_PORT", "8080"))


def serve(port: int | None = None) -> ThreadingHTTPServer:
    host, port = address(port)
    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="web").start()
    log.info("Results page on %s:%d", host, port)
    return server


if __name__ == "__main__":
    logging.basicConfig(level="INFO")
    ThreadingHTTPServer(address(), Handler).serve_forever()
