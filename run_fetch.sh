#!/usr/bin/env bash
# Turnkey runner for fetching the YouTube transcript.
#
# Intended to be run inside a fresh Claude Code (web) environment whose
# Network access allowlist includes the YouTube-related domains. It:
#   1. Checks reachability of www.youtube.com (must be HTTP 200).
#      - On 403 "Host not in allowlist" it STOPS and prints what to verify,
#        and does NOT run pip/python (mirrors HANDOFF.md policy).
#   2. Installs dependencies (youtube-transcript-api, yt-dlp).
#   3. Runs fetch_transcript.py (api -> yt-dlp fallback is automatic).
#   4. Shows the resulting transcript file summary.
#
# Usage:  bash run_fetch.sh
set -u

VIDEO_ID="i65k0u0Mkac"
OUT="transcript_${VIDEO_ID}.txt"
URL="https://www.youtube.com"

echo "==> [1/4] Reachability check: ${URL}"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${URL}" 2>/dev/null || echo 000)"
echo "    HTTP ${code}"

if [ "${code}" != "200" ]; then
  body="$(curl -sS --max-time 15 "${URL}" 2>/dev/null | head -c 80 | tr '\n' ' ')"
  echo
  echo "!! NOT reachable (HTTP ${code}). Body: ${body}"
  echo "!! Stopping here. pip/python were NOT run."
  echo
  echo "Verify the environment's Network access allowlist contains ALL of:"
  echo "    youtube.com"
  echo "    *.youtube.com"
  echo "    *.googlevideo.com"
  echo "    *.googleapis.com"
  echo "    *.ytimg.com"
  echo "And confirm THIS session was launched in that same environment."
  echo "If it keeps failing, switch Network access to Full and relaunch a new session."
  exit 1
fi

echo
echo "==> [2/4] Installing dependencies"
pip install youtube-transcript-api yt-dlp || {
  echo "!! pip install failed."; exit 1;
}

echo
echo "==> [3/4] Running fetch_transcript.py"
python3 fetch_transcript.py
rc=$?

echo
echo "==> [4/4] Result"
if [ "${rc}" -eq 0 ] && [ -f "${OUT}" ]; then
  echo "    path  : $(pwd)/${OUT}"
  echo "    bytes : $(wc -c < "${OUT}")"
  echo "    chars : $(wc -m < "${OUT}")"
  echo "    lines : $(wc -l < "${OUT}")"
  echo
  echo "Done. Review ${OUT}, then commit & push to claude/youtube-transcript-fetch-3fhnok."
else
  echo "!! fetch_transcript.py did not produce ${OUT} (exit ${rc})."
  exit 1
fi
