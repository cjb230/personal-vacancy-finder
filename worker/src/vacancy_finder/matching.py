"""Scores every open job against James's criteria and stores the verdict in job_scores.

The criteria (see the project notes, 2026-10-26):
- Roles: engineering manager / lead, staff / principal IC, or director-level as long as the
  advert doesn't demand previous director experience. Python and relational databases.
- Location: fully remote (EU/Europe), hybrid in Warsaw, or hybrid elsewhere with little travel.
- Sectors: space (incl. Earth observation, GIS) first; energy, green tech, health, edtech,
  robotics next. Never tobacco or gambling / free-to-play games.
- English adverts only, and no requirement to speak Polish or another language.
- Prefers B2B; previous rate 210 PLN/hour.

Each rule adds or subtracts points and explains itself in `reasons`. A hard "no" (wrong
language, non-engineering role, location ruled out, excluded sector) makes the verdict
'no' whatever the points. Otherwise >= MATCH points is 'match', >= MAYBE is 'maybe'.
The rules are deliberately simple and readable; tune them here.
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import psycopg

log = logging.getLogger(__name__)

MATCH = 45
MAYBE = 20


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


# --- roles -----------------------------------------------------------------------------------

NON_ENGINEERING = _rx(
    r"\b(sales|account (executive|manager|director)|marketing|recruit\w*|talent|hr\b|human resources"
    r"|people (partner|ops)|finance|financial|accountant|accounting|controller|controlling|legal"
    r"|counsel|customer (success|support|service)|support (specialist|agent)|designer|copywriter"
    r"|office manager|procurement|buyer|business development|bdr|sdr|payroll|nurse|physician"
    r"|teacher|tutor|intern|internship|working student|werkstudent|praktikum|apprentice|junior"
    r"|trainee|graduate|technician|mechanic|electrician|welder|operator|assembler|warehouse"
    r"|driver|cleaner|cook|chef|barista|sales engineer|pre-?sales|solutions consultant"
    r"|product marketing|seo|community manager|social media|executive assistant"
    r"|product manag\w*|delivery manag\w*|project manag\w*|program manag\w*|facilit\w*|estates"
    r"|field engineering|gtm|go-to-market|qa|quality assurance|test automation|tester)\b"
)
ENGINEERING = _rx(
    r"engineer|developer|software|programm|data|platform|backend|back-end|infrastructure|devops"
    r"|\bsre\b|architect|technical|technology|engineering|\bcto\b|\bml\b|machine learning|python"
    r"|\bai\b|cloud|system|geospatial|\bgis\b|tech\b"
)
# Software work specifically, as opposed to e.g. propulsion or structural engineering.
SOFTWARE = _rx(
    r"software|developer|data|platform|backend|back-end|full[- ]?stack|infrastructure|devops"
    r"|\bsre\b|cloud|python|\bml\b|machine learning|\bai\b|systems|architect|engineering manager"
    r"|engineering (lead|director)|head of (engineering|technology|software|data|platform)|\bcto\b"
    r"|technology|technical lead|tech lead|flight software|ground segment|geospatial|\bgis\b"
)
# Bank titles such as "Senior Developer - VP" or "AVP" are grades for individual roles.
BANK_GRADE = _rx(r"(-|,|–)\s*(a?vp|svp)\s*$|\bavp\b")
ROLE_TIERS = [
    ("director", 35, _rx(
        r"\b(director|head of|vp|vice president|cto|chief technology)\b")),
    ("manager", 30, _rx(
        r"engineering manager|eng\.? manager|manager,? (software|engineering|data|platform|backend)"
        r"|(software|development|data|platform|backend|engineering) manager|team lead|tech lead"
        r"|technical lead|lead engineer|engineering lead|\blead\b")),
    ("staff", 30, _rx(r"\b(staff|principal)\b")),
    ("architect", 15, _rx(r"\barchitect\b")),
    ("senior", 10, _rx(r"\b(senior|sr\.?)\b")),
]
DIRECTOR_EXPERIENCE = _rx(
    r"(\d+\+?\s*years?[^.]{0,60}\b(as (a |an )?)?(director|vp|vice president|executive)\b"
    r"|(previous|prior|proven) (experience|track record) as (a |an )?(director|vp|head of)"
    r"|experience (as|in) (a |an )?(director|vp)[- ]level)"
)

# --- tech ------------------------------------------------------------------------------------

PYTHON = _rx(r"\bpython\b|\bdjango\b|\bfastapi\b|\bflask\b|\bpandas\b")
RELATIONAL = _rx(r"\bsql\b|postgres|postgresql|mysql|relational|\brdbms\b|sql server|oracle db")

# --- language --------------------------------------------------------------------------------

OTHER_LANGUAGE_NEEDED = _rx(
    r"\b(fluent|native|business[- ]level|very good|good|excellent|proficient|proficiency in"
    r"|knowledge of)\s+(german|polish|french|dutch|spanish|italian|swedish|danish|norwegian"
    r"|finnish|japanese|portuguese)\b"
    r"|\b(german|polish|french|dutch|spanish|italian|swedish|danish|norwegian|finnish|japanese"
    r"|portuguese)\b[^.\n]{0,30}\b(required|is a must|mandatory|c1|c2|b2|native|fluent)\b"
)
LANGUAGE_NAMES = {"pl": "Polish", "de": "German", "es": "Spanish", "pt": "Portuguese",
                  "fr": "French"}

# --- location --------------------------------------------------------------------------------

WARSAW = _rx(r"warsaw|warszawa")
POLAND = _rx(r"poland|polska|\bpl\b|krak[oó]w|wroc[lł]aw|gda[nń]sk|pozna[nń]|[lł][oó]d[zź]"
             r"|katowice|gliwice|szczecin|lublin|bia[lł]ystok|rzesz[oó]w|toru[nń]|bydgoszcz")
EUROPE_OK = _rx(r"europe|\beu\b|emea|\bcet\b|cest|poland|worldwide|anywhere|global|international")
REMOTE_ELSEWHERE_ONLY = _rx(
    r"\b(us|usa|u\.s\.|united states|north america|canada|latam|latin america|south america|apac"
    r"|asia|australia|new zealand|india|brazil|mexico|philippines|uk only|us[- ]based|latam only"
    r"|(us|pst|est|pacific|eastern) time ?zones?|pst|pacific time)\b"
)
TIME_ZONE_OVERLAP = _rx(
    r"\b(pst|pdt|est|edt|cst|pacific|eastern|central) (time|hours)|\b(us|u\.s\.|american|"
    r"north american|apac|asian) (time ?zones?|business hours|hours)|overlap with (pst|est|pacific"
    r"|eastern|us)|crossover w(ith|/) (pst|est)"
)
EUROPE_WIDE = _rx(r"europe|\beu\b|emea|\bcet\b|cest|worldwide|anywhere|global|international")
# Places that are clearly not in Europe, for jobs whose location is a city.
OUTSIDE_EUROPE = _rx(
    r"\b(san francisco|bay area|new york|nyc|seattle|boston|chicago|denver|boulder|golden, co"
    r"|austin|dallas|houston|atlanta|miami|los angeles|san diego|san jose|palo alto|mountain view"
    r"|washington,? d\.?c|kansas|texas|california|colorado|florida|virginia|massachusetts"
    r"|toronto|vancouver|montreal|ottawa|calgary|mexico city|s[aã]o paulo|bogot[aá]|buenos aires"
    r"|santiago|lima|sydney|melbourne|brisbane|auckland|singapore|tokyo|osaka|seoul|beijing"
    r"|shanghai|shenzhen|hong kong|taipei|bangalore|bengaluru|mumbai|delhi|hyderabad|pune|chennai"
    r"|abu dhabi|dubai|uae|united arab emirates|riyadh|doha|qatar|tel aviv|israel|cairo|lagos"
    r"|nairobi|cape town|johannesburg|manila)\b"
    # "Boulder, CO": a US state code, but not a European country code like "Munich, DE".
    r"|,\s*(?!(?:DE|FR|ES|IT|PT|NL|BE|AT|CH|UK|GB|IE|SE|NO|DK|FI|EE|LV|LT|CZ|SK|HU|RO|BG|GR|HR"
    r"|SI|PL|UA|RS|LU|EU)\b)(?-i:[A-Z]{2})\s*(?:$|\|)"
)
# Single European countries other than Poland: remote "within Spain" usually means living there.
OTHER_EU_COUNTRY = _rx(
    r"\b(germany|deutschland|spain|espa[nñ]a|france|italy|portugal|netherlands|belgium|austria"
    r"|switzerland|uk|united kingdom|england|ireland|sweden|norway|denmark|finland|estonia"
    r"|latvia|lithuania|czech|czechia|slovakia|hungary|romania|bulgaria|greece|croatia|slovenia"
    r"|serbia|ukraine)\b"
)

# --- sectors ---------------------------------------------------------------------------------

TOP_SECTORS = {"space", "earth-observation", "gis"}
GOOD_SECTORS = {"energy", "energy-markets", "green-tech", "climate", "health", "edtech", "robotics"}
# Title and company name: a bare "space" or "health" there means the work itself.
SPACE_WORDS = _rx(r"\bspace\b|satellite|earth observation|geospatial|\bgis\b|remote sensing"
                  r"|orbit|aerospace|spacecraft|\bsar\b|flight software|ground segment")
GOOD_WORDS = _rx(r"\benergy\b|power grid|\bgrid\b|solar|battery|batteries|renewable|climate"
                 r"|carbon|robot\w*|health\w*|medical|clinical|edtech|education|learning")
# Descriptions mention "this space", "office space", "health insurance" and "education budget",
# so they only count with phrases that are about the business itself.
SPACE_PHRASES = _rx(r"satellites?\b(?! office)|earth observation|geospatial|remote sensing"
                    r"|spacecraft|space (industry|sector|company|mission|systems|technology|tech)"
                    r"|new ?space\b|\bgis\b|launch vehicle|in-orbit")
GOOD_PHRASES = _rx(r"energy (company|market|trading|transition|storage|sector)|renewable energy"
                   r"|solar (energy|power|panels?)|battery storage|climate tech|cleantech|green tech"
                   r"|health ?tech|digital health|medtech|healthcare (company|platform|provider)"
                   r"|edtech|learning platform|robotics (company|platform)|industrial robots?")
EXCLUDED_WORDS = _rx(r"tobacco|nicotine|vaping|\bvape\b|casino|gambling|betting|igaming"
                     r"|sportsbook|free-to-play|\bf2p\b|slot games")

# --- pay -------------------------------------------------------------------------------------

TO_PLN = {"PLN": 1, "EUR": Decimal("4.3"), "USD": Decimal("4.0"), "GBP": Decimal("5.0"),
          "CHF": Decimal("4.5")}
MONTHLY = {"hour": 168, "day": 21, "month": 1, "year": Decimal(1) / 12}
TARGET_MONTHLY_PLN = 210 * 168  # James's previous rate, full time


@dataclass
class JobFacts:
    """What the scorer knows about one job, gathered from all of its open postings."""

    job_id: int
    title: str
    company: str | None
    company_sectors: list[str]
    company_excluded: bool
    work_modes: set[str] = field(default_factory=set)
    locations: list[str] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    languages: set[str | None] = field(default_factory=set)
    required_languages: set[str] = field(default_factory=set)
    contract_types: set[str] = field(default_factory=set)
    monthly_pln_max: Decimal | None = None


@dataclass
class Score:
    verdict: str
    score: int
    role_level: str
    reasons: list[str]


def score_job(job: JobFacts) -> Score:
    points = 0
    reasons: list[str] = []
    blockers: list[str] = []
    title = job.title or ""
    text = "\n".join(job.descriptions)
    everything = f"{title}\n{job.company or ''}\n{text}"

    # Language the advert is written in, and languages the job needs.
    known = {lang for lang in job.languages if lang}
    if known and "en" not in known:
        names = ", ".join(sorted(LANGUAGE_NAMES.get(lang, lang) for lang in known))
        blockers.append(f"advert is in {names}")
    needed = {lang for lang in job.required_languages if lang != "en"}
    if needed:
        names = ", ".join(sorted(LANGUAGE_NAMES.get(lang, lang) for lang in needed))
        blockers.append(f"requires {names}")
    elif match := OTHER_LANGUAGE_NEEDED.search(text):
        blockers.append(f"asks for another language (\"{match.group(0).strip()}\")")

    # Role level, from the title. A post listing several roles ("Director of Corp Dev ·
    # MLOps Engineer") is judged on its engineering one.
    parts = [p for p in re.split(r"\s+[·•|]\s+", title) if p]
    role_title = next((p for p in parts if ENGINEERING.search(p)), title) if len(parts) > 1 else title
    role_level = "other"
    for level, bonus, pattern in ROLE_TIERS:
        if level == "director" and (BANK_GRADE.search(role_title)
                                    or not ENGINEERING.search(role_title)):
            continue
        if pattern.search(role_title):
            role_level = level
            points += bonus
            reasons.append(f"{level}-level role (+{bonus})")
            break
    if NON_ENGINEERING.search(role_title) or not ENGINEERING.search(role_title):
        blockers.append("not an engineering role")
    if role_level == "director" and DIRECTOR_EXPERIENCE.search(text):
        points -= 20
        reasons.append("asks for previous director experience (-20)")

    # Tech.
    if PYTHON.search(title):
        points += 15
        reasons.append("Python in the title (+15)")
    elif PYTHON.search(text):
        points += 10
        reasons.append("mentions Python (+10)")
    elif text:
        points -= 10
        reasons.append("no Python mentioned (-10)")
    if RELATIONAL.search(everything):
        points += 5
        reasons.append("relational databases (+5)")

    # Location and work mode.
    place = " | ".join(job.locations)
    modes = job.work_modes - {"unknown"} or {"unknown"}
    # Jobs listed outside Europe aren't ruled out: some remote employers hire anywhere, and
    # some only ask for time-zone overlap. They are capped at maybe, to check by hand.
    caps: list[str] = []  # reasons a job can be at most a maybe
    if REMOTE_ELSEWHERE_ONLY.search(title) and not EUROPE_OK.search(title):
        caps.append(f"title names another region (\"{title}\"): check where they hire")
    outside_europe = bool(place) and bool(
        (REMOTE_ELSEWHERE_ONLY.search(place) or OUTSIDE_EUROPE.search(place))
        and not (EUROPE_WIDE.search(place) or POLAND.search(place)
                 or OTHER_EU_COUNTRY.search(place))
    )
    if "remote" in modes:
        if outside_europe:
            points += 5
            reasons.append(f"remote, listed for {place} (+5)")
            caps.append(f"remote listed for {place}: check whether they hire from Poland")
        elif POLAND.search(place) or EUROPE_WIDE.search(place):
            points += 15
            reasons.append("remote, Europe/Poland OK (+15)")
        elif OTHER_EU_COUNTRY.search(place):
            points += 5
            reasons.append(f"remote within {place} (+5)")
            caps.append(f"remote within {place}: may require living there")
        else:
            points += 5
            reasons.append("remote, region not stated (+5)")
    elif "hybrid" in modes:
        if WARSAW.search(place):
            points += 15
            reasons.append("hybrid in Warsaw (+15)")
        elif POLAND.search(place):
            reasons.append(f"hybrid in {place}: check travel")
        else:
            points -= 10
            reasons.append(f"hybrid in {place or 'unknown place'}: check travel (-10)")
            caps.append("hybrid outside Poland: check how much travel")
    elif "onsite" in modes:
        if WARSAW.search(place):
            points -= 5
            reasons.append("on-site in Warsaw (-5)")
        else:
            blockers.append(f"on-site in {place or 'unknown place'}")
    elif WARSAW.search(place):
        points += 10
        reasons.append("in Warsaw, work mode not stated (+10)")
    elif POLAND.search(place):
        points += 3
        reasons.append(f"in {place}, work mode not stated (+3)")
    elif place and not EUROPE_OK.search(place):
        points -= 10
        reasons.append(f"in {place}, work mode not stated: probably on-site (-10)")
        caps.append(f"based in {place}: check whether remote is possible")
    if TIME_ZONE_OVERLAP.search(f"{place}\n{title}\n{text}"):
        reasons.append("asks for overlap with US/other time zones: check the hours")

    # Sector.
    sectors = set(job.company_sectors)
    if job.company_excluded or EXCLUDED_WORDS.search(f"{job.company or ''}\n{title}\n{text}"):
        blockers.append("excluded sector (tobacco or gambling/free-to-play)")
    if sectors & TOP_SECTORS:
        points += 25
        reasons.append("space / Earth observation company (+25)")
    elif SPACE_WORDS.search(f"{title}\n{job.company or ''}") or SPACE_PHRASES.search(text):
        points += 15
        reasons.append("space / satellite / GIS work (+15)")
    elif sectors & GOOD_SECTORS:
        points += 12
        reasons.append(f"{', '.join(sorted(sectors & GOOD_SECTORS))} company (+12)")
    elif GOOD_WORDS.search(f"{title}\n{job.company or ''}") or GOOD_PHRASES.search(text):
        points += 6
        reasons.append("energy / climate / health / education / robotics (+6)")

    # Contract and pay.
    if any("b2b" in c for c in job.contract_types):
        points += 5
        reasons.append("B2B possible (+5)")
    if job.monthly_pln_max is not None:
        monthly = int(job.monthly_pln_max)
        if monthly >= TARGET_MONTHLY_PLN * 0.85:
            points += 10
            reasons.append(f"pay up to ~{monthly:,} PLN/month (+10)")
        elif monthly < TARGET_MONTHLY_PLN * 0.55:
            points -= 15
            reasons.append(f"pay only up to ~{monthly:,} PLN/month (-15)")

    if not text:
        reasons.append("no description collected: check the advert")

    if blockers:
        return Score("no", points, role_level, blockers + reasons)
    verdict = "match" if points >= MATCH else "maybe" if points >= MAYBE else "no"
    # A match has to be at the level James is after, and software work.
    space = bool(sectors & TOP_SECTORS) or "space / satellite / GIS work (+15)" in reasons
    senior_enough = role_level in ("director", "manager", "staff") or (
        role_level == "architect" and space)
    if verdict == "match" and caps:
        verdict = "maybe"
        reasons.append(f"{caps[0]}: at most a maybe")
    elif verdict == "match" and not senior_enough:
        verdict = "maybe"
        reasons.append(f"{role_level}-level: at most a maybe")
    elif verdict == "match" and not SOFTWARE.search(role_title):
        verdict = "maybe"
        reasons.append("not clearly software work: at most a maybe")
    return Score(verdict, points, role_level, reasons)


def monthly_pln(amount: Decimal | None, currency: str | None, period: str | None) -> Decimal | None:
    if amount is None or currency not in TO_PLN or period not in MONTHLY:
        return None
    return Decimal(amount) * TO_PLN[currency] * Decimal(MONTHLY[period])


def required_languages(source: str, raw: dict[str, Any] | None) -> set[str]:
    """Languages a posting says the job needs, where the source states them."""
    raw = raw or {}
    if source == "justjoinit":
        offer = raw.get("offer") or {}
        return {lang.get("code") for lang in offer.get("languages") or [] if lang.get("code")}
    if source == "hn-whoishiring" and raw.get("language_requirement"):
        need = raw["language_requirement"].lower()
        return {code for code, name in LANGUAGE_NAMES.items() if name.lower() in need}
    return set()


def load_facts(conn: psycopg.Connection) -> list[JobFacts]:
    rows = conn.execute(
        """
        SELECT j.id, j.title, c.name, COALESCE(c.sectors, '{}'), COALESCE(c.excluded, false),
               p.work_mode, p.location_text, p.description, p.language, p.contract_type,
               p.salary_max, p.salary_min, p.salary_currency, p.salary_period, p.raw, s.key
        FROM jobs j
        JOIN postings p ON p.job_id = j.id AND p.closed_at IS NULL
        JOIN sources s ON s.id = p.source_id
        LEFT JOIN companies c ON c.id = j.company_id
        WHERE j.status = 'open'
        ORDER BY j.id
        """
    ).fetchall()
    facts: dict[int, JobFacts] = {}
    for (job_id, title, company, sectors, excluded, mode, location, description, language,
         contract, salary_max, salary_min, currency, period, raw, source) in rows:
        job = facts.get(job_id)
        if job is None:
            job = facts[job_id] = JobFacts(job_id, title, company, list(sectors), excluded)
        job.work_modes.add(mode)
        if location:
            job.locations.append(location)
        if description:
            job.descriptions.append(description)
        job.languages.add(language)
        job.required_languages |= required_languages(source, raw)
        if contract:
            job.contract_types.add(contract.lower())
        pay = monthly_pln(salary_max or salary_min, currency, period)
        if pay is not None and (job.monthly_pln_max is None or pay > job.monthly_pln_max):
            job.monthly_pln_max = pay
    return list(facts.values())


def score_all(conn: psycopg.Connection) -> dict[str, int]:
    """Rescore every open job; scores of closed jobs are kept as they were. Returns counts."""
    counts: dict[str, int] = defaultdict(int)
    with conn.transaction():
        for job in load_facts(conn):
            result = score_job(job)
            counts[result.verdict] += 1
            conn.execute(
                """
                INSERT INTO job_scores (job_id, verdict, score, role_level, reasons)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (job_id) DO UPDATE SET
                    verdict = EXCLUDED.verdict, score = EXCLUDED.score,
                    role_level = EXCLUDED.role_level, reasons = EXCLUDED.reasons,
                    scored_at = now()
                """,
                (job.job_id, result.verdict, result.score, result.role_level, result.reasons),
            )
    log.info("Scored open jobs: %d match, %d maybe, %d no",
             counts.get("match", 0), counts.get("maybe", 0), counts.get("no", 0))
    return dict(counts)
