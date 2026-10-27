"""Text normalization used to match the same company or job across sources."""

import hashlib
import re
import unicodedata

# Legal-form suffixes that boards include inconsistently.
_COMPANY_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "llc", "plc", "gmbh", "ag", "sa", "sas",
    "bv", "nv", "ab", "as", "oy", "spa", "srl", "sp", "z", "oo", "sk", "co",
    "corp", "corporation", "company", "group", "holding", "holdings",
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
    words = _ascii_words(name)
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


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


# Very common Polish words, to spot adverts written in Polish.
_POLISH_WORDS = {
    "i", "w", "z", "na", "do", "dla", "oraz", "jest", "się", "nie", "jako", "przez",
    "lub", "od", "że", "pracy", "doświadczenie", "wymagania", "oferujemy", "zespołu",
    "obowiązki", "znajomość", "praca", "będzie", "naszego", "twoje",
}
_ENGLISH_WORDS = {
    "the", "and", "of", "to", "in", "for", "with", "you", "we", "our", "is", "are",
    "will", "experience", "team", "your", "requirements", "on", "as", "be",
}


def looks_polish(text: str | None) -> bool:
    """True when an advert is mostly Polish rather than English."""
    if not text:
        return False
    words = re.findall(r"[a-ząćęłńóśźż]+", text.lower())
    polish = sum(w in _POLISH_WORDS for w in words)
    english = sum(w in _ENGLISH_WORDS for w in words)
    return polish > english
