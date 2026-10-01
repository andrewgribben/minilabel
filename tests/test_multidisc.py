import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import minidisc_label_maker as labels


class MultiDiscLabelTests(unittest.TestCase):
    def test_csv_tracklist_parses_title_artist_numbers_and_quoted_comma(self):
        title, artist, tracks = labels.parse_csv_tracklist(
            "disc_title,artist,track_number,track_title\n"
            'Example Album,Example Artist,1,"Opening, Part One"\n'
            ",,2,Finale\n"
        )
        self.assertEqual(title, "Example Album")
        self.assertEqual(artist, "Example Artist")
        self.assertEqual(tracks, [("1.", "Opening, Part One"), ("2.", "Finale")])

    def test_csv_tracklist_generates_numbers_when_omitted(self):
        title, artist, tracks = labels.parse_csv_tracklist(
            "album,track\nNight Drive,City Lights\n,Last Train Home\n"
        )
        self.assertEqual(title, "Night Drive")
        self.assertIsNone(artist)
        self.assertEqual(tracks, [("1.", "City Lights"), ("2.", "Last Train Home")])

    def test_manual_input_detects_csv_filename(self):
        self.assertEqual(
            labels.manual_input_format("not inspected", Path("tracks.csv"), "auto"),
            "csv",
        )

    def test_fetch_cover_art_accepts_front_image_pending_approval(self):
        pending_front = {
            "approved": False,
            "front": True,
            "types": ["Front"],
            "image": "https://example.test/front.jpg",
            "thumbnails": {},
        }
        with (
            patch.object(labels, "request_json", return_value={"images": [pending_front]}),
            patch.object(
                labels,
                "download_image_data_uri",
                return_value=("data:image/jpeg;base64,example", (500, 500)),
            ),
        ):
            front, spine, source, dedicated_spine = labels.fetch_cover_art(
                "release-id", "release-group-id"
            )

        self.assertEqual(front, "data:image/jpeg;base64,example")
        self.assertEqual(spine, front)
        self.assertEqual(source, "https://example.test/front.jpg")
        self.assertFalse(dedicated_spine)

    def test_musicbrainz_markdown_link_selects_exact_release(self):
        reference = labels.musicbrainz_reference(
            "[Back to the Future - The Musical]"
            "(https://musicbrainz.org/release/"
            "b65a4257-0f80-4824-970a-5b23e69b3b9f)"
        )
        self.assertEqual(
            reference,
            ("release", "b65a4257-0f80-4824-970a-5b23e69b3b9f"),
        )

    def test_exact_hyphenated_album_title_is_not_split_as_artist_and_album(self):
        candidate = {
            "id": "release-group-id",
            "title": "Back to the Future - The Musical",
        }
        with patch.object(
            labels, "search_release_groups", return_value=[candidate]
        ) as search:
            result = labels.search_release_groups_from_text(
                "Back to the Future - The Musical", None
            )
        self.assertEqual(result, [candidate])
        search.assert_called_once_with("Back to the Future - The Musical", None)

    def test_release_summary_orders_media(self):
        release = labels.release_summary(
            {
                "id": "release-id",
                "title": "Example",
                "media": [
                    {"position": 2, "title": "Act II", "format": "CD"},
                    {"position": 1, "title": "Act I", "format": "CD"},
                ],
            }
        )
        self.assertEqual([medium["title"] for medium in release["media"]], ["Act I", "Act II"])

    def test_print_svg_contains_two_positioned_labels(self):
        svg = labels.create_print_svg(
            "Example Album", [], 2, disc_labels=["Act I", "Act II"]
        )
        ET.fromstring(svg)
        self.assertIn("EXAMPLE", svg.upper())
        self.assertIn("ACT I", svg)
        self.assertIn("ACT II", svg)
        self.assertIn('id="face-artwork-2"', svg)

    def test_cut_svg_contains_face_and_edge_for_each_disc(self):
        root = ET.fromstring(labels.create_cut_svg("Example Album", 2, 2))
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        self.assertEqual(len(root.findall(".//svg:rect", namespace)), 4)

    def test_ready_cut_svg_supports_sparse_positions_and_registration_marks(self):
        svg = labels.create_cut_svg_for_positions(
            "Ready to cut", [1, 3, 8], include_registration_marks=True
        )
        root = ET.fromstring(svg)
        namespace = {"svg": "http://www.w3.org/2000/svg"}
        self.assertEqual(len(root.findall(".//svg:rect", namespace)), 6)
        self.assertIn('id="registration-marks"', svg)
        self.assertNotIn("face-label-position-2", svg)

    def test_newest_pdf_wins_when_positions_overlap(self):
        with TemporaryDirectory() as temporary_dir:
            output = Path(temporary_dir)
            older_pdf = output / "older-position-01.pdf"
            newer_pdf = output / "newer-position-01.pdf"
            older_pdf.touch()
            newer_pdf.touch()
            older_pdf.with_name("older-position-01-cut.svg").write_text(
                labels.create_cut_svg("Older", 1), encoding="utf-8"
            )
            newer_pdf.with_name("newer-position-01-cut.svg").write_text(
                labels.create_cut_svg("Newer", 1), encoding="utf-8"
            )
            older_pdf.touch()
            newer_pdf.touch()
            older_pdf.chmod(0o600)
            newer_pdf.chmod(0o600)
            older_time = older_pdf.stat().st_mtime_ns - 2_000_000_000
            newer_time = newer_pdf.stat().st_mtime_ns
            os.utime(older_pdf, ns=(older_time, older_time))
            os.utime(newer_pdf, ns=(newer_time, newer_time))

            sources, positions = labels.collect_aggregate_sources(output)
            self.assertEqual(positions, [1])
            self.assertEqual(Path(sources[0]["path"]), newer_pdf)


if __name__ == "__main__":
    unittest.main()
