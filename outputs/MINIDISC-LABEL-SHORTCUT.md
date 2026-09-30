# MiniDisc label Shortcut

The script searches MusicBrainz and the Cover Art Archive, then creates one
printable A4 PDF and one matching A4 SVG cut file. It uses the 12-position grid
from the supplied label-pair template. Positions run left-to-right and then
top-to-bottom:

```text
 1   2   3
 4   5   6
 7   8   9
10  11  12
```

## One-time requirement

The PDF converter is already installed on the Mac used to create this script.
If it is missing on another Mac, install it with:

```sh
brew install librsvg
```

## Create the macOS Shortcut

1. Add an **Ask for Input** action.
2. Choose **Text** and enable multiple lines.
3. Add a **Run Shell Script** action.
4. Set **Pass Input** to **to stdin**.
5. Use this command, updating the path if the script is moved:

```sh
/usr/bin/env python3 "/Volumes/External/Development/mdlabeller/outputs/minidisc_label_maker.py"
```

The script displays a position chooser and then a MusicBrainz match chooser.
It uses the selected front cover behind the tracklist. If the Cover Art Archive
has an image tagged as **Spine**, that image is used for the edge label;
otherwise a matching horizontal strip is derived from the front cover. The
files are saved in `~/Desktop/MiniDisc Labels` and their full paths are returned
to the Shortcut.

## Markdown format

```markdown
# Album title

Artist: Artist name

1. First track
2. Second track
3. Third track
```

The `Artist:` line is optional, but including it produces more accurate search
results when different artists have albums with the same title. Unordered lists
using `-`, `*`, or `+` also work. The album title is placed over the edge art.
The tracklist is wrapped and scaled over a darkened version of the front cover.

## Command-line use

To bypass the position chooser, provide `--position`:

```sh
/usr/bin/env python3 minidisc_label_maker.py --position 5 --input album.md
```

Use `--artist "Artist name"` to narrow a search from the command line, or
`--release-group MBID` to use an exact MusicBrainz release group. To create the
original text-only design without internet access, add `--no-online-art`.

Print the PDF at **100% / Actual Size**. Import the matching SVG into the cutter
without resizing and keep the A4 page origin unchanged.

Cover art is retrieved from the community-curated Cover Art Archive. Artwork
rights remain with their respective owners; use downloaded images appropriately.
