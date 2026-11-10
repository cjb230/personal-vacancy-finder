"""Advert text sent from James's own browser, for jobs we only know from email alerts.

We never fetch LinkedIn pages ourselves: its terms forbid automated access, and the account
at risk would be James's. Instead, when he has a LinkedIn job open anyway, one click on the
Capture bookmarklet (installed from /capture on the results page) sends that page's job
description here. It is stored in page_captures under the same key the alert postings use
("linkedin:<job id>"), so scoring reads it as the posting's description, whether the capture
comes before or after the alert.

The bookmarklet reads only the page already on screen, once per click. It takes the selected
text if there is any, otherwise the job description block.
"""

import json
import re

import psycopg

from .normalize import detect_language

DESCRIPTION_MAX = 50_000
MIN_LENGTH = 200  # anything shorter is a mis-click, not an advert

_LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?#]*-)?(\d+)|[?&]currentJobId=(\d+)")


def external_id_for(url: str) -> str | None:
    """The alert postings' key for a job page URL, e.g. "linkedin:4012345678"; None for pages
    we have no alert parser for."""
    m = _LINKEDIN_JOB.search(url or "")
    if not m or "linkedin.com/" not in url:
        return None
    return f"linkedin:{m.group(1) or m.group(2)}"


_CHROME_LINE = re.compile(r"^(about the job|(show|see) (more|less)|…\s*(show|see) more)$", re.I)


def clean_description(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").replace("\r\n", "\n").split("\n")]
    # The section heading and the expand/collapse buttons are page furniture, not advert.
    while lines and (not lines[0].strip() or _CHROME_LINE.match(lines[0].strip())):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _CHROME_LINE.match(lines[-1].strip())):
        lines.pop()
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:DESCRIPTION_MAX]


def save_capture(conn: psycopg.Connection, url: str, description: str,
                 title: str = "", company: str = "") -> tuple[str, list[tuple[int, str]]]:
    """Store (or replace) the text for one job page. Returns its key and the (job id, title)
    of stored jobs it belongs to, which may be none yet. Raises ValueError for a page that
    isn't a LinkedIn job or text too short to be an advert."""
    external_id = external_id_for(url)
    if external_id is None:
        raise ValueError("Only LinkedIn job pages can be captured for now.")
    description = clean_description(description)
    if len(description) < MIN_LENGTH:
        raise ValueError("That is too short to be a job description. Select the description "
                         "text on the page and click Capture again.")
    job_id = external_id.partition(":")[2]
    conn.execute(
        """
        INSERT INTO page_captures (external_id, url, title, company, description, language)
        VALUES (%s, %s, NULLIF(%s, ''), NULLIF(%s, ''), %s, %s)
        ON CONFLICT (external_id) DO UPDATE SET
            url = EXCLUDED.url, title = EXCLUDED.title, company = EXCLUDED.company,
            description = EXCLUDED.description, language = EXCLUDED.language,
            captured_at = now()
        """,
        (external_id, f"https://www.linkedin.com/jobs/view/{job_id}/", title.strip()[:500],
         company.strip()[:500], description, detect_language(description)),
    )
    jobs = conn.execute(
        """
        SELECT DISTINCT j.id, j.title FROM postings p JOIN jobs j ON j.id = p.job_id
        WHERE p.external_id = %s ORDER BY j.id
        """,
        (external_id,),
    ).fetchall()
    return external_id, [(job_id, title) for job_id, title in jobs]


def save_job_description(conn: psycopg.Connection, job_id: int, text: str) -> str:
    """Store (or replace) advert text pasted on the results page for one job, any source.
    Returns the cleaned text. Raises ValueError when nothing is left after cleaning."""
    description = clean_description(text)
    if not description:
        raise ValueError("The description is empty.")
    conn.execute(
        """
        INSERT INTO job_descriptions (job_id, description, language) VALUES (%s, %s, %s)
        ON CONFLICT (job_id) DO UPDATE SET
            description = EXCLUDED.description, language = EXCLUDED.language, pasted_at = now()
        """,
        (job_id, description, detect_language(description)),
    )
    return description


# The bookmarklet, before ORIGIN is filled in. One line, and no "%": browsers percent-decode
# javascript: URLs. It posts a form to a new tab rather than calling fetch(), so the result
# page shows whether it worked.
_BOOKMARKLET = (
    "(function(){"
    "var u=location.href,m=u.match(/jobs\\/view\\/(?:[^\\/?#]*-)?(\\d+)/)||u.match(/[?&]currentJobId=(\\d+)/);"
    "if(!/linkedin\\.com$/.test(location.hostname)||!m){alert('Open a LinkedIn job first.');return;}"
    "var t=String(getSelection()).trim();"
    "if(!t){var h=[].find.call(document.querySelectorAll('h2,h3'),function(e){"
    "return /^about the job$/i.test(e.textContent.trim());});"
    "var el=document.querySelector('#job-details')||document.querySelector('.jobs-description__content')"
    "||(h&&h.parentElement);t=el?el.innerText.trim():'';}"
    "if(t.length<200){alert('Could not find the job description. Select its text and click Capture again.');return;}"
    "var q=function(s){var e=document.querySelector(s);return e?e.innerText.trim():'';};"
    "var f=document.createElement('form');f.method='post';f.action=ORIGIN+'/capture';"
    "f.target='_blank';f.acceptCharset='utf-8';f.style.display='none';"
    "var add=function(k,v){var a=document.createElement('textarea');a.name=k;a.value=v;f.appendChild(a);};"
    "add('url','https://www.linkedin.com/jobs/view/'+m[1]+'/');"
    "add('title',q('.job-details-jobs-unified-top-card__job-title')||q('h1'));"
    "add('company',q('.job-details-jobs-unified-top-card__company-name'));"
    "add('description',t);document.body.appendChild(f);f.submit();f.remove();})();"
)


def bookmarklet(origin: str) -> str:
    """The javascript: URL of the Capture bookmarklet, posting to `origin` (the address James
    reaches the results page on, e.g. "http://server:8090")."""
    return "javascript:" + _BOOKMARKLET.replace("ORIGIN", json.dumps(origin).replace('"', "'"))
