import re
from dataclasses import dataclass

# Two-letter tokens that are both a language and a country in playlist group titles.
CODE_BOTH = {
    "DE": ("de", "DE"),
    "FR": ("fr", "FR"),
    "ES": ("es", "ES"),
    "IT": ("it", "IT"),
    "PT": ("pt", "PT"),
    "NL": ("nl", "NL"),
    "PL": ("pl", "PL"),
    "RU": ("ru", "RU"),
    "TR": ("tr", "TR"),
    "SV": ("sv", "SE"),
    "DA": ("da", "DK"),
    "FI": ("fi", "FI"),
    "NO": ("no", "NO"),
    "CS": ("cs", "CZ"),
    "RO": ("ro", "RO"),
    "HU": ("hu", "HU"),
    "EL": ("el", "GR"),
    "JA": ("ja", "JP"),
    "KO": ("ko", "KR"),
    "ZH": ("zh", "CN"),
}

LANG_NAMES = {
    "english": "en",
    "french": "fr",
    "spanish": "es",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "dutch": "nl",
    "swedish": "sv",
    "norwegian": "no",
    "danish": "da",
    "finnish": "fi",
    "polish": "pl",
    "russian": "ru",
    "ukrainian": "uk",
    "arabic": "ar",
    "hebrew": "he",
    "hindi": "hi",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "turkish": "tr",
    "greek": "el",
    "czech": "cs",
    "romanian": "ro",
    "hungarian": "hu",
    "thai": "th",
    "vietnamese": "vi",
    "indonesian": "id",
}

LANG_CODES = set(LANG_NAMES.values()) | {"uk"}

ISO639_2 = {
    "eng": "en",
    "fre": "fr",
    "fra": "fr",
    "spa": "es",
    "ger": "de",
    "deu": "de",
    "ita": "it",
    "por": "pt",
    "dut": "nl",
    "nld": "nl",
    "swe": "sv",
    "nor": "no",
    "dan": "da",
    "fin": "fi",
    "pol": "pl",
    "rus": "ru",
    "ukr": "uk",
    "ara": "ar",
    "heb": "he",
    "hin": "hi",
    "jpn": "ja",
    "kor": "ko",
    "zho": "zh",
    "chi": "zh",
    "tur": "tr",
    "ell": "el",
    "gre": "el",
    "ces": "cs",
    "cze": "cs",
    "ron": "ro",
    "rum": "ro",
    "hun": "hu",
    "tha": "th",
    "vie": "vi",
    "ind": "id",
}

COUNTRY_NAMES = {
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "america": "US",
    "united kingdom": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "canada": "CA",
    "australia": "AU",
    "new zealand": "NZ",
    "ireland": "IE",
    "france": "FR",
    "germany": "DE",
    "spain": "ES",
    "italy": "IT",
    "portugal": "PT",
    "netherlands": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "poland": "PL",
    "russia": "RU",
    "ukraine": "UA",
    "mexico": "MX",
    "brazil": "BR",
    "argentina": "AR",
    "japan": "JP",
    "korea": "KR",
    "south korea": "KR",
    "china": "CN",
    "taiwan": "TW",
    "india": "IN",
    "turkey": "TR",
    "greece": "GR",
    "czech republic": "CZ",
    "czechia": "CZ",
    "romania": "RO",
    "hungary": "HU",
    "thailand": "TH",
    "vietnam": "VN",
    "indonesia": "ID",
}

COUNTRY_CODES = {
    "US", "GB", "CA", "AU", "NZ", "IE", "FR", "DE", "ES", "IT", "PT", "NL",
    "BE", "CH", "AT", "SE", "NO", "DK", "FI", "PL", "RU", "UA", "MX", "BR",
    "AR", "JP", "KR", "CN", "TW", "IN", "TR", "GR", "CZ", "RO", "HU", "TH",
    "VN", "ID", "ZA", "IL", "AE", "SA", "EG", "MA", "PH", "SG", "MY", "HK",
}

COUNTRY_ALIASES = {"UK": "GB", "USA": "US", "EL": "GR"}

# Tokens that show up in group titles and are not places or languages.
SKIP_CODES = {"HD", "SD", "TV", "CC", "UHD", "FHD", "HQ", "LQ", "UK"}

STEP_RANK = {"title": 1, "audio": 2, "group": 3, "tvg": 4, "manual": 99}

ADULT_PATTERNS = (
    re.compile(r"\bxxx\b", re.IGNORECASE),
    re.compile(r"\bporn\w*\b", re.IGNORECASE),
    re.compile(r"\berotic\w*\b", re.IGNORECASE),
    re.compile(r"\bhentai\b", re.IGNORECASE),
    re.compile(r"18\+"),
    re.compile(r"\badults?\b", re.IGNORECASE),
)
ADULT_EXCEPTIONS = (re.compile(r"\badult swim\b", re.IGNORECASE),)


@dataclass(frozen=True)
class Detection:
    language: str | None
    language_step: str | None
    country: str | None
    country_step: str | None


def normalize_language(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower().replace("_", "-")
    if not text:
        return None
    primary = text.split("-", 1)[0]
    if primary in LANG_CODES:
        return primary
    if primary in ISO639_2:
        return ISO639_2[primary]
    return LANG_NAMES.get(primary)


def normalize_country(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    named = COUNTRY_NAMES.get(text.lower())
    if named:
        return named
    upper = text.upper()
    if upper in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[upper]
    if upper in COUNTRY_CODES:
        return upper
    return None


def suggest_adult(title: str | None, group_title: str | None) -> bool:
    text = f"{title or ''} {group_title or ''}"
    for pattern in ADULT_EXCEPTIONS:
        text = pattern.sub(" ", text)
    return any(pattern.search(text) for pattern in ADULT_PATTERNS)


def _apply_code(token: str, language: str | None, country: str | None) -> tuple[str | None, str | None]:
    upper = token.upper()
    if upper in SKIP_CODES:
        if upper == "UK" and country is None:
            return language, "GB"
        return language, country
    if upper in CODE_BOTH:
        lang_code, country_code = CODE_BOTH[upper]
        if language is None:
            language = lang_code
        if country is None:
            country = country_code
        return language, country
    lower = upper.lower()
    if language is None and lower in LANG_CODES:
        language = lower
    if country is None and upper in COUNTRY_CODES:
        country = upper
    return language, country


def from_text(text: str | None, allow_bare_codes: bool) -> tuple[str | None, str | None]:
    if not text or not text.strip():
        return None, None
    language = None
    country = None
    for name, code in sorted(LANG_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            language = code
            break
    for name, code in sorted(COUNTRY_NAMES.items(), key=lambda item: len(item[0]), reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text, re.IGNORECASE):
            country = code
            break
    if allow_bare_codes:
        for token in re.findall(r"\b[A-Za-z]{2}\b", text):
            language, country = _apply_code(token, language, country)
    else:
        for token in re.findall(r"[\(\[]([A-Za-z]{2})[\)\]]", text):
            language, country = _apply_code(token, language, country)
    return language, country


def detect(
    tvg_language: str | None,
    tvg_country: str | None,
    group_title: str | None,
    title: str | None,
    audio_language: str | None = None,
) -> Detection:
    language = normalize_language(tvg_language)
    language_step = "tvg" if language else None
    country = normalize_country(tvg_country)
    country_step = "tvg" if country else None

    group_language, group_country = from_text(group_title, allow_bare_codes=True)
    if language is None and group_language:
        language, language_step = group_language, "group"
    if country is None and group_country:
        country, country_step = group_country, "group"

    audio = normalize_language(audio_language)
    if language is None and audio:
        language, language_step = audio, "audio"

    title_language, title_country = from_text(title, allow_bare_codes=False)
    if language is None and title_language:
        language, language_step = title_language, "title"
    if country is None and title_country:
        country, country_step = title_country, "title"

    return Detection(language, language_step, country, country_step)


def merge_detected(
    current: str | None,
    current_step: str | None,
    new: str | None,
    new_step: str | None,
) -> tuple[str | None, str | None]:
    if current_step == "manual":
        return current, current_step
    if new is None or new_step is None:
        return current, current_step
    if current_step is None or STEP_RANK[new_step] >= STEP_RANK[current_step]:
        return new, new_step
    return current, current_step


def guide_sort_key(guide_number: str | None) -> int:
    if not guide_number:
        return 1_000_000_000
    parts = guide_number.split(".", 1)
    try:
        major = int(parts[0])
    except ValueError:
        return 1_000_000_000
    minor = 0
    if len(parts) == 2 and parts[1].isdigit():
        minor = int(parts[1])
    return major * 1000 + minor
