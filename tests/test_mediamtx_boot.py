import os
import subprocess
import time
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "app" / "mediamtx.yml"
IMAGE = "bluenviron/mediamtx:1.16.2"


def _docker() -> list[str]:
    probe = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.returncode == 0:
        return ["docker"]
    return ["sudo", "docker"]


def test_shipped_config_keeps_mediamtx_1_16_2_running():
    text = CONFIG.read_text(encoding="utf-8")
    assert "webrtcIPsFromInterfaces: true" in text
    docker = _docker()
    name = f"home-iptv-mtx-boot-{os.getpid()}"
    subprocess.run([*docker, "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = subprocess.run(
        [
            *docker,
            "run",
            "-d",
            "--name",
            name,
            "-v",
            f"{CONFIG}:/mediamtx.yml:ro",
            IMAGE,
            "/mediamtx.yml",
        ],
        capture_output=True,
        text=True,
    )
    assert started.returncode == 0, started.stderr
    try:
        time.sleep(2)
        state = subprocess.run(
            [*docker, "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", name],
            capture_output=True,
            text=True,
            check=True,
        )
        logs = subprocess.run([*docker, "logs", name], capture_output=True, text=True)
        output = logs.stdout + logs.stderr
        assert state.stdout.strip() == "running 0", output
        assert "ERR:" not in output
    finally:
        subprocess.run([*docker, "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
