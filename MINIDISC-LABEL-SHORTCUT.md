# MiniDisc label Shortcut

The script has two workflows and creates one printable A4 PDF plus one matching
A4 SVG cut file:

1. **Manual** - provide a disc title and Markdown tracklist.
2. **MusicBrainz** - search for an album, choose the album and release edition,
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

## Create the macOS Shortcut

1. Add a **Choose from Menu** action with the prompt `Label source`.
2. Add two menu items: **Manual tracklist** and **MusicBrainz artwork**.
3. Under **Manual tracklist**:
   - Add **Ask for Input**, select **Text**, enable multiple lines, and use the
     prompt `Paste the Markdown disc title and tracklist`.
   - Add **Run Shell Script**, set **Pass Input** to **to stdin**, and use:

   ```sh
   /usr/bin/env python3 "/Volumes/External/Development/mdlabeller/minidisc_label_maker.py" --mode manual
   ```

4. Under **MusicBrainz artwork**:
   - Add **Ask for Input**, select **Text**, and use the prompt
     `Enter an album title, or Artist - Album title`.
   - Add **Run Shell Script**, set **Pass Input** to **to stdin**, and use:

   ```sh
   /usr/bin/env python3 "/Volumes/External/Development/mdlabeller/minidisc_label_maker.py" --mode musicbrainz
   ```

Each branch asks for the reusable-sheet grid position. The MusicBrainz branch
also displays album and release-edition choosers. For multi-disc releases, pick
the first unused position; the remaining labels use the following positions in
left-to-right, top-to-bottom order. Generated PDFs and cut SVGs are saved in
`/Volumes/External/Development/mdlabeller/output/`, and their full paths are
returned to the Shortcut. There is no initial shared or empty input prompt.

## Manual workflow

The **Manual tracklist** input must use this Markdown format:

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

## MusicBrainz workflow

Enter either an album title or `Artist - Album title`, select the correct album,
then select the relevant release edition and disc count. The title and media
information come from MusicBrainz. The front cover becomes the complete face
label without a tracklist overlay.
A suitable wide image tagged as **Spine** is used for the edge background when
available; otherwise the edge label uses a matching horizontal strip derived
from the front cover. Multi-disc editions create one face and edge pair per
medium. Each face receives a small disc badge, and each edge includes its medium
title when MusicBrainz provides one, otherwise `Disc 1`, `Disc 2`, and so on.

## Command-line use

Manual mode:

```sh
/usr/bin/env python3 minidisc_label_maker.py \
  --mode manual --position 5 --input work/album.md
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
without resizing and keep the A4 page origin unchanged.

Cover art is retrieved from the community-curated Cover Art Archive. Artwork
rights remain with their respective owners; use downloaded images appropriately.
