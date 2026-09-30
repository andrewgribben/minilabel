#!/usr/bin/env python3
"""Create one printable MiniDisc label pair at a selected A4 grid position.

Input Markdown:
    # Album title
    1. First track
    2. Second track

There are two workflows: manual Markdown input, or a MusicBrainz search that
supplies the title, tracklist and cover art. The script writes an artwork PDF
and a matching SVG cut file. If --position is not supplied, macOS displays a
position chooser numbered left-to-right and then top-to-bottom.
"""

from __future__ import annotations

import argparse
import base64
import html
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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
        help="Input file: Markdown in manual mode, or search text in MusicBrainz mode.",
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
        help="Use a specific MusicBrainz release-group MBID and skip the match chooser.",
    )
    parser.add_argument(
        "--no-online-art",
        action="store_true",
        help="Create the original text-only labels without querying MusicBrainz.",
    )
    return parser.parse_args()


def read_optional_input(input_path: Path | None) -> str:
    if input_path:
        return input_path.expanduser().read_text(encoding="utf-8").strip()
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


def choose_position() -> int:
    choices = []
    for position in range(1, 13):
        row = (position - 1) // 3 + 1
        column = (position - 1) % 3 + 1
        choices.append(f'"{position} - row {row}, column {column}"')

    if sys.platform == "darwin" and shutil.which("osascript"):
        apple_script = (
            f"set choices to {{{', '.join(choices)}}}\n"
            'set picked to choose from list choices with prompt '
            '"Choose the unused A4 label position (left-to-right, top-to-bottom):" '
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
        terminal.write("Choose grid position 1-12: ")
        terminal.flush()
        value = terminal.readline().strip()
    if not value.isdigit() or not 1 <= int(value) <= 12:
        raise SystemExit("Grid position must be a number from 1 to 12.")
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


def tracks_from_release(release: dict) -> list[tuple[str, str]]:
    titles: list[str] = []
    for medium in release.get("media", []):
        for track in medium.get("tracks", []):
            title = track.get("title") or track.get("recording", {}).get("title")
            if title:
                titles.append(clean_inline_markdown(str(title)))
    return [(f"{index}.", title) for index, title in enumerate(titles, start=1)]


def fetch_release_tracks(
    release_group_mbid: str,
    preferred_release_mbid: str | None = None,
) -> list[tuple[str, str]]:
    if preferred_release_mbid:
        url = (
            f"{MUSICBRAINZ_API}/release/{preferred_release_mbid}?"
            + urllib.parse.urlencode({"inc": "recordings", "fmt": "json"})
        )
        release = request_json(url, musicbrainz=True)
        tracks = tracks_from_release(release)
        if tracks:
            return tracks

    url = f"{MUSICBRAINZ_API}/release/?" + urllib.parse.urlencode(
        {
            "release-group": release_group_mbid,
            "inc": "recordings",
            "fmt": "json",
            "limit": 25,
        }
    )
    data = request_json(url, musicbrainz=True)
    releases = [release for release in data.get("releases", []) if tracks_from_release(release)]
    if not releases:
        raise ValueError("MusicBrainz has no tracklist for the selected album.")

    def release_rank(release: dict) -> tuple[int, int, str]:
        official_penalty = 0 if release.get("status") == "Official" else 1
        media_penalty = 0 if len(release.get("media", [])) == 1 else 1
        return official_penalty, media_penalty, release.get("date", "9999")

    return tracks_from_release(min(releases, key=release_rank))


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
    release_group_mbid: str,
) -> tuple[str, str, str | None, bool, str | None]:
    metadata_url = f"{COVER_ART_API}/release-group/{release_group_mbid}"
    data = request_json(metadata_url)
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
    release_url = str(data.get("release", ""))
    release_mbid = release_url.rstrip("/").rsplit("/", 1)[-1] if release_url else None
    return front_data, spine_data, front_url, dedicated_spine, release_mbid


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
) -> str:
    group_x, group_y = cell_origin(position)
    face_x = group_x + 11.5
    face_y = group_y
    edge_x = group_x
    edge_y = group_y + 55
    font_mm, line_height, lines = layout_tracklist(tracks)
    text_height = len(lines) * line_height
    first_baseline = face_y + (FACE_HEIGHT_MM - text_height) / 2 + font_mm

    track_elements = []
    text_colour = "#ffffff" if front_image else "#111111"
    for index, line in enumerate(lines):
        y = first_baseline + index * line_height
        track_elements.append(
            f'    <text x="{face_x + 2:.2f}" y="{y:.2f}" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="{font_mm:.2f}" '
            f'fill="{text_colour}" xml:space="preserve">{html.escape(line)}</text>'
        )

    title_font_mm = 2.2
    title_width = approximate_em_width(album_title) * title_font_mm
    length_attribute = ""
    if title_width > EDGE_WIDTH_MM - 4:
        length_attribute = f' textLength="{EDGE_WIDTH_MM - 4}" lengthAdjust="spacingAndGlyphs"'

    face_background = []
    if front_image:
        face_background.extend(
            [
                f'    <image x="{face_x}" y="{face_y}" width="36" height="53" preserveAspectRatio="xMidYMid slice" href="{front_image}" />',
                f'    <rect x="{face_x}" y="{face_y}" width="36" height="53" fill="#000000" fill-opacity="0.64" />',
            ]
        )

    edge_background = []
    if spine_image:
        edge_background.extend(
            [
                f'    <image x="{edge_x}" y="{edge_y}" width="59" height="4" preserveAspectRatio="xMidYMid slice" href="{spine_image}" />',
                f'    <rect x="{edge_x}" y="{edge_y}" width="59" height="4" fill="#000000" fill-opacity="0.48" />',
            ]
        )
    title_colour = "#ffffff" if spine_image else "#111111"
    source_note = f" Cover art source: {art_source}." if art_source else ""

    return "\n".join(
        [
            svg_header(
                f"{album_title} - printable MiniDisc labels",
                f"Artwork at A4 grid position {position}.{source_note}",
            ),
            '  <rect x="0" y="0" width="210" height="297" fill="#ffffff" />',
            "  <defs>",
            f'    <clipPath id="face-clip"><rect x="{face_x}" y="{face_y}" width="36" height="53" rx="1" ry="1" /></clipPath>',
            f'    <clipPath id="edge-clip"><rect x="{edge_x}" y="{edge_y}" width="59" height="4" rx="0.75" ry="0.75" /></clipPath>',
            "  </defs>",
            f'  <g id="face-artwork" clip-path="url(#face-clip)">',
            *face_background,
            *track_elements,
            "  </g>",
            f'  <g id="edge-artwork" clip-path="url(#edge-clip)">',
            *edge_background,
            f'    <text x="{edge_x + EDGE_WIDTH_MM / 2:.2f}" y="{edge_y + 2.72:.2f}" text-anchor="middle" '
            f'font-family="Helvetica, Arial, sans-serif" font-size="{title_font_mm:.2f}" font-weight="600" '
            f'fill="{title_colour}"{length_attribute}>{html.escape(album_title)}</text>',
            "  </g>",
            "</svg>",
        ]
    )


def create_cut_svg(album_title: str, position: int) -> str:
    group_x, group_y = cell_origin(position)
    face_x = group_x + 11.5
    face_y = group_y
    edge_x = group_x
    edge_y = group_y + 55
    return "\n".join(
        [
            svg_header(
                f"{album_title} - MiniDisc cut paths",
                f"Cut paths at A4 grid position {position}. Use at 100 percent scale.",
            ),
            '  <g id="cut-paths" fill="none" stroke="#ff0000" stroke-width="0.1" vector-effect="non-scaling-stroke">',
            f'    <rect id="face-label" x="{face_x}" y="{face_y}" width="36" height="53" rx="1" ry="1" />',
            f'    <rect id="edge-label" x="{edge_x}" y="{edge_y}" width="59" height="4" rx="0.75" ry="0.75" />',
            "  </g>",
            "</svg>",
        ]
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


def main() -> None:
    args = parse_args()
    input_text = read_optional_input(args.input)
    if args.mode:
        mode = args.mode
    elif args.release_group or args.search:
        mode = "musicbrainz"
    elif args.no_online_art:
        mode = "manual"
    else:
        mode = choose_mode()

    front_image: str | None = None
    spine_image: str | None = None
    art_source: str | None = None

    if mode == "manual":
        if not input_text:
            raise SystemExit(
                "Manual mode requires Markdown on standard input or via --input FILE."
            )
        album_title, _, tracks = parse_markdown(input_text)
        print("Workflow: manual title and tracklist", file=sys.stderr)
    else:
        try:
            if args.release_group:
                selected = lookup_release_group(args.release_group)
            else:
                search_text = args.search or input_text or prompt_search_text()
                album_query, artist_query = split_album_search(search_text, args.artist)
                candidates = search_release_groups(album_query, artist_query)
                selected = choose_release_group(candidates)

            release_group_mbid = selected["id"]
            album_title = selected["title"]
            preferred_release_mbid: str | None = None

            if not args.no_online_art:
                try:
                    (
                        front_image,
                        spine_image,
                        art_source,
                        dedicated_spine,
                        preferred_release_mbid,
                    ) = fetch_cover_art(release_group_mbid)
                    spine_note = (
                        "dedicated spine scan"
                        if dedicated_spine
                        else "front-cover strip"
                    )
                    print(f"Artwork: {spine_note}", file=sys.stderr)
                except (ValueError, urllib.error.URLError, TimeoutError) as error:
                    print(
                        f"Warning: cover art could not be loaded ({error}). "
                        "Continuing with a text-only label.",
                        file=sys.stderr,
                    )

            tracks = fetch_release_tracks(
                release_group_mbid,
                preferred_release_mbid=preferred_release_mbid,
            )
            print(
                f"Workflow: MusicBrainz - {album_title} - {selected['artist']}",
                file=sys.stderr,
            )
        except (ValueError, urllib.error.URLError, TimeoutError) as error:
            raise SystemExit(f"MusicBrainz lookup failed: {error}") from error

    position = args.position or choose_position()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = f"{slugify(album_title)}-position-{position:02d}"
    pdf_path = output_dir / f"{base_name}.pdf"
    cut_path = output_dir / f"{base_name}-cut.svg"
    cut_path.write_text(create_cut_svg(album_title, position), encoding="utf-8")

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
            ),
            encoding="utf-8",
        )
        subprocess.run(
            [find_rsvg_convert(), "--format=pdf", f"--output={pdf_path}", artwork_svg],
            check=True,
        )

    print(pdf_path)
    print(cut_path)


if __name__ == "__main__":
    main()
