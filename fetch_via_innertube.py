#!/usr/bin/env python3
"""Fetch YouTube transcript via the InnerTube API on youtubei.googleapis.com only.

This avoids www.youtube.com and *.googlevideo.com entirely, which are blocked in
this environment's allowlist, while *.googleapis.com is reachable.

Flow:
  1. POST /youtubei/v1/next   -> find getTranscriptEndpoint.params
  2. POST /youtubei/v1/get_transcript -> inline transcript segments
"""
import json
import sys
import urllib.request

VIDEO_ID = "i65k0u0Mkac"
API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"  # public InnerTube web key
BASE = "https://youtubei.googleapis.com/youtubei/v1"
HEADERS = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}


def post(endpoint, body):
    req = urllib.request.Request(
        f"{BASE}/{endpoint}?key={API_KEY}&prettyPrint=false",
        data=json.dumps(body).encode("utf-8"),
        headers=HEADERS,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def find_key(obj, key):
    """Recursively yield all values stored under `key`."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from find_key(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_key(v, key)


def collect_text(obj):
    """Recursively collect 'text' from runs/simpleText under transcript segments."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            # transcriptSegmentRenderer.snippet has runs[] or simpleText
            if "transcriptSegmentRenderer" in o:
                seg = o["transcriptSegmentRenderer"]
                snip = seg.get("snippet", {})
                if "runs" in snip:
                    out.append("".join(r.get("text", "") for r in snip["runs"]))
                elif "simpleText" in snip:
                    out.append(snip["simpleText"])
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return out


def main():
    for client in (
        {"clientName": "WEB", "clientVersion": "2.20240101.00.00", "hl": "ja"},
        {"clientName": "ANDROID", "clientVersion": "20.10.38", "hl": "ja"},
    ):
        ctx = {"client": client}
        try:
            nxt = post("next", {"context": ctx, "videoId": VIDEO_ID})
        except Exception as e:  # noqa: BLE001
            print(f"[next] client={client['clientName']} failed: {e!r}", file=sys.stderr)
            continue

        params = next(iter(find_key(nxt, "getTranscriptEndpoint")), None)
        if params:
            params = params.get("params")
        if not params:
            print(f"[next] client={client['clientName']}: no transcript params", file=sys.stderr)
            continue

        try:
            tr = post("get_transcript", {"context": ctx, "params": params})
        except Exception as e:  # noqa: BLE001
            print(f"[get_transcript] client={client['clientName']} failed: {e!r}", file=sys.stderr)
            continue

        segments = collect_text(tr)
        segments = [s for s in (x.strip() for x in segments) if s]
        if segments:
            text = "\n".join(segments)
            out = f"transcript_{VIDEO_ID}.txt"
            with open(out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print("===== RESULT =====")
            print(f"source        : InnerTube get_transcript (youtubei.googleapis.com)")
            print(f"client        : {client['clientName']}")
            print(f"output path   : {out}")
            print(f"segments      : {len(segments)}")
            print(f"char count    : {len(text)}")
            return 0
        print(f"[get_transcript] client={client['clientName']}: empty segments", file=sys.stderr)

    print("FAILED via InnerTube.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
