#!/usr/bin/env python3
"""Create printable MiniDisc label pairs at selected A4 grid positions.

Input Markdown:
    # Album title
    1. First track
    2. Second track

Input CSV:
    disc_title,track_number,track_title
    Album title,1,First track
    ,2,Second track

There are two workflows: manual Markdown or CSV title and tracklist input, or
a MusicBrainz search that supplies the album title, release media, and cover art.
The script writes an artwork PDF and a matching SVG cut file. Multi-disc
releases use consecutive positions. If --position is not supplied, macOS
displays a position chooser numbered left-to-right and then top-to-bottom.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path


PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
FACE_WIDTH_MM = 36
FACE_HEIGHT_MM = 53
EDGE_WIDTH_MM = 59
EDGE_HEIGHT_MM = 4

# Origins of the 59 x 59 mm label-pair cells from minidisc-label-pairs-a4.svg.
GRID_X = (11.5, 75.5, 139.5)
GRID_Y = (18.5, 85.5, 152.5, 219.5)
MUSICBRAINZ_API = "https://musicbrainz.org/ws/2"
COVER_ART_API = "https://coverartarchive.org"
USER_AGENT = os.environ.get(
    "MUSICBRAINZ_USER_AGENT",
    "MiniDiscLabelMaker/1.1 (personal macOS Shortcut)",
)
_last_musicbrainz_request = 0.0
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a printable MiniDisc label PDF and matching cut SVG."
    )
    parser.add_argument(
        "--mode",
        choices=("manual", "musicbrainz"),
        help="Choose manual Markdown input or automatic MusicBrainz lookup.",
    )
    parser.add_argument(
        "--position",
        type=int,
        choices=range(1, 13),
        help="Grid position 1-12, numbered left-to-right and top-to-bottom.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Input file: Markdown or CSV in manual mode, or search text in MusicBrainz mode.",
    )
    parser.add_argument(
        "--input-format",
        choices=("auto", "markdown", "csv"),
        default="auto",
        help="Manual input format (default: detect from filename or content).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Output directory (default: the project's root output directory).",
    )
    parser.add_argument(
        "--search",
        help='MusicBrainz search text, such as "Radiohead - OK Computer".',
    )
    parser.add_argument(
        "--artist",
        help="Artist name used to narrow the MusicBrainz search.",
    )
    parser.add_argument(
        "--release-group",
        metavar="MBID",
        help="Use a specific MusicBrainz release-group MBID and skip album matching.",
    )
    parser.add_argument(
        "--release",
        metavar="MBID",
        help="Use an exact MusicBrainz release MBID and skip both match choosers.",
    )
    parser.add_argument(
        "--no-online-art",
        action="store_true",
        help="Legacy shortcut for manual mode when --mode is omitted.",
    )
    return parser.parse_args()


def read_optional_input(input_path: Path | None) -> str:
    if input_path:
        return input_path.expanduser().read_text(encoding="utf-8-sig").strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def choose_mode() -> str:
    labels = [
        "Manual - enter disc title and tracklist",
        "MusicBrainz - search and match an album",
    ]
    if sys.platform == "darwin" and shutil.which("osascript"):
        choices = ", ".join(f'"{label}"' for label in labels)
        apple_script = (
            f"set choices to {{{choices}}}\n"
            'set picked to choose from list choices with prompt "Choose a label workflow:" '
            f'default items {{"{labels[0]}"}}\n'
            "if picked is false then error number -128\n"
            "return item 1 of picked"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise SystemExit("No label workflow selected.")
        return "manual" if result.stdout.strip() == labels[0] else "musicbrainz"

    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write("1. Manual title and tracklist\n2. Search MusicBrainz\nChoose workflow: ")
        terminal.flush()
        value = terminal.readline().strip()
    if value not in {"1", "2"}:
        raise SystemExit("Workflow must be 1 or 2.")
    return "manual" if value == "1" else "musicbrainz"


def prompt_search_text() -> str:
    prompt = "Enter an album title, or Artist - Album title:"
    if sys.platform == "darwin" and shutil.which("osascript"):
        apple_script = (
            f'display dialog "{prompt}" default answer "" '
            'with title "MiniDisc MusicBrainz Search"\n'
            "return text returned of result"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise SystemExit("No MusicBrainz search entered.")
        return result.stdout.strip()

    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write(prompt + " ")
        terminal.flush()
        value = terminal.readline().strip()
    if not value:
        raise SystemExit("No MusicBrainz search entered.")
    return value


def clean_inline_markdown(value: str) -> str:
    value = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"(`+|\*\*|__|~~)", "", value)
    value = re.sub(r"(?<!\*)\*(?!\*)|(?<!_)_(?!_)", "", value)
    return html.unescape(value).strip()


def normalise_label_text(value: str) -> str:
    return value.translate(str.maketrans({"–": "-", "—": "-", "‑": "-", "•": "-"}))


def parse_markdown(markdown: str) -> tuple[str, str | None, list[tuple[str, str]]]:
    title = ""
    artist: str | None = None
    tracks: list[tuple[str, str]] = []
    current_index: int | None = None

    for raw_line in markdown.splitlines():
        if not title:
            heading = re.match(r"^\s*#(?!#)\s+(.+?)\s*$", raw_line)
            if heading:
                title = clean_inline_markdown(heading.group(1))
                continue

        artist_line = re.match(r"^\s*(?:artist|by)\s*:\s*(.+?)\s*$", raw_line, re.I)
        if artist_line and artist is None:
            artist = clean_inline_markdown(artist_line.group(1))
            continue

        item = re.match(r"^\s*(?:(\d+)[.)]|([-+*]))\s+(.+?)\s*$", raw_line)
        if item:
            marker = f"{item.group(1)}." if item.group(1) else "•"
            tracks.append((marker, clean_inline_markdown(item.group(3))))
            current_index = len(tracks) - 1
            continue

        # Indented lines immediately following an item are treated as a
        # continuation of that track name.
        if current_index is not None and raw_line.startswith((" ", "\t")):
            continuation = clean_inline_markdown(raw_line)
            if continuation:
                marker, text = tracks[current_index]
                tracks[current_index] = (marker, f"{text} {continuation}")
        elif raw_line.strip():
            current_index = None

    if not title:
        raise SystemExit("The Markdown must contain an H1 album title, for example: # Album")
    if not tracks:
        raise SystemExit("The Markdown must contain at least one ordered or unordered list item.")
    return title, artist, tracks


def csv_dialect(csv_text: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(csv_text[:4096], delimiters=",;\t")
    except csv.Error:
        return csv.excel


def normalise_csv_header(value: str) -> str:
    value = value.lstrip("\ufeff").strip().casefold()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def csv_columns(fieldnames: list[str] | None) -> tuple[str, str, str | None, str | None]:
    if not fieldnames:
        raise ValueError("The CSV must have a header row.")
    fields = {normalise_csv_header(name): name for name in fieldnames if name}

    def first(names: tuple[str, ...]) -> str | None:
        return next((fields[name] for name in names if name in fields), None)

    disc_column = first(("disc_title", "album_title", "album", "disc"))
    track_column = first(("track_title", "track", "song_title", "song", "name"))
    title_column = fields.get("title")
    if disc_column and not track_column and title_column:
        track_column = title_column
    elif track_column and not disc_column and title_column:
        disc_column = title_column

    if not disc_column:
        raise ValueError(
            "The CSV needs a disc_title column (album_title, album, or disc also work)."
        )
    if not track_column:
        raise ValueError(
            "The CSV needs a track_title column (track, song, title, or name also work)."
        )
    number_column = first(("track_number", "track_no", "number", "no", "position"))
    artist_column = first(("artist", "album_artist", "disc_artist"))
    return disc_column, track_column, number_column, artist_column


def parse_csv_tracklist(csv_text: str) -> tuple[str, str | None, list[tuple[str, str]]]:
    reader = csv.DictReader(
        io.StringIO(csv_text.lstrip("\ufeff")),
        dialect=csv_dialect(csv_text),
        skipinitialspace=True,
    )
    try:
        disc_column, track_column, number_column, artist_column = csv_columns(
            reader.fieldnames
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error

    disc_title = ""
    artist: str | None = None
    tracks: list[tuple[str, str]] = []
    for row_number, row in enumerate(reader, start=2):
        row_title = clean_inline_markdown(str(row.get(disc_column) or ""))
        if row_title:
            if disc_title and comparable_album_title(row_title) != comparable_album_title(
                disc_title
            ):
                raise SystemExit(
                    f"CSV row {row_number} changes the disc title from "
                    f'"{disc_title}" to "{row_title}".'
                )
            disc_title = row_title

        if artist_column and not artist:
            row_artist = clean_inline_markdown(str(row.get(artist_column) or ""))
            artist = row_artist or None

        track_title = clean_inline_markdown(str(row.get(track_column) or ""))
        if not track_title:
            continue
        raw_number = str(row.get(number_column) or "").strip() if number_column else ""
        if re.fullmatch(r"\d+\.0+", raw_number):
            raw_number = raw_number.split(".", 1)[0]
        if raw_number:
            marker = raw_number if raw_number.endswith((".", ")")) else f"{raw_number}."
        else:
            marker = f"{len(tracks) + 1}."
        tracks.append((marker, track_title))

    if not disc_title:
        raise SystemExit("The CSV must provide a disc title in at least one row.")
    if not tracks:
        raise SystemExit("The CSV must contain at least one track title.")
    return disc_title, artist, tracks


def manual_input_format(
    input_text: str, input_path: Path | None, requested_format: str
) -> str:
    if requested_format != "auto":
        return requested_format
    if input_path and input_path.suffix.casefold() == ".csv":
        return "csv"
    try:
        reader = csv.reader(io.StringIO(input_text), dialect=csv_dialect(input_text))
        fieldnames = next(reader, None)
        csv_columns(fieldnames)
        return "csv"
    except (ValueError, csv.Error):
        return "markdown"


def parse_manual_input(
    input_text: str, input_path: Path | None, requested_format: str
) -> tuple[str, str | None, list[tuple[str, str]]]:
    input_format = manual_input_format(input_text, input_path, requested_format)
    if input_format == "csv":
        return parse_csv_tracklist(input_text)
    return parse_markdown(input_text)


def choose_position(label_count: int = 1) -> int:
    maximum_start = 13 - label_count
    choices = []
    for position in range(1, maximum_start + 1):
        row = (position - 1) // 3 + 1
        column = (position - 1) % 3 + 1
        choices.append(f'"{position} - row {row}, column {column}"')

    if sys.platform == "darwin" and shutil.which("osascript"):
        apple_script = (
            f"set choices to {{{', '.join(choices)}}}\n"
            'set picked to choose from list choices with prompt '
            f'"Choose the first of {label_count} consecutive A4 label position(s):" '
            'default items {"1 - row 1, column 1"}\n'
            "if picked is false then error number -128\n"
            "return item 1 of picked"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise SystemExit("No grid position selected.")
        return int(result.stdout.strip().split()[0])

    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write(f"Choose first grid position 1-{maximum_start}: ")
        terminal.flush()
        value = terminal.readline().strip()
    if not value.isdigit() or not 1 <= int(value) <= maximum_start:
        raise SystemExit(f"Grid position must be a number from 1 to {maximum_start}.")
    return int(value)


def request_json(url: str, *, musicbrainz: bool = False) -> dict:
    global _last_musicbrainz_request
    if musicbrainz:
        elapsed = time.monotonic() - _last_musicbrainz_request
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.load(response)
    if musicbrainz:
        _last_musicbrainz_request = time.monotonic()
    return data


def lucene_phrase(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def search_release_groups(album_title: str, artist: str | None) -> list[dict]:
    query = f'releasegroup:"{lucene_phrase(album_title)}"'
    if artist:
        query += f' AND artist:"{lucene_phrase(artist)}"'
    url = f"{MUSICBRAINZ_API}/release-group/?" + urllib.parse.urlencode(
        {"query": query, "fmt": "json", "limit": 10}
    )
    data = request_json(url, musicbrainz=True)
    candidates = []
    for result in data.get("release-groups", []):
        credits = result.get("artist-credit", [])
        credited_artist = "".join(
            str(credit.get("name", "")) + str(credit.get("joinphrase", ""))
            for credit in credits
            if isinstance(credit, dict)
        ).strip()
        candidates.append(
            {
                "id": result.get("id", ""),
                "title": result.get("title", album_title),
                "artist": credited_artist or "Unknown artist",
                "date": result.get("first-release-date", "")[:4],
                "score": int(result.get("score", 0)),
            }
        )
    return [candidate for candidate in candidates if candidate["id"]]


def lookup_release_group(release_group_mbid: str) -> dict:
    url = (
        f"{MUSICBRAINZ_API}/release-group/{release_group_mbid}?"
        + urllib.parse.urlencode({"fmt": "json", "inc": "artists"})
    )
    result = request_json(url, musicbrainz=True)
    credits = result.get("artist-credit", [])
    credited_artist = "".join(
        str(credit.get("name", "")) + str(credit.get("joinphrase", ""))
        for credit in credits
        if isinstance(credit, dict)
    ).strip()
    return {
        "id": release_group_mbid,
        "title": result.get("title", "MusicBrainz album"),
        "artist": credited_artist or "Unknown artist",
        "date": result.get("first-release-date", "")[:4],
        "score": 100,
    }


def release_summary(result: dict) -> dict:
    media = sorted(result.get("media", []), key=lambda item: item.get("position", 0))
    credits = result.get("artist-credit", [])
    artist = "".join(
        str(credit.get("name", "")) + str(credit.get("joinphrase", ""))
        for credit in credits
        if isinstance(credit, dict)
    ).strip()
    return {
        "id": result.get("id", ""),
        "release_group_id": result.get("release-group", {}).get("id", ""),
        "title": result.get("title", "MusicBrainz album"),
        "artist": artist or "Unknown artist",
        "date": result.get("date", "")[:10],
        "country": result.get("country", ""),
        "status": result.get("status", ""),
        "disambiguation": result.get("disambiguation", ""),
        "media": media or [{"position": 1, "title": "", "format": ""}],
    }


def browse_releases(release_group_mbid: str) -> list[dict]:
    url = f"{MUSICBRAINZ_API}/release?" + urllib.parse.urlencode(
        {
            "release-group": release_group_mbid,
            "inc": "media+artist-credits",
            "fmt": "json",
            "limit": 100,
        }
    )
    data = request_json(url, musicbrainz=True)
    releases = [release_summary(result) for result in data.get("releases", [])]
    releases = [release for release in releases if release["id"]]
    releases.sort(
        key=lambda release: (
            -len(release["media"]),
            release["status"] != "Official",
            release["date"] or "9999",
            release["country"],
        )
    )
    return releases[:25]


def lookup_release(release_mbid: str) -> dict:
    url = f"{MUSICBRAINZ_API}/release/{release_mbid}?" + urllib.parse.urlencode(
        {"fmt": "json", "inc": "media+release-groups+artist-credits"}
    )
    return release_summary(request_json(url, musicbrainz=True))


def split_album_search(search_text: str, artist_override: str | None) -> tuple[str, str | None]:
    search_text = search_text.strip()
    if artist_override:
        return search_text, artist_override.strip()
    for separator in (" - ", " – ", " — "):
        if separator in search_text:
            artist, album = search_text.split(separator, 1)
            if artist.strip() and album.strip():
                return album.strip(), artist.strip()
    return search_text, None


def musicbrainz_reference(value: str) -> tuple[str, str] | None:
    match = re.search(
        r"musicbrainz\.org/(release-group|release)/"
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
        value,
    )
    if not match:
        return None
    return match.group(1), match.group(2).lower()


def comparable_album_title(value: str) -> str:
    value = normalise_label_text(clean_inline_markdown(value))
    value = value.strip().strip('"').strip("'")
    return re.sub(r"\s+", " ", value).casefold()


def search_release_groups_from_text(
    search_text: str, artist_override: str | None
) -> list[dict]:
    cleaned_search = clean_inline_markdown(search_text).strip().strip('"').strip("'")
    if artist_override:
        return search_release_groups(cleaned_search, artist_override.strip())

    # A hyphen can be part of an album title as well as the supported
    # "Artist - Album" shorthand. Prefer an exact full-title match before
    # interpreting it as a separator.
    full_title_candidates = search_release_groups(cleaned_search, None)
    expected_title = comparable_album_title(cleaned_search)
    exact_matches = [
        candidate
        for candidate in full_title_candidates
        if comparable_album_title(candidate["title"]) == expected_title
    ]
    if exact_matches:
        return exact_matches

    album_title, artist = split_album_search(cleaned_search, None)
    if artist:
        return search_release_groups(album_title, artist)
    return full_title_candidates


def choose_release_group(candidates: list[dict]) -> dict:
    if not candidates:
        raise ValueError("MusicBrainz returned no matching releases.")

    labels = []
    for index, candidate in enumerate(candidates, start=1):
        year = f" ({candidate['date']})" if candidate["date"] else ""
        labels.append(
            f"{index}. {candidate['title']} - {candidate['artist']}{year}"
        )

    if sys.platform == "darwin" and shutil.which("osascript"):
        escaped = [
            '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'
            for label in labels
        ]
        default_label = escaped[0]
        apple_script = (
            f"set choices to {{{', '.join(escaped)}}}\n"
            'set picked to choose from list choices with prompt '
            '"Choose the MusicBrainz album to use:" '
            f"default items {{{default_label}}}\n"
            "if picked is false then error number -128\n"
            "return item 1 of picked"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise SystemExit("No MusicBrainz release selected.")
        selection = result.stdout.strip()
        selected_index = labels.index(selection)
        return candidates[selected_index]

    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write("\n".join(labels) + "\nChoose release: ")
        terminal.flush()
        value = terminal.readline().strip()
    if not value.isdigit() or not 1 <= int(value) <= len(candidates):
        raise SystemExit("Invalid MusicBrainz release selection.")
    return candidates[int(value) - 1]


def choose_release(candidates: list[dict]) -> dict:
    if not candidates:
        raise ValueError("MusicBrainz returned no releases for that album.")
    if len(candidates) == 1:
        return candidates[0]

    labels = []
    for index, candidate in enumerate(candidates, start=1):
        formats = sorted(
            {medium.get("format", "") for medium in candidate["media"] if medium.get("format")}
        )
        medium_text = f"{len(candidate['media'])} disc" + (
            "s" if len(candidate["media"]) != 1 else ""
        )
        details = [medium_text, "/".join(formats), candidate["country"], candidate["date"]]
        if candidate["disambiguation"]:
            details.append(candidate["disambiguation"])
        labels.append(f"{index}. {candidate['title']} - " + " | ".join(filter(None, details)))

    if sys.platform == "darwin" and shutil.which("osascript"):
        escaped = [
            '"' + label.replace("\\", "\\\\").replace('"', '\\"') + '"'
            for label in labels
        ]
        apple_script = (
            f"set choices to {{{', '.join(escaped)}}}\n"
            'set picked to choose from list choices with prompt '
            '"Choose the release edition and disc count:" '
            f"default items {{{escaped[0]}}}\n"
            "if picked is false then error number -128\n"
            "return item 1 of picked"
        )
        result = subprocess.run(
            ["osascript", "-e", apple_script],
            check=False,
            text=True,
            capture_output=True,
        )
        if result.returncode != 0:
            raise SystemExit("No MusicBrainz release edition selected.")
        return candidates[labels.index(result.stdout.strip())]

    with open("/dev/tty", "r+", encoding="utf-8") as terminal:
        terminal.write("\n".join(labels) + "\nChoose release edition: ")
        terminal.flush()
        value = terminal.readline().strip()
    if not value.isdigit() or not 1 <= int(value) <= len(candidates):
        raise SystemExit("Invalid MusicBrainz release edition selection.")
    return candidates[int(value) - 1]


def preferred_image_url(image: dict) -> str | None:
    thumbnails = image.get("thumbnails", {})
    return (
        thumbnails.get("500")
        or thumbnails.get("1200")
        or thumbnails.get("250")
        or image.get("image")
    )


def image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if image_bytes.startswith(b"\x89PNG") and len(image_bytes) >= 24:
        return (
            int.from_bytes(image_bytes[16:20], "big"),
            int.from_bytes(image_bytes[20:24], "big"),
        )
    if image_bytes.startswith(b"\xff\xd8"):
        index = 2
        start_of_frame = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while index + 9 < len(image_bytes):
            if image_bytes[index] != 0xFF:
                index += 1
                continue
            marker = image_bytes[index + 1]
            index += 2
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(image_bytes):
                break
            segment_length = int.from_bytes(image_bytes[index:index + 2], "big")
            if marker in start_of_frame and index + 7 <= len(image_bytes):
                height = int.from_bytes(image_bytes[index + 3:index + 5], "big")
                width = int.from_bytes(image_bytes[index + 5:index + 7], "big")
                return width, height
            if segment_length < 2:
                break
            index += segment_length
    return None


def download_image_data_uri(url: str) -> tuple[str, tuple[int, int] | None]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        image_bytes = response.read()
        content_type = response.headers.get_content_type()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        if image_bytes.startswith(b"\x89PNG"):
            content_type = "image/png"
        elif image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:16]:
            content_type = "image/webp"
        else:
            content_type = "image/jpeg"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}", image_dimensions(image_bytes)


def fetch_cover_art(
    release_mbid: str,
    release_group_mbid: str,
) -> tuple[str, str, str | None, bool]:
    data = None
    for entity, mbid in (("release", release_mbid), ("release-group", release_group_mbid)):
        if not mbid:
            continue
        try:
            candidate = request_json(f"{COVER_ART_API}/{entity}/{mbid}")
        except urllib.error.HTTPError as error:
            if error.code == 404:
                continue
            raise
        if any(image.get("front") for image in candidate.get("images", [])):
            data = candidate
            break
    if data is None:
        raise ValueError("The selected MusicBrainz release has no front cover image.")
    images = [image for image in data.get("images", []) if image.get("approved", True)]
    front = next(
        (image for image in images if image.get("front")),
        next(
            (image for image in images if "front" in [str(t).lower() for t in image.get("types", [])]),
            None,
        ),
    )
    if not front:
        raise ValueError("The selected MusicBrainz release has no front cover image.")

    spine = next(
        (image for image in images if "spine" in [str(t).lower() for t in image.get("types", [])]),
        None,
    )
    front_url = preferred_image_url(front)
    spine_url = preferred_image_url(spine) if spine else None
    if not front_url:
        raise ValueError("The selected front cover has no downloadable image URL.")

    front_data, _ = download_image_data_uri(front_url)
    dedicated_spine = False
    spine_data = front_data
    if spine_url:
        candidate_spine, dimensions = download_image_data_uri(spine_url)
        # Cover Art Archive entries tagged Spine are often square back-cover
        # scans that merely include thin spines at their edges. Use the online
        # image directly only when it is actually a wide spine-shaped scan.
        if dimensions and dimensions[0] / max(dimensions[1], 1) >= 3:
            spine_data = candidate_spine
            dedicated_spine = True
    return front_data, spine_data, front_url, dedicated_spine


def cell_origin(position: int) -> tuple[float, float]:
    index = position - 1
    return GRID_X[index % 3], GRID_Y[index // 3]


def slugify(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value[:60] or "minidisc-label"


def approximate_em_width(value: str) -> float:
    width = 0.0
    for char in value:
        if char.isspace():
            width += 0.30
        elif char in "ilI.,:;'|!":
            width += 0.27
        elif char in "MW@%&":
            width += 0.85
        elif char.isupper():
            width += 0.64
        elif char.isdigit():
            width += 0.56
        else:
            width += 0.52
    return width


def wrap_words(text: str, max_width_mm: float, font_mm: float) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if approximate_em_width(candidate) * font_mm <= max_width_mm:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def layout_tracklist(
    tracks: list[tuple[str, str]],
) -> tuple[float, float, list[str]]:
    content_width = FACE_WIDTH_MM - 4
    content_height = FACE_HEIGHT_MM - 4

    for step in range(32, 9, -1):
        font_mm = step / 10
        rendered_lines: list[str] = []
        for marker, track in tracks:
            prefix = f"{marker} "
            prefix_width = approximate_em_width(prefix) * font_mm
            wrapped = wrap_words(track, content_width - prefix_width, font_mm)
            rendered_lines.append(prefix + wrapped[0])
            rendered_lines.extend("    " + line for line in wrapped[1:])
        line_height = font_mm * 1.24
        if len(rendered_lines) * line_height <= content_height:
            return font_mm, line_height, rendered_lines

    raise SystemExit(
        "The tracklist is too long to fit legibly on a 36 x 53 mm label. "
        "Shorten the track names or reduce the number of tracks."
    )


def svg_header(title: str, description: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg xmlns="http://www.w3.org/2000/svg"
     width="{PAGE_WIDTH_MM}mm" height="{PAGE_HEIGHT_MM}mm"
     viewBox="0 0 {PAGE_WIDTH_MM} {PAGE_HEIGHT_MM}" version="1.1">
  <title>{html.escape(title)}</title>
  <desc>{html.escape(description)}</desc>'''


def create_print_svg(
    album_title: str,
    tracks: list[tuple[str, str]],
    position: int,
    front_image: str | None = None,
    spine_image: str | None = None,
    art_source: str | None = None,
    disc_labels: list[str] | None = None,
) -> str:
    labels = disc_labels or [""]
    positions = list(range(position, position + len(labels)))
    source_note = f" Cover art source: {art_source}." if art_source else ""
    parts = [
        svg_header(
            f"{album_title} - printable MiniDisc labels",
            f"Artwork at A4 grid positions {positions}.{source_note}",
        ),
        '  <rect x="0" y="0" width="210" height="297" fill="#ffffff" />',
        "  <defs>",
    ]
    for index, grid_position in enumerate(positions, start=1):
        group_x, group_y = cell_origin(grid_position)
        face_x = group_x + 11.5
        edge_y = group_y + 55
        parts.extend(
            [
                f'    <clipPath id="face-clip-{index}"><rect x="{face_x}" y="{group_y}" width="36" height="53" rx="1" ry="1" /></clipPath>',
                f'    <clipPath id="edge-clip-{index}"><rect x="{group_x}" y="{edge_y}" width="59" height="4" rx="0.75" ry="0.75" /></clipPath>',
            ]
        )
    parts.append("  </defs>")

    for index, (grid_position, disc_label) in enumerate(zip(positions, labels), start=1):
        group_x, group_y = cell_origin(grid_position)
        face_x = group_x + 11.5
        face_y = group_y
        edge_x = group_x
        edge_y = group_y + 55
        edge_title = f"{album_title} - {disc_label}" if disc_label else album_title

        parts.append(f'  <g id="face-artwork-{index}" clip-path="url(#face-clip-{index})">')
        if front_image:
            parts.append(
                f'    <image x="{face_x}" y="{face_y}" width="36" height="53" preserveAspectRatio="xMidYMid slice" href="{front_image}" />'
            )
            if tracks:
                parts.append(
                    f'    <rect x="{face_x}" y="{face_y}" width="36" height="53" fill="#000000" fill-opacity="0.64" />'
                )
        if tracks:
            font_mm, line_height, lines = layout_tracklist(tracks)
            text_height = len(lines) * line_height
            first_baseline = face_y + (FACE_HEIGHT_MM - text_height) / 2 + font_mm
            text_colour = "#ffffff" if front_image else "#111111"
            for line_index, line in enumerate(lines):
                y = first_baseline + line_index * line_height
                parts.append(
                    f'    <text x="{face_x + 2:.2f}" y="{y:.2f}" '
                    f'font-family="Helvetica, Arial, sans-serif" font-size="{font_mm:.2f}" '
                    f'fill="{text_colour}" xml:space="preserve">{html.escape(line)}</text>'
                )
        if len(labels) > 1:
            badge_text = disc_label.upper()
            badge_length = ""
            if approximate_em_width(badge_text) * 2 > 12:
                badge_length = ' textLength="12" lengthAdjust="spacingAndGlyphs"'
            parts.extend(
                [
                    f'    <rect x="{face_x + 20}" y="{face_y + 46}" width="14" height="5" rx="1" fill="#000000" fill-opacity="0.72" />',
                    f'    <text x="{face_x + 27}" y="{face_y + 49.35}" text-anchor="middle" font-family="Helvetica, Arial, sans-serif" font-size="2" font-weight="700" fill="#ffffff"{badge_length}>{html.escape(badge_text)}</text>',
                ]
            )
        parts.append("  </g>")

        parts.append(f'  <g id="edge-artwork-{index}" clip-path="url(#edge-clip-{index})">')
        if spine_image:
            parts.extend(
                [
                    f'    <image x="{edge_x}" y="{edge_y}" width="59" height="4" preserveAspectRatio="xMidYMid slice" href="{spine_image}" />',
                    f'    <rect x="{edge_x}" y="{edge_y}" width="59" height="4" fill="#000000" fill-opacity="0.48" />',
                ]
            )
        title_font_mm = 2.2
        length_attribute = ""
        if approximate_em_width(edge_title) * title_font_mm > EDGE_WIDTH_MM - 4:
            length_attribute = f' textLength="{EDGE_WIDTH_MM - 4}" lengthAdjust="spacingAndGlyphs"'
        title_colour = "#ffffff" if spine_image else "#111111"
        parts.append(
            f'    <text x="{edge_x + EDGE_WIDTH_MM / 2:.2f}" y="{edge_y + 2.72:.2f}" text-anchor="middle" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="{title_font_mm:.2f}" font-weight="600" '
            f'fill="{title_colour}"{length_attribute}>{html.escape(edge_title)}</text>'
        )
        parts.append("  </g>")

    parts.append("</svg>")
    return "\n".join(parts)


def registration_marks_svg() -> list[str]:
    return [
        '  <g id="registration-marks" fill="none" stroke="#000000" stroke-width="0.35" stroke-linecap="square">',
        '    <path d="M 5 5 H 12 M 5 5 V 12" />',
        '    <path d="M 205 5 H 198 M 205 5 V 12" />',
        '    <path d="M 5 292 H 12 M 5 292 V 285" />',
        '    <path d="M 205 292 H 198 M 205 292 V 285" />',
        "  </g>",
    ]


def create_cut_svg_for_positions(
    album_title: str,
    positions: list[int],
    *,
    include_registration_marks: bool = False,
) -> str:
    positions = sorted(set(positions))
    parts = [
        svg_header(
            f"{album_title} - MiniDisc cut paths",
            f"Cut paths at A4 grid positions {positions}. Use at 100 percent scale.",
        ),
        '  <g id="cut-paths" fill="none" stroke="#ff0000" stroke-width="0.1" vector-effect="non-scaling-stroke">',
    ]
    for grid_position in positions:
        group_x, group_y = cell_origin(grid_position)
        face_x = group_x + 11.5
        edge_y = group_y + 55
        parts.extend(
            [
                f'    <rect id="face-label-position-{grid_position}" x="{face_x}" y="{group_y}" width="36" height="53" rx="1" ry="1" />',
                f'    <rect id="edge-label-position-{grid_position}" x="{group_x}" y="{edge_y}" width="59" height="4" rx="0.75" ry="0.75" />',
            ]
        )
    parts.append("  </g>")
    if include_registration_marks:
        parts.extend(registration_marks_svg())
    parts.append("</svg>")
    return "\n".join(parts)


def create_cut_svg(album_title: str, position: int, label_count: int = 1) -> str:
    return create_cut_svg_for_positions(
        album_title, list(range(position, position + label_count))
    )


def find_rsvg_convert() -> str:
    candidates = (
        shutil.which("rsvg-convert"),
        "/opt/homebrew/bin/rsvg-convert",
        "/usr/local/bin/rsvg-convert",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise SystemExit(
        "rsvg-convert is required to create the PDF. Install it once with: "
        "brew install librsvg"
    )


def positions_from_cut_svg(cut_path: Path) -> list[int]:
    try:
        root = ET.parse(cut_path).getroot()
    except (ET.ParseError, OSError) as error:
        raise ValueError(f"Could not read cut file {cut_path.name}: {error}") from error

    positions = []
    for element in root.iter():
        if not element.tag.endswith("rect"):
            continue
        element_id = element.attrib.get("id", "")
        if not element_id.startswith("face-label"):
            continue
        try:
            x = float(element.attrib["x"])
            y = float(element.attrib["y"])
        except (KeyError, ValueError):
            continue
        for position in range(1, 13):
            cell_x, cell_y = cell_origin(position)
            if abs(x - (cell_x + 11.5)) < 0.2 and abs(y - cell_y) < 0.2:
                positions.append(position)
                break
    return sorted(set(positions))


def collect_aggregate_sources(output_dir: Path) -> tuple[list[dict], list[int]]:
    owners: dict[int, tuple[int, str, Path]] = {}
    for pdf_path in sorted(output_dir.glob("*.pdf")):
        if pdf_path.name == "ready-to-print.pdf":
            continue
        cut_path = pdf_path.with_name(f"{pdf_path.stem}-cut.svg")
        if not cut_path.is_file():
            print(
                f"Aggregate: skipping {pdf_path.name} because its cut SVG is missing.",
                file=sys.stderr,
            )
            continue
        positions = positions_from_cut_svg(cut_path)
        if not positions:
            print(
                f"Aggregate: skipping {pdf_path.name} because no grid positions were found.",
                file=sys.stderr,
            )
            continue
        version = (pdf_path.stat().st_mtime_ns, pdf_path.name, pdf_path)
        for position in positions:
            if position not in owners or version[:2] > owners[position][:2]:
                owners[position] = version

    positions_by_pdf: dict[Path, list[int]] = {}
    for position, (_, _, pdf_path) in owners.items():
        positions_by_pdf.setdefault(pdf_path, []).append(position)
    sources = [
        {"path": str(pdf_path), "positions": sorted(positions)}
        for pdf_path, positions in positions_by_pdf.items()
    ]
    sources.sort(key=lambda source: min(source["positions"]))
    return sources, sorted(owners)


def create_ready_to_print(output_dir: Path, sources: list[dict]) -> Path:
    helper_path = PROJECT_ROOT / "scripts" / "merge_label_pdfs.swift"
    if not helper_path.is_file():
        raise SystemExit(f"Aggregate PDF helper is missing: {helper_path}")
    if not Path("/usr/bin/xcrun").is_file():
        raise SystemExit(
            "The macOS Xcode Command Line Tools are required to combine label PDFs. "
            "Install them with: xcode-select --install"
        )

    ready_path = output_dir / "ready-to-print.pdf"
    with tempfile.TemporaryDirectory(
        prefix=".minidisc-ready-", dir=output_dir
    ) as temporary_dir:
        temporary_path = Path(temporary_dir)
        pending_pdf = temporary_path / "ready-to-print.pdf"
        plan_path = temporary_path / "aggregate-plan.json"
        plan_path.write_text(
            json.dumps({"output": str(pending_pdf), "sources": sources}),
            encoding="utf-8",
        )
        environment = os.environ.copy()
        environment["SWIFT_MODULECACHE_PATH"] = str(temporary_path / "swift-cache")
        environment["CLANG_MODULE_CACHE_PATH"] = str(temporary_path / "clang-cache")
        result = subprocess.run(
            ["/usr/bin/xcrun", "swift", str(helper_path), str(plan_path)],
            check=False,
            text=True,
            capture_output=True,
            env=environment,
        )
        if result.returncode != 0 or not pending_pdf.is_file():
            detail = result.stderr.strip() or "unknown PDF merge error"
            raise SystemExit(f"Could not create ready-to-print.pdf: {detail}")
        os.replace(pending_pdf, ready_path)
    return ready_path


def update_ready_outputs(output_dir: Path) -> tuple[Path, Path]:
    sources, positions = collect_aggregate_sources(output_dir)
    if not positions:
        raise SystemExit("No positioned label PDFs were found for the aggregate sheet.")

    ready_cut_path = output_dir / "ready-to-cut.svg"
    pending_cut_path = output_dir / ".ready-to-cut.svg.tmp"
    pending_cut_path.write_text(
        create_cut_svg_for_positions(
            "Ready to cut",
            positions,
            include_registration_marks=True,
        ),
        encoding="utf-8",
    )
    os.replace(pending_cut_path, ready_cut_path)
    ready_print_path = create_ready_to_print(output_dir, sources)
    print(
        "Aggregate positions: " + ", ".join(str(position) for position in positions),
        file=sys.stderr,
    )
    return ready_print_path, ready_cut_path


def main() -> None:
    args = parse_args()
    input_text = read_optional_input(args.input)
    if args.mode:
        mode = args.mode
    elif args.release or args.release_group or args.search:
        mode = "musicbrainz"
    elif args.no_online_art:
        mode = "manual"
    else:
        mode = choose_mode()

    front_image: str | None = None
    spine_image: str | None = None
    art_source: str | None = None
    disc_labels = [""]

    if mode == "manual":
        if not input_text:
            raise SystemExit(
                "Manual mode requires Markdown or CSV on standard input or via --input FILE."
            )
        input_format = manual_input_format(input_text, args.input, args.input_format)
        album_title, _, tracks = parse_manual_input(
            input_text, args.input, args.input_format
        )
        album_title = normalise_label_text(album_title)
        print(f"Workflow: manual {input_format} title and tracklist", file=sys.stderr)
    else:
        try:
            selected_group = None
            if args.release:
                selected_release = lookup_release(args.release)
            else:
                if args.release_group:
                    selected_group = lookup_release_group(args.release_group)
                else:
                    search_text = args.search or input_text or prompt_search_text()
                    reference = musicbrainz_reference(search_text)
                    if reference and reference[0] == "release":
                        selected_release = lookup_release(reference[1])
                    else:
                        if reference:
                            selected_group = lookup_release_group(reference[1])
                        else:
                            candidates = search_release_groups_from_text(
                                search_text, args.artist
                            )
                            selected_group = choose_release_group(candidates)
                if selected_group:
                    releases = browse_releases(selected_group["id"])
                    selected_release = choose_release(releases)

            release_group_mbid = (
                selected_release["release_group_id"]
                or (selected_group["id"] if selected_group else "")
            )
            album_title = normalise_label_text(selected_release["title"])
            media = selected_release["media"]
            if len(media) > 1:
                disc_labels = [
                    normalise_label_text(str(medium.get("title", "")).strip())
                    or f"Disc {index}"
                    for index, medium in enumerate(media, start=1)
                ]
            if args.no_online_art:
                raise ValueError("MusicBrainz mode requires online cover artwork.")
            (
                front_image,
                spine_image,
                art_source,
                dedicated_spine,
            ) = fetch_cover_art(selected_release["id"], release_group_mbid)
            spine_note = (
                "dedicated spine scan" if dedicated_spine else "front-cover strip"
            )
            print(f"Artwork: {spine_note}", file=sys.stderr)
            tracks = []
            artist = (
                selected_group["artist"] if selected_group else selected_release["artist"]
            )
            print(
                f"Workflow: MusicBrainz artwork - {album_title} - {artist} "
                f"({len(media)} disc{'s' if len(media) != 1 else ''})",
                file=sys.stderr,
            )
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            raise SystemExit(f"MusicBrainz lookup failed: {error}") from error

    label_count = len(disc_labels)
    if label_count > 12:
        raise SystemExit(
            f"This release has {label_count} discs, but the A4 layout has only 12 positions."
        )
    position = args.position or choose_position(label_count)
    if position + label_count - 1 > 12:
        raise SystemExit(
            f"A {label_count}-disc release cannot start at position {position}. "
            f"Choose position {13 - label_count} or earlier."
        )
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    last_position = position + label_count - 1
    position_text = (
        f"position-{position:02d}"
        if label_count == 1
        else f"positions-{position:02d}-{last_position:02d}"
    )
    base_name = f"{slugify(album_title)}-{position_text}"
    pdf_path = output_dir / f"{base_name}.pdf"
    cut_path = output_dir / f"{base_name}-cut.svg"
    cut_path.write_text(
        create_cut_svg(album_title, position, label_count), encoding="utf-8"
    )

    with tempfile.TemporaryDirectory(prefix="minidisc-label-") as temporary_dir:
        artwork_svg = Path(temporary_dir) / "artwork.svg"
        artwork_svg.write_text(
            create_print_svg(
                album_title,
                tracks,
                position,
                front_image=front_image,
                spine_image=spine_image,
                art_source=art_source,
                disc_labels=disc_labels,
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [find_rsvg_convert(), "--format=pdf", f"--output={pdf_path}", artwork_svg],
            check=True,
        )

    ready_print_path, ready_cut_path = update_ready_outputs(output_dir)
    print(pdf_path)
    print(cut_path)
    print(ready_print_path)
    print(ready_cut_path)


if __name__ == "__main__":
    main()
