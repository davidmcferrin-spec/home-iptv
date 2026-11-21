import os
from pathlib import Path

BUNDLED = Path(__file__).with_name("mediamtx.yml")


def write_config(path: str | None = None) -> Path:
    target = Path(path or os.environ.get("MEDIAMTX_CONFIG_PATH", "/config/mediamtx.yml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    text = BUNDLED.read_text(encoding="utf-8")
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8")
    return target


def main() -> None:
    written = write_config()
    print(written)


if __name__ == "__main__":
    main()
