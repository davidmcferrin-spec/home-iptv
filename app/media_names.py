import re
from dataclasses import dataclass
from pathlib import Path

_EPISODE = re.compile(r"(?i)(?:^|[^A-Za-z0-9])S(\d{1,2})E(\d{1,3})")
_ALT = re.compile(r"(?i)(?:^|[^A-Za-z0-9])(\d{1,2})x(\d{2})(?=$|[^A-Za-z0-9])")
_SEASON_DIR = re.compile(r"(?i)^season[\s._-]*(\d{1,2})$|^s(\d{1,2})$")
_PAREN_YEAR = re.compile(r"\((19\d{2}|20\d{2})\)")
_DOT_YEAR = re.compile(r"(?i)(?:^|[._-])((?:19|20)\d{2})(?=$|[._-])")
_QUALITY = re.compile(
    r"(?i)\b(1080p|720p|2160p|480p|4k|uhd|bluray|blu-ray|webrip|web-dl|webdl|"
    r"hdtv|dvdrip|brrip|x264|x265|h264|h265|hevc|aac|dts|truehd|atmos|proper|"
    r"repack|extended|remux)\b"
)


@dataclass(frozen=True)
class ParsedName:
    show: str | None
    title: str
    year: int | None
    season: int | None
    episode: int | None


def tidy(text: str) -> str:
    text = text.replace(".", " ").replace("_", " ").replace("-", " ")
    text = _QUALITY.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    return text or "Untitled"


def sort_title(title: str) -> str:
    lowered = title.casefold()
    if lowered.startswith("the "):
        lowered = lowered[4:]
    return lowered


def _season_dir(name: str) -> int | None:
    match = _SEASON_DIR.fullmatch(name.strip())
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _episode_match(stem: str):
    found = _EPISODE.search(stem)
    if found:
        return found, int(found.group(1)), int(found.group(2))
    found = _ALT.search(stem)
    if found:
        return found, int(found.group(1)), int(found.group(2))
    return None, None, None


def _movie(stem: str) -> tuple[str, int | None]:
    paren = _PAREN_YEAR.search(stem)
    if paren:
        year = int(paren.group(1))
        title = tidy(stem[: paren.start()] + " " + stem[paren.end() :])
        return title, year
    if re.search(r"[._]", stem):
        years = list(_DOT_YEAR.finditer(stem))
        if years:
            last = years[-1]
            title = tidy(stem[: last.start()] + " " + stem[last.end() :])
            return title, int(last.group(1))
    return tidy(stem), None


def parse_video(rel_path: str, library_kind: str) -> ParsedName:
    parts = tuple(part for part in Path(rel_path).parts if part not in ("", ".", "/"))
    if not parts:
        return ParsedName(None, "Untitled", None, None, None)
    stem = Path(parts[-1]).stem
    parents = parts[:-1]
    season_from_folder = None
    show_from_folder = None
    for index, part in enumerate(parents):
        number = _season_dir(part)
        if number is None:
            continue
        season_from_folder = number
        if index > 0:
            show_from_folder = tidy(parents[index - 1])
        break
    if show_from_folder is None and parents:
        show_from_folder = tidy(parents[0])

    if library_kind == "movies":
        title, year = _movie(stem)
        return ParsedName(None, title, year, None, None)
    if library_kind == "misc":
        return ParsedName(None, tidy(stem), None, None, None)

    found, season, episode = _episode_match(stem)
    if found is None and season_from_folder is None:
        title, year = _movie(stem)
        show = show_from_folder or title
        return ParsedName(show, title, year, None, None)
    if found is not None:
        before = tidy(stem[: found.start()])
        after = tidy(stem[found.end() :])
        show = show_from_folder or before or "Show"
        title = after if after and after != "Untitled" else show
        if season_from_folder is not None:
            season = season_from_folder
        return ParsedName(show, title, None, season, episode)
    show = show_from_folder or tidy(stem)
    return ParsedName(show, tidy(stem), None, season_from_folder, None)
