#!/usr/bin/env python3
"""Fetch the full transcript for a YouTube video using only Python libraries / CLI
(no MCP, no API keys).

Strategy:
  1. youtube-transcript-api
       - language fallback: ja -> ja-JP -> en
       - prefer manually created captions, fall back to auto-generated
  2. On failure (e.g. 403 / 429), fall back to yt-dlp:
       yt-dlp --skip-download --write-subs --write-auto-subs \
              --sub-langs "ja,ja-JP,en" --sub-format vtt <url>
     then convert the .vtt to plain text (strip timestamps, inline tags and the
     duplicate rolling lines that auto-generated captions emit).

Output: transcript_<video_id>.txt
Then prints: output path / language used / manual-or-auto / character count.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys
import time

VIDEO_ID = "i65k0u0Mkac"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
# API language fallback. yt-dlp additionally honours ja-orig (the original-language
# auto track YouTube exposes for non-English source videos).
LANG_PRIORITY = ["ja", "ja-JP", "en"]
YTDLP_LANG_PRIORITY = ["ja", "ja-orig", "ja-JP", "en"]
OUT = f"transcript_{VIDEO_ID}.txt"

# The cloud egress routes outbound HTTPS through a TLS-intercepting proxy whose
# self-signed CA lives in the system trust store but NOT in the certifi bundle
# that requests / yt-dlp ship with. Point cert-validating libraries at the
# system bundle so the youtube-transcript-api path can complete its TLS
# handshake (it otherwise hits CERTIFICATE_VERIFY_FAILED before even reaching
# YouTube). Harmless when no such proxy is present.
_SYSTEM_CA = "/etc/ssl/certs/ca-certificates.crt"
if os.path.exists(_SYSTEM_CA):
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _SYSTEM_CA)
    os.environ.setdefault("SSL_CERT_FILE", _SYSTEM_CA)


# --------------------------------------------------------------------------- #
# Method 1: youtube-transcript-api
# --------------------------------------------------------------------------- #
def try_transcript_api():
    """Return (text, lang, kind) or None if it fails."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("youtube-transcript-api not installed; skipping.", file=sys.stderr)
        return None

    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(VIDEO_ID)
    except Exception as e:  # noqa: BLE001
        print(f"[api] list() failed: {e!r}", file=sys.stderr)
        return None

    chosen = kind = None
    # Manual captions first, in language priority order.
    for lang in LANG_PRIORITY:
        try:
            chosen = transcript_list.find_manually_created_transcript([lang])
            kind = "manual"
            break
        except Exception:
            continue
    # Then auto-generated.
    if chosen is None:
        for lang in LANG_PRIORITY:
            try:
                chosen = transcript_list.find_generated_transcript([lang])
                kind = "auto-generated"
                break
            except Exception:
                continue

    if chosen is None:
        print("[api] no transcript in requested languages.", file=sys.stderr)
        return None

    try:
        data = chosen.fetch()
    except Exception as e:  # noqa: BLE001
        print(f"[api] fetch() failed: {e!r}", file=sys.stderr)
        return None

    lines = []
    for snip in data:
        txt = (snip.text if hasattr(snip, "text") else snip["text"]).strip()
        if txt:
            lines.append(txt)
    return "\n".join(lines), chosen.language_code, kind


# --------------------------------------------------------------------------- #
# Method 2: yt-dlp + VTT parsing
# --------------------------------------------------------------------------- #
def run_yt_dlp():
    """Download subtitles via yt-dlp. Returns the path to a .vtt file, or None.

    Hardened for the cloud egress, where the bare command fails two ways:
      * TLS CERTIFICATE_VERIFY_FAILED, because yt-dlp validates against its
        bundled certifi and never sees the proxy's CA -> --no-check-certificates
        (the system trust store already lets curl reach YouTube at HTTP 200).
      * HTTP 429 on YouTube's timedtext endpoint, because the shared egress IP
        is throttled, and the default player client also gets IP-blocked
        -> cycle player clients (ios/tv first) and retry with backoff while
        spacing out requests.
    """
    out_tmpl = f"sub_{VIDEO_ID}.%(ext)s"
    cmd = [
        "yt-dlp", "--skip-download",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", ",".join(YTDLP_LANG_PRIORITY),
        "--sub-format", "vtt",
        "--no-check-certificates",
        "--sleep-requests", "3",
        "--retries", "10",
        "--fragment-retries", "10",
        "--extractor-args", "youtube:player_client=ios,tv,web,android",
        "-o", out_tmpl, URL,
    ]

    # Retry the whole invocation with exponential backoff to ride out the
    # timedtext 429s. Stop as soon as a .vtt actually lands on disk.
    candidates: list[str] = []
    backoff = [0, 5, 15, 30]
    for attempt, delay in enumerate(backoff, 1):
        if delay:
            print(f"[yt-dlp] retry {attempt}/{len(backoff)} after {delay}s ...",
                  file=sys.stderr)
            time.sleep(delay)
        print(f"[yt-dlp] {' '.join(cmd)}", file=sys.stderr)
        try:
            subprocess.run(cmd, check=False)
        except FileNotFoundError:
            print("[yt-dlp] not installed.", file=sys.stderr)
            return None
        candidates = glob.glob(f"sub_{VIDEO_ID}*.vtt")
        if candidates:
            break

    if not candidates:
        return None

    def score(path: str):
        name = os.path.basename(path)
        # language rank
        lang_rank = len(YTDLP_LANG_PRIORITY)
        for i, lg in enumerate(YTDLP_LANG_PRIORITY):
            if f".{lg}." in name:
                lang_rank = i
                break
        return lang_rank
    candidates.sort(key=score)
    return candidates[0]


def vtt_lang(path: str) -> str:
    name = os.path.basename(path)
    for lg in YTDLP_LANG_PRIORITY:
        if f".{lg}." in name:
            return lg
    m = re.search(r"\.([a-zA-Z-]+)\.vtt$", name)
    return m.group(1) if m else "unknown"


_TS_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s-->\s")
_TAG_RE = re.compile(r"<[^>]+>")            # inline <00:00:00.000> / <c> tags
_CUE_SETTINGS_RE = re.compile(r"\balign:[^\s]+|\bposition:[^\s]+")


def parse_vtt(path: str) -> str:
    """Convert a WebVTT file to deduplicated plain text."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    out_lines = []
    seen_recent = []  # rolling window to drop auto-caption duplicate lines
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE", "STYLE")):
            continue
        if _TS_RE.search(line):  # timestamp / cue-timing line
            continue
        if line.isdigit():       # numeric cue index
            continue
        # strip inline tags and cue settings
        line = _TAG_RE.sub("", line)
        line = _CUE_SETTINGS_RE.sub("", line).strip()
        if not line:
            continue
        # auto-generated captions repeat the previous cue's last line; dedupe
        if line in seen_recent:
            continue
        out_lines.append(line)
        seen_recent.append(line)
        if len(seen_recent) > 4:
            seen_recent.pop(0)

    return "\n".join(out_lines)


# --------------------------------------------------------------------------- #
def main():
    result = try_transcript_api()
    source = "youtube-transcript-api"

    if result is None:
        print("[*] Falling back to yt-dlp ...", file=sys.stderr)
        vtt = run_yt_dlp()
        if not vtt:
            print("FAILED: could not obtain transcript via either method.", file=sys.stderr)
            sys.exit(1)
        text = parse_vtt(vtt)
        lang = vtt_lang(vtt)
        # --write-subs only emits a file when a manual track exists; an
        # *-orig track or an auto Kind header means auto-generated.
        if lang.endswith("-orig") or ".auto." in vtt or _is_auto(vtt):
            kind = "auto-generated"
        else:
            kind = "manual"
        source = "yt-dlp"
    else:
        text, lang, kind = result

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    print("===== RESULT =====")
    print(f"source        : {source}")
    print(f"output path   : {os.path.abspath(OUT)}")
    print(f"language used : {lang}")
    print(f"caption kind  : {kind}")
    print(f"char count    : {len(text)}")


def _is_auto(path: str) -> bool:
    """yt-dlp marks auto captions; heuristically detect from the file's Kind header."""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(500)
        return "Kind: captions" in head and "auto" in head.lower()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    main()
