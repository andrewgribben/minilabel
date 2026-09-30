# MiniDisc label Shortcut

The script has two workflows and creates one printable A4 PDF plus one matching
A4 SVG cut file:

1. **Manual** - provide a disc title and Markdown tracklist.
2. **MusicBrainz** - search for an album, choose the match, and retrieve its
   title and artwork automatically.

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

1. Add an **Ask for Input** action with the prompt: `Paste Markdown, or enter Artist - Album`.
2. Choose **Text** and enable multiple lines.
3. Add a **Run Shell Script** action.
4. Set **Pass Input** to **to stdin**.
5. Use this command, updating the path if the script is moved:

```sh
/usr/bin/env python3 "/Volumes/External/Development/mdlabeller/output/minidisc_label_maker.py"
```

The script asks which workflow to use and then asks for the reusable-sheet grid
position. In MusicBrainz mode it also displays a match chooser. Generated PDFs
and cut SVGs are saved in `/Volumes/External/Development/mdlabeller/output/`,
and their full paths are returned to the Shortcut.

## Manual workflow

Choose **Manual - enter disc title and tracklist**. The Shortcut input must use
this Markdown format:

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

Enter either an album title or `Artist - Album title` in the Shortcut input,
then choose **MusicBrainz - search and match an album**. Select the correct
result from the match chooser. The title comes from MusicBrainz. The front
cover becomes the complete face label without a tracklist overlay.
A suitable wide image tagged as **Spine** is used for the edge background when
available; otherwise the edge label uses a matching horizontal strip derived
from the front cover. The MusicBrainz album title is printed over that strip.

## Command-line use

Manual mode:

```sh
/usr/bin/env python3 output/minidisc_label_maker.py \
  --mode manual --position 5 --input work/album.md
```

MusicBrainz mode:

```sh
/usr/bin/env python3 output/minidisc_label_maker.py \
  --mode musicbrainz --position 5 --search "Radiohead - OK Computer"
```

Use `--artist "Artist name"` with an album title to narrow the search, or
`--release-group MBID` to use an exact MusicBrainz release group.

Print the PDF at **100% / Actual Size**. Import the matching SVG into the cutter
without resizing and keep the A4 page origin unchanged.

Cover art is retrieved from the community-curated Cover Art Archive. Artwork
rights remain with their respective owners; use downloaded images appropriately.
