from app.catalog import apply_audio_language, upsert_parsed_channel
from app.db import connect, init_db
from app.detect import detect, suggest_adult
from app.playlist import ParsedChannel


def test_tvg_beats_group_and_title():
    found = detect("fr", "CA", "US | News", "English Breakfast (UK)")
    assert found.language == "fr"
    assert found.language_step == "tvg"
    assert found.country == "CA"
    assert found.country_step == "tvg"


def test_group_title_codes_and_country_names():
    found = detect(None, None, "UK | Sports", "Match Night")
    assert found.language is None
    assert found.country == "GB"
    assert found.country_step == "group"
    both = detect(None, None, "FR - Cinema", "Soir")
    assert both.language == "fr"
    assert both.country == "FR"
    assert both.language_step == "group"


def test_audio_beats_title_and_loses_to_tvg(tmp_path):
    database = tmp_path / "t.sqlite"
    init_db(str(database))
    conn = connect(str(database))
    try:
        upsert_parsed_channel(
            conn,
            ParsedChannel(url="http://example.test/a.ts", title="Evening News (FR)"),
            playlist_id=None,
            device_id=None,
        )
        title_row = conn.execute("SELECT * FROM channels WHERE url = ?", ("http://example.test/a.ts",)).fetchone()
        assert title_row["language"] == "fr"
        assert title_row["language_step"] == "title"
        apply_audio_language(conn, title_row["id"], "eng")
        audio_row = conn.execute("SELECT * FROM channels WHERE id = ?", (title_row["id"],)).fetchone()
        assert audio_row["language"] == "en"
        assert audio_row["language_step"] == "audio"
        assert audio_row["country"] == "FR"

        upsert_parsed_channel(
            conn,
            ParsedChannel(url="http://example.test/b.ts", title="News", tvg_language="fr"),
            playlist_id=None,
            device_id=None,
        )
        locked = conn.execute("SELECT * FROM channels WHERE url = ?", ("http://example.test/b.ts",)).fetchone()
        apply_audio_language(conn, locked["id"], "eng")
        kept = conn.execute("SELECT language, language_step FROM channels WHERE id = ?", (locked["id"],)).fetchone()
        assert kept["language"] == "fr"
        assert kept["language_step"] == "tvg"
    finally:
        conn.close()


def test_title_heuristic_is_last():
    found = detect(None, None, None, "Travel Hour (UK)")
    assert found.country == "GB"
    assert found.country_step == "title"
    named = detect(None, None, None, "The English Hour")
    assert named.language == "en"
    assert named.language_step == "title"


def test_adult_suggestions_skip_adult_swim():
    assert suggest_adult("Midnight XXX", None) is True
    assert suggest_adult("Cinema", "Adult") is True
    assert suggest_adult("Late Show 18+", None) is True
    assert suggest_adult("Adult Swim East", "Adult Swim") is False
    assert suggest_adult("City News", "News") is False
