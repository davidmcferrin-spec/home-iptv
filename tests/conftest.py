import pytest
from fastapi.testclient import TestClient


def pytest_report_teststatus(report, config):
    if report.when == "call" and report.passed:
        return report.outcome, "", ""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "home-iptv.sqlite"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("EMBED_WORKER", "0")
    monkeypatch.setenv("EMBED_HUB", "0")
    monkeypatch.setenv("EMBED_DISCOVERY", "0")
    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
