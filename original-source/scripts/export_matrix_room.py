#!/usr/bin/env python3
"""Export a Matrix room history through the Client-Server API.

The access token is read from MATRIX_ACCESS_TOKEN. It is never written to the
export directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_SERVER = "https://matrix-client.matrix.org"
DEFAULT_ROOM_ALIAS = "#sm7150-mainline:matrix.org"


def utc_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).isoformat()


def json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class MatrixClient:
    def __init__(self, server: str, token: str) -> None:
        self.server = server.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
        url = self.server + path + query
        req = urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "codex-matrix-room-export/1.0",
            },
        )

        while True:
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                if not body:
                    return None
                return json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                retry_after = exc.headers.get("Retry-After")
                if exc.code == 429:
                    wait_seconds = 3
                    try:
                        parsed = json.loads(detail)
                        wait_ms = parsed.get("retry_after_ms")
                        if isinstance(wait_ms, int) and wait_ms > 0:
                            wait_seconds = max(1, wait_ms / 1000)
                    except json.JSONDecodeError:
                        pass
                    if retry_after:
                        try:
                            wait_seconds = max(wait_seconds, float(retry_after))
                        except ValueError:
                            pass
                    print(f"rate limited, sleeping {wait_seconds:.1f}s", file=sys.stderr)
                    time.sleep(wait_seconds)
                    continue
                raise RuntimeError(f"HTTP {exc.code} for {url}: {detail}") from exc

    def whoami(self) -> Any:
        return self.request("GET", "/_matrix/client/v3/account/whoami")

    def resolve_alias(self, alias: str) -> Any:
        encoded = urllib.parse.quote(alias, safe="")
        return self.request("GET", f"/_matrix/client/v3/directory/room/{encoded}")

    def room_messages(
        self,
        room_id: str,
        *,
        from_token: str | None,
        limit: int,
    ) -> Any:
        encoded = urllib.parse.quote(room_id, safe="")
        return self.request(
            "GET",
            f"/_matrix/client/v3/rooms/{encoded}/messages",
            {
                "from": from_token,
                "dir": "b",
                "limit": limit,
            },
        )


def normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    content = event.get("content") if isinstance(event.get("content"), dict) else {}
    unsigned = event.get("unsigned") if isinstance(event.get("unsigned"), dict) else {}
    relates_to = content.get("m.relates_to") if isinstance(content.get("m.relates_to"), dict) else None
    replacement = None
    if relates_to and relates_to.get("rel_type") == "m.replace":
        new_content = content.get("m.new_content")
        if isinstance(new_content, dict):
            replacement = new_content.get("body")

    return {
        "event_id": event.get("event_id"),
        "origin_server_ts": event.get("origin_server_ts"),
        "timestamp": utc_iso(event.get("origin_server_ts")),
        "sender": event.get("sender"),
        "type": event.get("type"),
        "state_key": event.get("state_key"),
        "msgtype": content.get("msgtype"),
        "body": content.get("body"),
        "formatted_body": content.get("formatted_body"),
        "replacement_body": replacement,
        "relates_to": relates_to,
        "redacts": event.get("redacts") or content.get("redacts"),
        "transaction_id": unsigned.get("transaction_id"),
        "content": content,
    }


def markdown_line(item: dict[str, Any]) -> str | None:
    event_type = item.get("type")
    body = item.get("replacement_body") or item.get("body")

    if event_type == "m.room.message" and body:
        timestamp = item.get("timestamp") or ""
        sender = item.get("sender") or ""
        msgtype = item.get("msgtype") or ""
        body_text = str(body).replace("\r\n", "\n").replace("\r", "\n").strip()
        body_text = body_text.replace("\n", "\n  ")
        return f"- `{timestamp}` **{html.escape(sender)}** `{html.escape(msgtype)}`: {html.escape(body_text)}"

    if event_type == "m.reaction":
        timestamp = item.get("timestamp") or ""
        sender = item.get("sender") or ""
        relates_to = item.get("relates_to") or {}
        key = relates_to.get("key", "")
        event_id = relates_to.get("event_id", "")
        return f"- `{timestamp}` **{html.escape(sender)}** reaction `{html.escape(str(key))}` -> `{html.escape(str(event_id))}`"

    return None


def write_outputs(out_dir: Path, events: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "events.raw.jsonl"
    normalized_path = out_dir / "events.normalized.jsonl"
    messages_path = out_dir / "messages.md"
    metadata_path = out_dir / "export-metadata.json"
    media_path = out_dir / "media-manifest.tsv"

    normalized = [normalize_event(event) for event in events]
    normalized.sort(key=lambda item: (item.get("origin_server_ts") or 0, item.get("event_id") or ""))

    with raw_path.open("w", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json_dumps(event) + "\n")

    with normalized_path.open("w", encoding="utf-8", newline="\n") as fh:
        for item in normalized:
            fh.write(json_dumps(item) + "\n")

    message_lines = [line for item in normalized if (line := markdown_line(item))]
    with messages_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# Matrix export: {metadata['room_alias']}\n\n")
        fh.write(f"- room_id: `{metadata['room_id']}`\n")
        fh.write(f"- exported_at: `{metadata['exported_at']}`\n")
        fh.write(f"- raw_events: `{len(events)}`\n")
        fh.write(f"- rendered_lines: `{len(message_lines)}`\n\n")
        fh.write("## Messages\n\n")
        for line in message_lines:
            fh.write(line + "\n")

    media_rows: list[tuple[str, str, str, str]] = []
    for item in normalized:
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        url = content.get("url")
        if isinstance(url, str) and url.startswith("mxc://"):
            media_rows.append(
                (
                    str(item.get("timestamp") or ""),
                    str(item.get("sender") or ""),
                    str(content.get("body") or ""),
                    url,
                )
            )
    with media_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("timestamp\tsender\tname\tmxc_url\n")
        for row in media_rows:
            fh.write("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row) + "\n")

    metadata = dict(metadata)
    metadata["event_count"] = len(events)
    metadata["rendered_message_count"] = len(message_lines)
    metadata["media_count"] = len(media_rows)
    with metadata_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--room-alias", default=DEFAULT_ROOM_ALIAS)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--sleep", type=float, default=0.15)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("MATRIX_ACCESS_TOKEN")
    if not token:
        print("MATRIX_ACCESS_TOKEN is required", file=sys.stderr)
        return 2

    client = MatrixClient(args.server, token)
    whoami = client.whoami()
    alias_info = client.resolve_alias(args.room_alias)
    room_id = alias_info["room_id"]

    out_dir = Path(args.out_dir)
    seen: set[str] = set()
    events: list[dict[str, Any]] = []
    from_token: str | None = None
    last_end: str | None = None
    page_count = 0

    while True:
        if args.max_pages is not None and page_count >= args.max_pages:
            break

        page = client.room_messages(room_id, from_token=from_token, limit=args.limit)
        page_count += 1
        chunk = page.get("chunk") or []
        added = 0
        for event in chunk:
            event_id = event.get("event_id")
            if isinstance(event_id, str):
                if event_id in seen:
                    continue
                seen.add(event_id)
            events.append(event)
            added += 1

        end = page.get("end")
        print(
            f"page={page_count} chunk={len(chunk)} added={added} total={len(events)} end={end}",
            flush=True,
        )

        if not end or end == from_token or end == last_end or not chunk:
            last_end = end
            break
        last_end = end
        from_token = end
        if args.sleep > 0:
            time.sleep(args.sleep)

    exported_at = dt.datetime.now(dt.UTC).isoformat()
    metadata = {
        "server": args.server,
        "room_alias": args.room_alias,
        "room_id": room_id,
        "exported_at": exported_at,
        "page_count": page_count,
        "last_end": last_end,
        "whoami": {
            "user_id": whoami.get("user_id"),
            "device_id": whoami.get("device_id"),
            "is_guest": whoami.get("is_guest"),
        },
    }
    write_outputs(out_dir, events, metadata)
    print(f"exported {len(events)} events to {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
