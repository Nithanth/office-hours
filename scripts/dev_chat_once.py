#!/usr/bin/env python3
"""Minimal CLI helper to send one chat message to the local Lux Scholar API
and print the assistant response to stdout.

Example usage:

    python scripts/dev_chat_once.py \
        --paper https://arxiv.org/abs/2003.08934 \
        --message "From Table 6, which ablation had the worst PSNR?" \
        --audience non_technical

The script mimics the browser workflow:
1. POST /chat with multipart-form fields (`paper_id`, `message`, …).
2. Parse the returned HTML to extract the `sse-connect` stream URL.
3. Open the Server-Sent Events stream and print each `data:` line.

It depends only on `httpx` (already a transitive dependency of the project).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx
import html
import re

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_stream_url(html: str) -> str | None:
    """Return the relative stream URL from the /chat HTML stub."""

    m = re.search(r'sse-connect="([^"]+)"', html)
    return m.group(1) if m else None


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Send one chat message and stream reply")
    p.add_argument("--paper", required=True, help="Paper ID or URL")
    p.add_argument("--message", required=True, help="User message to send")
    p.add_argument("--audience", default="technical", help="audience_type field")
    p.add_argument("--model", help="model_key field (optional)")
    p.add_argument(
        "--server",
        default="http://localhost:8000",
        help="Base Lux Scholar server URL (default: %(default)s)",
    )
    args = p.parse_args(argv)

    # Ensure base URL ends without trailing slash for consistent joins.
    base_url = args.server.rstrip("/")

    form_data: dict[str, tuple[str, str]] = {
        "paper_id": (None, args.paper),
        "message": (None, args.message),
        "audience_type": (None, args.audience),
    }
    if args.model:
        form_data["model_key"] = (None, args.model)

    with httpx.Client(base_url=base_url, timeout=120.0) as client:
        # Step 1. Kick off chat request (returns tiny HTML stub)
        resp = client.post("/chat", files=form_data)
        resp.raise_for_status()
        stream_path = _parse_stream_url(resp.text)
        if not stream_path:
            print("[error] cannot find sse-connect URL in /chat response", file=sys.stderr)
            Path("/tmp/chat_stub.html").write_text(resp.text, encoding="utf-8")
            print("HTML stub saved to /tmp/chat_stub.html for inspection", file=sys.stderr)
            sys.exit(1)

        # Normalize to absolute URL
        stream_url = stream_path if stream_path.startswith("http") else base_url + stream_path

        # Step 2. Open SSE stream and relay assistant messages to stdout.
        headers = {"Accept": "text/event-stream"}
        with client.stream("GET", stream_url, headers=headers) as s:
            for raw in s.iter_lines():
                if not raw:
                    continue
                # httpx returns str lines (already decoded)
                if raw.startswith(":"):
                    # keep-alive comment
                    continue
                if not raw.startswith("data:"):
                    continue
                payload = raw[5:].lstrip()
                if not payload:
                    continue

                # Convert minimal HTML to plain text for terminal readability
                txt = html.unescape(payload)

                # Lists: convert <li> to bullet lines
                txt = re.sub(r"<li[^>]*>", "\n* ", txt, flags=re.IGNORECASE)
                txt = re.sub(r"</li>", "", txt, flags=re.IGNORECASE)

                # Line breaks inside prose – use space, not \n, to avoid broken words
                txt = re.sub(r"<br\s*/?>", " ", txt, flags=re.IGNORECASE)

                # Remove block tags that add their own newline semantics
                txt = re.sub(r"</?p[^>]*>", "\n", txt, flags=re.IGNORECASE)
                txt = re.sub(r"</?div[^>]*>", "\n", txt, flags=re.IGNORECASE)
                txt = re.sub(r"</?ul[^>]*>", "\n", txt, flags=re.IGNORECASE)

                # Strip any other residual HTML tags
                txt = re.sub(r"<[^>]+>", "", txt)

                # Ensure a single space after bullet marker
                txt = re.sub(r"\*\s*", "* ", txt)

                # Collapse repeated whitespace
                txt = re.sub(r"[ \t]+", " ", txt)
                txt = re.sub(r"\n{3,}", "\n\n", txt)

                print(txt.strip("\n"), end="\n", flush=True)

    print()  # final newline after stream ends


if __name__ == "__main__":
    main()
