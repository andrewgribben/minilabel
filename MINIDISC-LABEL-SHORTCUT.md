# MiniDisc label Shortcut

The script has two label workflows, with separate Markdown and CSV routes in
the Shortcut. It creates one printable A4 PDF plus one matching A4 SVG cut file:

1. **Markdown tracklist** - paste a disc title and tracklist.
2. **CSV tracklist** - select a CSV containing a disc title and tracks.
3. **MusicBrainz** - search for an album, choose the album and release edition,
   and retrieve its title, artwork, and disc count automatically.

Both workflows use the same 12-position reusable-sheet grid. Positions run
left-to-right and then top-to-bottom:

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

The combined ready-to-print sheet uses Apple's Swift PDF support. If macOS
reports that the developer tools are missing, install them once with:

```sh
xcode-select --install
```

## Create the macOS Shortcut

1. Add a **Choose from Menu** action with the prompt `Label source`.
2. Add three menu items: **Markdown tracklist**, **CSV tracklist**, and
   **MusicBrainz artwork**.
3. Under **Markdown tracklist**:
   - Add **Ask for Input**, select **Text**, enable multiple lines, and use the
     prompt `Paste the Markdown disc title and tracklist`.
   - Add **Run Shell Script**, set **Pass Input** to **to stdin**, and use:

   ```sh
   /usr/bin/env python3 "/Volumes/External/Development/mdlabeller/minidisc_label_maker.py" --mode manual
   ```

4. Under **CSV tracklist**:
   - Add **Select File** and leave **Select Multiple** turned off.
   - Add **Run Shell Script**, set **Pass Input** to **as arguments**, and use:

   ```sh
   /usr/bin/env python3 "/Volumes/External/Development/mdlabeller/minidisc_label_maker.py" --mode manual --input "$1"
   ```

5. Under **MusicBrainz artwork**:
   - Add **Ask for Input**, select **Text**, and use the prompt
     `Enter an album title, Artist - Album title, or MusicBrainz URL`.
   - Add **Run Shell Script**, set **Pass Input** to **to stdin**, and use:

   ```sh
   /usr/bin/env python3 "/Volumes/External/Development/mdlabeller/minidisc_label_maker.py" --mode musicbrainz
   ```

Each route asks for the reusable-sheet grid position. The MusicBrainz route
also displays album and release-edition choosers. For multi-disc releases, pick
the first unused position; the remaining labels use the following positions in
left-to-right, top-to-bottom order. Generated PDFs and cut SVGs are saved in
`/Volumes/External/Development/mdlabeller/output/`, and their full paths are
returned to the Shortcut. There is no initial shared or empty input prompt.

Every run also rebuilds `ready-to-print.pdf` and `ready-to-cut.svg` from all
label PDF/cut-SVG pairs still in `output/`. The ready files contain only the
occupied grid positions and include matching corner registration marks. If two
PDFs claim the same position, the newest one replaces the older one in the
aggregate. Removing both files for an unwanted label removes it the next time
the generator runs.

## Markdown workflow

The **Markdown tracklist** input must use this format:

```markdown
# Album title

Artist: Artist name

1. First track
2. Second track
3. Third track
```

The `Artist:` line is optional and is not printed. Unordered lists using `-`,
`*`, or `+` also work. The H1 becomes the edge-label title and the tracklist is
wrapped and scaled to fit the face label. This route does not contact
MusicBrainz or download artwork.

## CSV workflow

The CSV must have a header row. Use `disc_title`, `track_number`, and
`track_title`; `track_number` and `artist` are optional. The disc title may be
repeated on every row or supplied only on the first row:

```csv
disc_title,artist,track_number,track_title
Night Drive,Example Artist,1,City Lights
,,2,Last Train Home
```

Common alternatives such as `album_title`, `track`, `song`, `number`, and
`position` are also accepted. Quoted commas in titles work normally. The H1
equivalent (`disc_title`) becomes the edge-label title, and the tracklist is
wrapped and scaled to fit the face label. This route stays offline.

## MusicBrainz workflow

Enter an album title, `Artist - Album title`, or paste a MusicBrainz release or
release-group URL. A release URL selects that exact edition immediately. For a
title search, select the correct album and then the relevant release edition and
disc count. The title and media information come from MusicBrainz. The front
cover becomes the complete face label without a tracklist overlay.
A suitable wide image tagged as **Spine** is used for the edge background when
available; otherwise the edge label uses a matching horizontal strip derived
from the front cover. Multi-disc editions create one face and edge pair per
medium. Each face receives a small disc badge, and each edge includes its medium
title when MusicBrainz provides one, otherwise `Disc 1`, `Disc 2`, and so on.
Newly submitted Cover Art Archive images can be used while their community
approval is still pending; the script reports this in its run output.

## Command-line use

Manual Markdown mode:

```sh
/usr/bin/env python3 minidisc_label_maker.py \
  --mode manual --position 5 --input work/album.md
```

Manual CSV mode:

```sh
/usr/bin/env python3 minidisc_label_maker.py \
  --mode manual --position 5 --input work/example-album.csv
```

MusicBrainz mode:

```sh
/usr/bin/env python3 minidisc_label_maker.py \
  --mode musicbrainz --position 5 --search "Radiohead - OK Computer"
```

Use `--artist "Artist name"` with an album title to narrow the search, or
`--release-group MBID` to use an exact MusicBrainz release group. Use
`--release MBID` to select an exact release edition and skip both choosers.

Print the PDF at **100% / Actual Size**. Import the matching SVG into the cutter
without resizing and keep the A4 page origin unchanged. Use
`ready-to-print.pdf` with `ready-to-cut.svg` when printing the combined sheet.
The SVG keeps registration marks in a separate `registration-marks` group from
the red label paths so they can be treated separately by cutting software.

Cover art is retrieved from the community-curated Cover Art Archive. Artwork
rights remain with their respective owners; use downloaded images appropriately.
