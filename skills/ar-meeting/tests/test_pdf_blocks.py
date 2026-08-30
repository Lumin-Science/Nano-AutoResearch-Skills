#!/usr/bin/env python3

import sys
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))
import convert  # noqa: E402


BBOX_XML = """\
<html xmlns="http://www.w3.org/1999/xhtml"><body><doc>
  <page width="600" height="800">
    <flow><block xMin="150" yMin="50" xMax="450" yMax="70">
      <line xMin="150" yMin="50" xMax="450" yMax="70">
        <word xMin="150" yMin="50" xMax="220" yMax="70">A</word>
        <word xMin="230" yMin="50" xMax="320" yMax="70">Result</word>
      </line>
    </block></flow>
    <flow><block xMin="80" yMin="100" xMax="350" yMax="110">
      <line xMin="80" yMin="100" xMax="350" yMax="110">
        <word xMin="80" yMin="100" xMax="120" yMax="110">Plain</word>
        <word xMin="125" yMin="100" xMax="175" yMax="110">prose</word>
      </line>
    </block></flow>
    <flow><block xMin="200" yMin="200" xMax="270" yMax="212">
      <line xMin="200" yMin="200" xMax="270" yMax="212">
        <word xMin="200" yMin="200" xMax="210" yMax="212">E</word>
        <word xMin="218" yMin="200" xMax="230" yMax="212">=</word>
        <word xMin="238" yMin="200" xMax="270" yMax="212">mc²</word>
      </line>
    </block></flow>
    <flow><block xMin="500" yMin="200" xMax="520" yMax="212">
      <line xMin="500" yMin="200" xMax="520" yMax="212">
        <word xMin="500" yMin="200" xMax="520" yMax="212">(1)</word>
      </line>
    </block></flow>
    <flow><block xMin="100" yMin="300" xMax="150" yMax="310">
      <line xMin="100" yMin="300" xMax="150" yMax="310">
        <word xMin="100" yMin="300" xMax="150" yMax="310">Method</word>
      </line>
    </block></flow>
    <flow><block xMin="160" yMin="300" xMax="210" yMax="310">
      <line xMin="160" yMin="300" xMax="210" yMax="310">
        <word xMin="160" yMin="300" xMax="210" yMax="310">Score</word>
      </line>
    </block></flow>
    <flow><block xMin="100" yMin="318" xMax="150" yMax="338">
      <line xMin="100" yMin="318" xMax="150" yMax="328">
        <word xMin="100" yMin="318" xMax="150" yMax="328">Base</word>
      </line>
      <line xMin="100" yMin="328" xMax="150" yMax="338">
        <word xMin="100" yMin="328" xMax="150" yMax="338">Ours</word>
      </line>
    </block></flow>
    <flow><block xMin="160" yMin="318" xMax="210" yMax="338">
      <line xMin="160" yMin="318" xMax="210" yMax="328">
        <word xMin="160" yMin="318" xMax="210" yMax="328">0.7</word>
      </line>
      <line xMin="160" yMin="328" xMax="210" yMax="338">
        <word xMin="160" yMin="328" xMax="210" yMax="338">0.8</word>
      </line>
    </block></flow>
  </page>
</doc></body></html>
"""


class PdfBlockTests(unittest.TestCase):
    def setUp(self):
        pages = convert.parse_pdf_bbox_layout(BBOX_XML)
        self.assertEqual(len(pages), 1)
        self.page = pages[0]

    def test_groups_and_classifies_positioned_regions(self):
        regions = convert.pdf_layout_regions(self.page)
        by_kind = {}
        for region in regions:
            by_kind.setdefault(region["kind"], []).append(region)

        self.assertIn("heading", by_kind)
        self.assertIn("equation", by_kind)
        self.assertIn("table", by_kind)
        self.assertIn("text", by_kind)
        self.assertIn("(1)", by_kind["equation"][0]["text"])
        self.assertIn("Method", by_kind["table"][0]["text"])
        self.assertIn("0.8", by_kind["table"][0]["text"])

    def test_embedded_image_is_a_figure_module(self):
        image = {"x": 100, "y": 400, "w": 300, "h": 200,
                 "lines": [], "words": [], "text": "", "kind_hint": "figure"}
        regions = convert.pdf_layout_regions(self.page, [image])
        figures = [region for region in regions if region["kind"] == "figure"]
        self.assertEqual(len(figures), 1)
        self.assertEqual(figures[0]["w"], 300)

    def test_overlay_contains_modules_and_positioned_words(self):
        overlay, manifest = convert.pdf_region_overlays(1, self.page)
        self.assertIn('class="lm-overlay lm-module lm-pdf-block', overlay)
        self.assertIn('data-kind="equation"', overlay)
        self.assertIn('class="lm-pdf-word"', overlay)
        self.assertTrue(any(item["kind"] == "table" for item in manifest))

        slide = convert.build_page_slide(1, "slides/page-1.png", "page text", overlay,
                                         coarse_text=False, page_module=False,
                                         modules=manifest)
        self.assertIn('<div class="lm-page">', slide["body"])
        self.assertNotIn("lm-textlayer", slide["body"])
        self.assertEqual(slide["modules"], manifest)


if __name__ == "__main__":
    unittest.main()
