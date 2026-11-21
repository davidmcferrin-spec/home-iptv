(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.playbackStats = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function numberOrNull(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }

  function droppedPercent(dropped, total) {
    if (dropped == null || total == null || total <= 0 || dropped < 0) return null;
    return (dropped / total) * 100;
  }

  function windowDroppedPercent(samples, now, windowMs) {
    if (!samples || !samples.length) return null;
    const ordered = samples.slice().sort((a, b) => a.t - b.t);
    let end = null;
    let start = null;
    const cutoff = now - windowMs;
    for (const sample of ordered) {
      if (sample.t > now) break;
      end = sample;
      if (sample.t <= cutoff) start = sample;
    }
    if (!start || !end || end === start) return null;
    const dropped = end.dropped - start.dropped;
    const total = end.total - start.total;
    if (dropped < 0 || total <= 0) return null;
    return (dropped / total) * 100;
  }

  function pushQualitySample(samples, sample, now, keepMs) {
    const last = samples.length ? samples[samples.length - 1] : null;
    const base = last && (sample.dropped < last.dropped || sample.total < last.total) ? [] : samples;
    return trimSamples(base.concat([sample]), now, keepMs);
  }

  function trimSamples(samples, now, keepMs) {
    const cutoff = now - keepMs;
    let baseline = null;
    const kept = [];
    for (const sample of samples) {
      if (sample.t < cutoff) baseline = sample;
      else if (sample.t <= now) kept.push(sample);
    }
    if (baseline) kept.unshift(baseline);
    return kept;
  }

  function bitrateFromBytes(prev, next) {
    if (!prev || !next) return null;
    const seconds = (next.t - prev.t) / 1000;
    if (seconds <= 0) return null;
    const bytes = next.bytes - prev.bytes;
    if (bytes < 0) return null;
    return (bytes * 8) / seconds;
  }

  function formatPercent(value) {
    if (value == null || Number.isNaN(value)) return "n/a";
    return `${value.toFixed(2)}%`;
  }

  function formatCount(value) {
    if (value == null || Number.isNaN(value)) return "n/a";
    return String(Math.round(value));
  }

  function formatSeconds(value) {
    if (value == null || Number.isNaN(value)) return "n/a";
    return `${value.toFixed(1)} s`;
  }

  function formatBitrate(bps) {
    if (bps == null || Number.isNaN(bps) || bps < 0) return "n/a";
    if (bps >= 1000000) return `${(bps / 1000000).toFixed(2)} Mb/s`;
    if (bps >= 1000) return `${(bps / 1000).toFixed(0)} kb/s`;
    return `${Math.round(bps)} b/s`;
  }

  function formatJitter(seconds) {
    if (seconds == null || Number.isNaN(seconds)) return "n/a";
    return `${(seconds * 1000).toFixed(1)} ms`;
  }

  function readPlaybackQuality(quality) {
    if (!quality) return { dropped: null, total: null };
    const dropped = numberOrNull(
      quality.droppedVideoFrames != null ? quality.droppedVideoFrames : quality.webkitDroppedFrameCount
    );
    const total = numberOrNull(
      quality.totalVideoFrames != null ? quality.totalVideoFrames : quality.webkitDecodedFrameCount
    );
    return { dropped, total };
  }

  function profileFromFmtp(line) {
    if (!line) return "";
    const match = /profile-level-id=([0-9a-fA-F]+)/.exec(line);
    return match ? `profile ${match[1]}` : "";
  }

  function codecFromReport(entries) {
    const empty = {
      codec: null,
      jitter: null,
      packetsLost: null,
      framesDecoded: null,
      framesDropped: null,
      bytesReceived: null,
    };
    if (!entries) return empty;
    const inbound = entries.find((entry) => {
      return entry.type === "inbound-rtp" && (entry.kind === "video" || entry.mediaType === "video");
    });
    if (!inbound) return empty;
    const codec = entries.find((entry) => entry.type === "codec" && entry.id === inbound.codecId);
    let name = codec ? (codec.mimeType || codec.codec || "") : "";
    const profile = codec ? profileFromFmtp(codec.sdpFmtpLine) : "";
    if (name && profile) name = `${name} ${profile}`;
    return {
      codec: name || null,
      jitter: numberOrNull(inbound.jitter),
      packetsLost: numberOrNull(inbound.packetsLost),
      framesDecoded: numberOrNull(inbound.framesDecoded),
      framesDropped: numberOrNull(inbound.framesDropped),
      bytesReceived: numberOrNull(inbound.bytesReceived),
    };
  }

  function codecFromHlsLevel(level) {
    if (!level) return null;
    const bits = [];
    if (level.videoCodec) bits.push(level.videoCodec);
    if (level.audioCodec) bits.push(level.audioCodec);
    if (!bits.length && level.attrs && level.attrs.CODECS) bits.push(level.attrs.CODECS);
    return bits.join(", ") || null;
  }

  function statRows(fields) {
    const dropped = formatCount(fields.dropped);
    const total = formatCount(fields.total);
    const frames = `${formatCount(fields.framesDecoded)} decoded, ${formatCount(fields.framesDropped)} dropped`;
    return [
      ["Mode", `${fields.mode || "n/a"}, ${fields.profile || "n/a"}`],
      ["Resolution", fields.resolution || "n/a"],
      ["Codec", fields.codec || "n/a"],
      ["Dropped frames", `${dropped} / ${total} (${formatPercent(fields.droppedPercent)})`],
      ["Dropped last 10s", formatPercent(fields.droppedPercent10s)],
      ["Buffer ahead", formatSeconds(fields.bufferAhead)],
      ["Stalls", formatCount(fields.stalls)],
      ["Bitrate", formatBitrate(fields.bitrate)],
      ["HLS errors", formatCount(fields.hlsErrors)],
      ["Jitter", formatJitter(fields.jitter)],
      ["Packets lost", formatCount(fields.packetsLost)],
      ["WebRTC frames", frames],
    ];
  }

  function formatStats(fields) {
    const lines = [
      `Channel: ${fields.channel || "n/a"}`,
      `Time: ${fields.timestamp || "n/a"}`,
      `User agent: ${fields.userAgent || "n/a"}`,
    ];
    statRows(fields).forEach((row) => lines.push(`${row[0]}: ${row[1]}`));
    if (fields.error) lines.push(`Error: ${fields.error}`);
    return lines.join("\n");
  }

  return {
    droppedPercent,
    windowDroppedPercent,
    trimSamples,
    pushQualitySample,
    bitrateFromBytes,
    formatPercent,
    formatBitrate,
    formatJitter,
    readPlaybackQuality,
    codecFromReport,
    codecFromHlsLevel,
    statRows,
    formatStats,
  };
});
