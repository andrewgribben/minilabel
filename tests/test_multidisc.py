import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

import minidisc_label_maker as labels


class MultiDiscLabelTests(unittest.TestCase):
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
