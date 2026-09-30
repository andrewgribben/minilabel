import unittest
import xml.etree.ElementTree as ET

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


if __name__ == "__main__":
    unittest.main()
