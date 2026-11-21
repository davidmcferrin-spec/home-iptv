import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_stats_format_and_dropped_window():
    node = shutil.which("node")
    assert node, "node is required to check playback stats"
    script = r"""
const assert = require("assert");
const stats = require("./web/stats.js");

assert.strictEqual(stats.droppedPercent(1, 100), 1);
assert.strictEqual(stats.droppedPercent(0, 0), null);
assert.strictEqual(stats.droppedPercent(-1, 10), null);

const samples = [
  { t: 0, dropped: 0, total: 0 },
  { t: 5000, dropped: 1, total: 100 },
  { t: 10000, dropped: 2, total: 200 },
  { t: 15000, dropped: 2, total: 300 },
  { t: 20000, dropped: 8, total: 400 },
];
assert.strictEqual(stats.windowDroppedPercent(samples, 10000, 10000), 1);
assert.strictEqual(stats.windowDroppedPercent(samples, 20000, 10000), 3);
assert.strictEqual(stats.windowDroppedPercent(samples, 5000, 10000), null);
assert.strictEqual(stats.windowDroppedPercent([
  { t: 0, dropped: 5, total: 50 },
  { t: 10000, dropped: 1, total: 40 },
], 10000, 10000), null);

const reset = stats.pushQualitySample([
  { t: 0, dropped: 9, total: 90 },
], { t: 1000, dropped: 0, total: 1 }, 1000, 10000);
assert.deepStrictEqual(reset, [{ t: 1000, dropped: 0, total: 1 }]);

const trimmed = stats.trimSamples(samples, 20000, 10000);
assert.deepStrictEqual(trimmed.map((sample) => sample.t), [5000, 10000, 15000, 20000]);

assert.strictEqual(stats.bitrateFromBytes({ t: 0, bytes: 1000 }, { t: 1000, bytes: 6000 }), 40000);
assert.strictEqual(stats.formatBitrate(2500000), "2.50 Mb/s");
assert.strictEqual(stats.formatJitter(0.012), "12.0 ms");

const report = stats.codecFromReport([
  { type: "inbound-rtp", kind: "video", codecId: "c1", jitter: 0.004, packetsLost: 2, framesDecoded: 90, framesDropped: 1, bytesReceived: 5000 },
  { type: "codec", id: "c1", mimeType: "video/H264", sdpFmtpLine: "profile-level-id=64001f" },
]);
assert.strictEqual(report.codec, "video/H264 profile 64001f");
assert.strictEqual(report.framesDropped, 1);

const text = stats.formatStats({
  channel: "12 News",
  timestamp: "2026-11-21T10:47:00.000Z",
  userAgent: "TestAgent",
  mode: "HLS",
  profile: "Smooth",
  resolution: "1280x720",
  codec: "avc1.64001f",
  dropped: 12,
  total: 4000,
  droppedPercent: 0.3,
  droppedPercent10s: 0.1,
  bufferAhead: 8.2,
  stalls: 1,
  bitrate: 2400000,
  hlsErrors: 0,
  jitter: null,
  packetsLost: null,
  framesDecoded: null,
  framesDropped: null,
  error: "getStats failed: timed out",
});
assert.ok(text.includes("Channel: 12 News"));
assert.ok(text.includes("Time: 2026-11-21T10:47:00.000Z"));
assert.ok(text.includes("User agent: TestAgent"));
assert.ok(text.includes("Mode: HLS, Smooth"));
assert.ok(text.includes("Dropped frames: 12 / 4000 (0.30%)"));
assert.ok(text.includes("Dropped last 10s: 0.10%"));
assert.ok(text.includes("Buffer ahead: 8.2 s"));
assert.ok(text.includes("Bitrate: 2.40 Mb/s"));
assert.ok(text.includes("Jitter: n/a"));
assert.ok(text.includes("Error: getStats failed: timed out"));
assert.ok(!/[\u{1F300}-\u{1FAFF}]/u.test(text));
"""
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
