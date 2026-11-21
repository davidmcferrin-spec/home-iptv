from pathlib import Path

MEDIA_ROOT = Path("/media")

_TV = {"tv", "tv shows", "tvshows", "television", "series", "shows"}
_MOVIES = {"movies", "movie", "films", "film"}
_MISC = {"misc", "miscellaneous", "other", "others", "videos", "video"}


class PathEscape(Exception):
    pass


def media_root() -> Path:
    return MEDIA_ROOT


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError as exc:
        raise PathEscape(f"{path} cannot be resolved") from exc


def library_directory(root: Path, rel_path: str) -> Path:
    rel = Path(rel_path)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise PathEscape("path must stay inside /media")
    root_ready = _resolved(root)
    target = _resolved(root_ready / rel)
    if target != root_ready and root_ready not in target.parents:
        raise PathEscape("path must stay inside /media")
    return target


def file_inside(library_dir: Path, rel_path: str) -> Path:
    rel = Path(rel_path)
    if rel.is_absolute() or not rel.parts or ".." in rel.parts:
        raise PathEscape("path must stay inside the library")
    base = _resolved(library_dir)
    target = _resolved(base / rel)
    if base not in target.parents:
        raise PathEscape("path must stay inside the library")
    if not target.is_file():
        raise FileNotFoundError(rel_path)
    return target


def suggest_libraries(root: Path, taken: set[str]) -> list[dict]:
    if not root.is_dir():
        return []
    taken_folded = {path.casefold() for path in taken}
    found = []
    try:
        children = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    except OSError:
        return []
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            if _resolved(root) not in _resolved(child).parents:
                continue
        except PathEscape:
            continue
        folded = child.name.casefold()
        if folded in _TV:
            kind = "tv"
        elif folded in _MOVIES:
            kind = "movies"
        elif folded in _MISC:
            kind = "misc"
        else:
            continue
        if child.name.casefold() in taken_folded:
            continue
        found.append({"name": child.name, "path": child.name, "kind": kind})
    return found
