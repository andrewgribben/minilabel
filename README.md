# MiniDisc label maker

Creates one printable MiniDisc face label and one edge label at a selected
position on the reusable 12-position A4 layout.

The generator supports two workflows:

- **Manual:** provide a Markdown H1 disc title and ordered or unordered tracklist.
- **MusicBrainz:** search for an album and use its Cover Art Archive image as
  the complete face label, with the album title on the edge label.

Run the generator from the project root:

```sh
/usr/bin/env python3 minidisc_label_maker.py
```

Generated PDFs and matching cut SVGs are saved in the root [`output/`](output/)
directory by default. It contains generated labels only. Reusable A4 layout
assets are kept in [`templates/`](templates/), and full macOS Shortcut and
command-line instructions are in
[`MINIDISC-LABEL-SHORTCUT.md`](MINIDISC-LABEL-SHORTCUT.md).
