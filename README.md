# MiniDisc label maker

Creates one printable MiniDisc face label and one edge label at a selected
position on the reusable 12-position A4 layout.

The generator supports two workflows:

- **Manual:** provide a Markdown H1 disc title and ordered or unordered tracklist.
- **MusicBrainz:** search for an album and release edition, then use its Cover
  Art Archive image as the face label. Multi-disc editions automatically fill
  consecutive positions with numbered face and edge labels. Exact MusicBrainz
  release and release-group URLs can also be pasted into the search input.

Run the generator from the project root:

```sh
/usr/bin/env python3 minidisc_label_maker.py
```

Generated PDFs and matching cut SVGs are saved in the root [`output/`](output/)
directory by default. It contains generated labels only. Reusable A4 layout
assets are kept in [`templates/`](templates/), and full macOS Shortcut and
command-line instructions are in
[`MINIDISC-LABEL-SHORTCUT.md`](MINIDISC-LABEL-SHORTCUT.md).

After every successful run, the generator also rebuilds:

- `output/ready-to-print.pdf`, containing every occupied position represented
  by a PDF and matching cut SVG still in `output/`.
- `output/ready-to-cut.svg`, containing cut paths for those positions only.

Both ready files include matching corner registration marks. If multiple label
files occupy the same position, the most recently generated PDF is used.
