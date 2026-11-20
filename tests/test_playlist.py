from pathlib import Path

import pytest

from app.playlist import PlaylistParseError, parse_playlist

FIXTURE = Path(__file__).parent / "fixtures" / "sample.m3u"


def test_parses_extinf_attributes_and_dedupes():
    result = parse_playlist(FIXTURE.read_text(encoding="utf-8"))
    by_url = {channel.url: channel for channel in result.channels}
    news = by_url["http://example.test/news.ts"]
    assert news.title == "City News Updated"
    assert news.tvg_id == "news.us"
    assert news.tvg_name == "City News"
    assert news.logo_url == "http://example.test/news.png"
    assert news.tvg_language == "en"
    assert news.tvg_country == "US"
    assert news.group_title == "News"
    cinema = by_url["http://example.test/cine.ts"]
    assert cinema.title == "Cinema FR"
    assert cinema.group_title == "Cinema"
    assert any("duplicate URL" in warning for warning in result.warnings)
    assert any("without a URL" in warning for warning in result.warnings)
    assert len(result.channels) == 6


def test_comma_inside_quoted_group_title():
    text = '#EXTM3U\n#EXTINF:-1 group-title="News, US",Channel, Name\nhttp://example.test/a.ts\n'
    channel = parse_playlist(text).channels[0]
    assert channel.group_title == "News, US"
    assert channel.title == "Channel, Name"


def test_tvg_name_used_when_display_name_missing():
    text = '#EXTM3U\n#EXTINF:-1 tvg-name="Fallback",\nhttp://example.test/a.ts\n'
    assert parse_playlist(text).channels[0].title == "Fallback"


def test_empty_playlist_is_an_error():
    with pytest.raises(PlaylistParseError):
        parse_playlist("#EXTM3U\n")
