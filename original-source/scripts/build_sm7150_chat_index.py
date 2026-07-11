#!/usr/bin/env python3
"""Build a combined search index from SM7150 Matrix and Telegram exports."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from bs4 import BeautifulSoup


KEYWORDS = [
    "sm7150",
    "sunfish",
    "pixel",
    "pixel 4a",
    "google-sunfish",
    "android",
    "kernel",
    "mainline",
    "downstream",
    "dts",
    "dtsi",
    "dtb",
    "dtbo",
    "deviceinfo",
    "bootimg",
    "boot image",
    "initramfs",
    "qcom",
    "qualcomm",
    "sdm730",
    "sdm732",
    "adreno",
    "gpu",
    "drm",
    "dpu",
    "mdss",
    "dsi",
    "panel",
    "display",
    "touch",
    "touchscreen",
    "fts",
    "goodix",
    "synaptics",
    "ufs",
    "phy",
    "interconnect",
    "clk",
    "clock",
    "regulator",
    "pm6150",
    "pm8150",
    "remoteproc",
    "rpmsg",
    "modem",
    "adsp",
    "cdsp",
    "slpi",
    "venus",
    "vcodec",
    "camss",
    "camera",
    "audio",
    "wlan",
    "wifi",
    "bluetooth",
    "firmware",
    "vendor",
    "u-boot",
    "lk2nd",
    "fastboot",
    "bootloader",
]


@dataclass
class Message:
    source: str
    timestamp: str
    sender: str
    message_id: str
    text: str


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def tsv_cell(value: str) -> str:
    return value.replace("\t", " ").replace("\r", " ").replace("\n", " ")


def parse_telegram_timestamp(value: str) -> str:
    parsed = dt.datetime.strptime(value, "%d.%m.%Y %H:%M:%S UTC%z")
    return parsed.astimezone(dt.UTC).isoformat()


def load_matrix(path: Path) -> Iterable[Message]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            item = json.loads(line)
            if item.get("type") != "m.room.message":
                continue
            text = item.get("replacement_body") or item.get("body") or ""
            text = clean_text(str(text))
            if not text:
                continue
            yield Message(
                source="matrix",
                timestamp=str(item.get("timestamp") or ""),
                sender=str(item.get("sender") or ""),
                message_id=str(item.get("event_id") or ""),
                text=text,
            )


def load_telegram(export_dir: Path) -> Iterable[Message]:
    last_sender = ""
    for html_path in sorted(export_dir.glob("messages*.html"), key=lambda path: path.name):
        soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")
        for node in soup.select("div.message"):
            message_id = node.get("id") or ""
            if "service" in node.get("class", []):
                continue
            date_node = node.select_one("div.date.details")
            if not date_node:
                continue
            title = date_node.get("title") or ""
            if not title:
                continue
            try:
                timestamp = parse_telegram_timestamp(title)
            except ValueError:
                timestamp = title

            from_node = node.select_one("div.from_name")
            if from_node:
                last_sender = clean_text(from_node.get_text(" ", strip=True))
            sender = last_sender

            text_node = node.select_one("div.text")
            if text_node:
                text = clean_text(text_node.get_text(" ", strip=True))
            else:
                media_node = node.select_one("div.media_wrap")
                text = clean_text(media_node.get_text(" ", strip=True)) if media_node else ""
            if not text:
                continue
            yield Message(
                source="telegram",
                timestamp=timestamp,
                sender=sender,
                message_id=message_id,
                text=text,
            )


def match_keywords(text: str) -> list[str]:
    lowered = text.lower()
    matches = []
    for keyword in KEYWORDS:
        if keyword in lowered:
            matches.append(keyword)
    return matches


def write_tsv(path: Path, rows: Iterable[list[str]], header: list[str]) -> int:
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("\t".join(header) + "\n")
        for row in rows:
            fh.write("\t".join(tsv_cell(cell) for cell in row) + "\n")
            count += 1
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-jsonl", required=True)
    parser.add_argument("--telegram-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix_path = Path(args.matrix_jsonl)
    telegram_dir = Path(args.telegram_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    messages = list(load_matrix(matrix_path))
    messages.extend(load_telegram(telegram_dir))
    messages.sort(key=lambda item: (item.timestamp, item.source, item.message_id))

    combined_path = out_dir / "combined-messages.tsv"
    combined_count = write_tsv(
        combined_path,
        ([msg.source, msg.timestamp, msg.sender, msg.message_id, msg.text] for msg in messages),
        ["source", "timestamp_utc", "sender", "id", "text"],
    )

    hit_rows: list[list[str]] = []
    for msg in messages:
        hits = match_keywords(msg.text)
        if not hits:
            continue
        snippet = msg.text[:280]
        hit_rows.append(
            [
                msg.source,
                msg.timestamp,
                msg.sender,
                msg.message_id,
                ",".join(hits),
                snippet,
            ]
        )

    hits_path = out_dir / "kernel-keyword-hits.tsv"
    hits_count = write_tsv(
        hits_path,
        hit_rows,
        ["source", "timestamp_utc", "sender", "id", "keywords", "snippet"],
    )

    summary = {
        "matrix_jsonl": str(matrix_path),
        "telegram_dir": str(telegram_dir),
        "combined_messages": combined_count,
        "kernel_keyword_hits": hits_count,
        "keywords": KEYWORDS,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
    }
    with (out_dir / "summary.json").open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(f"combined_messages={combined_count}")
    print(f"kernel_keyword_hits={hits_count}")
    print(f"out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
