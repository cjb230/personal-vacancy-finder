"""Text normalization used to match the same company or job across sources."""

import hashlib
import re
import unicodedata

# Legal-form suffixes that boards include inconsistently.
_COMPANY_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "llc", "plc", "gmbh", "ag", "sa", "sas",
    "bv", "nv", "ab", "as", "oy", "spa", "srl", "sp", "z", "oo", "o", "sk", "co",
    "corp", "corporation", "company", "group", "holding", "holdings",
    # Polish forms as boards write them: "Sp. z o. o.", "sp.z o.o.", "S.K.A.", "sp.k."
    "spz", "spk", "ska", "sc",
    # Country subsidiaries: "Capgemini Polska" is Capgemini.
    "polska", "poland",
}

# Words that vary between adverts for the same role.
_TITLE_NOISE = {"m", "f", "d", "w", "x", "mfd", "fmd", "mwd", "remote", "hybrid"}


# Letters NFKD does not decompose into an ASCII base letter.
_EXTRA_FOLDS = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "ß": "ss", "đ": "d"})


def _ascii_words(text: str) -> list[str]:
    # Join dotted abbreviations first, so "S.A." and "Sp. z o.o." become "SA" and "Sp z oo".
    text = text.translate(_EXTRA_FOLDS).replace(".", "")
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", folded.lower())


def normalize_company(name: str) -> str:
    """The key that identifies a company across sources. Spaces are dropped as well as
    legal forms, so "Duck Duck Go" and "DuckDuckGo" match. Changing this needs
    renormalize.renormalize() to re-key existing rows, which the worker runs on start."""
    words = _ascii_words(name)
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return "".join(words)


UNKNOWN_COMPANY = normalize_company("(unknown)")
# Title for postings that name no role (e.g. HN posts); such postings are never merged.
PLACEHOLDER_TITLE = "Open roles (see post)"


def normalize_title(title: str) -> str:
    # Drop bracketed asides such as "(m/f/d)" or "[Remote]".
    title = re.sub(r"[(\[][^)\]]*[)\]]", " ", title)
    words = [w for w in _ascii_words(title) if w not in _TITLE_NOISE]
    return " ".join(words)


def job_fingerprint(company_name: str, title: str) -> str:
    """Same company and same normalized title count as one job.

    Location is deliberately left out: boards spell it too differently
    (Warsaw, Warszawa, Remote - Poland) to be a reliable key.
    """
    key = f"{normalize_company(company_name)}|{normalize_title(title)}"
    return hashlib.sha256(key.encode()).hexdigest()[:32]


# Very common words per language, to tell which language an advert is written in.
_LANGUAGE_WORDS = {
    "pl": {
        "i", "w", "z", "na", "do", "dla", "oraz", "jest", "się", "nie", "jako", "przez",
        "lub", "od", "że", "pracy", "doświadczenie", "wymagania", "oferujemy", "zespołu",
        "obowiązki", "znajomość", "praca", "będzie", "naszego", "twoje",
    },
    "de": {
        "und", "der", "die", "das", "mit", "für", "wir", "sie", "du", "ist", "von", "zu",
        "den", "eine", "ein", "bei", "auf", "dich", "uns", "deine", "unsere", "aufgaben",
        "erfahrung", "kenntnisse", "bieten",
    },
    "en": {
        "the", "and", "of", "to", "in", "for", "with", "you", "we", "our", "is", "are",
        "will", "experience", "team", "your", "requirements", "on", "as", "be",
    },
    "es": {
        "el", "los", "las", "del", "y", "para", "con", "una", "por", "que", "en", "experiencia",
        "trabajo", "nuestro", "equipo", "buscamos", "requisitos", "ofrecemos",
    },
    "pt": {
        "os", "das", "dos", "em", "para", "com", "uma", "não", "você", "experiência", "trabalho",
        "equipe", "nossa", "nosso", "requisitos", "oferecemos", "vaga",
    },
    "fr": {
        "le", "les", "des", "et", "pour", "avec", "une", "vous", "nous", "est", "dans", "sur",
        "expérience", "équipe", "poste", "notre", "vos",
    },
}


def detect_language(text: str | None) -> str | None:
    """'en', 'pl', 'de', 'es', 'pt' or 'fr' by counting common words; None when there is
    too little text."""
    if not text:
        return None
    words = re.findall(r"[a-zäöüßąćęłńóśźżàâçéèêëîïôûùüÿñãõáíú]+", text.lower())
    scores = {lang: sum(w in vocab for w in words) for lang, vocab in _LANGUAGE_WORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] < 3:
        return None
    # Ties go to English: adverts often mix English terms into other languages.
    return best if scores[best] > scores["en"] else "en"


def detect_title_language(title: str | None) -> str | None:
    """For adverts without a description: Polish letters in the title mark it as Polish,
    e.g. "Specjalista ds. księgowości". Anything else is too short to tell."""
    if title and re.search(r"[ąćęłńśźż]", title.lower()):
        return "pl"
    return None


def looks_polish(text: str | None) -> bool:
    """True when an advert is mostly Polish rather than English."""
    return detect_language(text) == "pl"
