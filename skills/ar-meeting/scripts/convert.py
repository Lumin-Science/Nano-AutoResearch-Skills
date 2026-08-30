#!/usr/bin/env python3
"""ar-meeting converter — any presentation (pptx/pdf/md/html) -> machine-style HTML deck.

Usage: python3 convert.py <input> [--out <dir>] [--name <n>] [--mode native|image]

Output (default ~/.cache/ar-meeting/<input-basename>-<source-hash>/):
  index.html   the deck — renders standalone in a browser, no server needed
  theme.css    the active theme (config.json "theme"), copied alongside
  slides/      extracted images, or page PNGs in image mode

Conversion modes:
  native (default for pptx/md/html) — real HTML: selectable text, real modules,
      styleable by theme.css, no external tools, no pip packages.
  image  (only mode for pdf; opt-in for pptx) — one PNG per page with clickable
      overlays. Higher visual fidelity, but the text is a picture. Needs a
      renderer: PowerPoint or Keynote via osascript on macOS, else LibreOffice,
      plus poppler for rasterising.

Stdlib only.
"""
import glob
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS = os.path.join(SKILL_DIR, "assets")
USAGE = ("usage: convert.py <input.pptx|.pdf|.md|.html> [--out <dir>] [--name <n>] "
         "[--mode native|image]")
EMU_PER_PX = 9525
CACHE_ENV = "LABMEET_CACHE_DIR"
CACHE_NAMESPACE = "ar-meeting"


def emit(kind, msg, hint):
    print("%s: %s | help: %s" % (kind, msg, hint))


def die(msg, hint, code=1):
    emit("error", msg, hint)
    sys.exit(code)


def esc(s, quote=False):
    return html.escape(str(s), quote=quote)


def attr(s):
    """Escape for use inside a double-quoted HTML attribute."""
    return html.escape(str(s), quote=True)


def default_cache_root():
    """Return the user-local cache root for generated meeting artifacts."""
    override = os.environ.get(CACHE_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = os.path.expanduser(xdg) if xdg else os.path.expanduser("~/.cache")
    return os.path.abspath(os.path.join(base, CACHE_NAMESPACE))


def default_output_dir(src, name):
    """Keep default rooms outside projects and disambiguate equal basenames."""
    identity = os.path.realpath(os.path.abspath(src)).encode("utf-8")
    suffix = hashlib.sha256(identity).hexdigest()[:12]
    return os.path.join(default_cache_root(), "%s-%s" % (name, suffix))


def load_skill_config():
    cfg = {"margin_position": "right", "theme": "theme.css"}
    try:
        with open(os.path.join(SKILL_DIR, "config.json")) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)  # unknown keys are carried along and ignored downstream
    except Exception:
        pass
    return cfg


class ModuleCounter:
    """Per-slide module ids: s<slide>.<kind-prefix><n> (e.g. s3.f1)."""

    def __init__(self, slide_no):
        self.slide_no = slide_no
        self.counts = {}

    def next(self, prefix):
        self.counts[prefix] = self.counts.get(prefix, 0) + 1
        return "s%d.%s%d" % (self.slide_no, prefix, self.counts[prefix])


def module_div(mid, kind, label, inner, extra_class="", extra_attr=""):
    return ('<div class="lm-module%s" data-module="%s" data-kind="%s" data-label="%s" '
            'tabindex="0"%s>%s</div>'
            % (extra_class, mid, kind, attr(label), extra_attr, inner))


# ---------------------------------------------------------------- local assets

def copy_asset(src, out_dir, base_dir):
    """Copy a locally-referenced file next to the deck; return its new relative src.

    Remote URLs are refused (the deck must not phone home when opened) and
    destination names are content-hashed so same-named files from different
    directories cannot overwrite each other.
    """
    if re.match(r"^(https?|ftp|//)", src, re.I):
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", src) and not src.lower().startswith("file:"):
        return None
    if src.lower().startswith("file:"):
        src = re.sub(r"^file://", "", src)
    p = src if os.path.isabs(src) else os.path.normpath(os.path.join(base_dir, src))
    if not os.path.isfile(p):
        return None
    dest_dir = os.path.join(out_dir, "slides", "assets")
    os.makedirs(dest_dir, exist_ok=True)
    with open(p, "rb") as f:
        digest = hashlib.sha1(f.read()).hexdigest()[:10]
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(p)) or "asset"
    name = "%s-%s" % (digest, base)
    dest = os.path.join(dest_dir, name)
    if not os.path.exists(dest):
        shutil.copy2(p, dest)
    return "slides/assets/" + name


def remote_placeholder(url, alt=""):
    """An inert stand-in for a remote image: nothing loads until the user clicks."""
    label = alt or url
    return ('<span class="lm-remote">remote image not loaded: '
            '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a></span>'
            % (attr(url), esc(label[:80])))


# ---------------------------------------------------------------- markdown

def split_md_slides(text):
    """Split on h1/h2 headings and --- rules; fenced code is opaque."""
    lines = text.replace("\r\n", "\n").split("\n")
    chunks, cur, in_fence = [], [], False
    for ln in lines:
        if ln.strip().startswith("```"):
            in_fence = not in_fence
            cur.append(ln)
            continue
        if not in_fence and re.match(r"^---+\s*$", ln):
            chunks.append(cur)
            cur = []
            continue
        if not in_fence and re.match(r"^#{1,2}\s+", ln) and any(l.strip() for l in cur):
            chunks.append(cur)
            cur = [ln]
            continue
        cur.append(ln)
    chunks.append(cur)
    return [c for c in chunks if any(l.strip() for l in c)]


def md_inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(^|[\s(])\*([^*\s][^*]*?)\*", r"\1<em>\2</em>", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
               lambda m: '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                         % (attr(m.group(2)), m.group(1)), s)
    return s


def md_blocks(lines):
    """Yield (kind, payload) block tuples from markdown lines."""
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue
        if ln.strip().startswith("```"):
            lang = ln.strip()[3:].strip()
            j, buf = i + 1, []
            while j < n and not lines[j].strip().startswith("```"):
                buf.append(lines[j])
                j += 1
            yield ("code", ("\n".join(buf), lang))
            i = j + 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            yield ("heading", (len(m.group(1)), m.group(2)))
            i += 1
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", ln):
            j, buf = i, []
            while j < n and (re.match(r"^\s*([-*+]|\d+[.)])\s+", lines[j])
                             or (lines[j].startswith("  ") and lines[j].strip())):
                buf.append(lines[j])
                j += 1
            yield ("list", buf)
            i = j
            continue
        if ln.lstrip().startswith(">"):
            j, buf = i, []
            while j < n and lines[j].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[j]))
                j += 1
            yield ("quote", buf)
            i = j
            continue
        m = re.match(r"^\s*!\[([^\]]*)\]\(([^)]+)\)\s*$", ln)
        if m:
            yield ("image", (m.group(1), m.group(2)))
            i += 1
            continue
        j, buf = i, []
        while j < n and lines[j].strip() and not re.match(
                r"^(#{1,6}\s+|```|\s*([-*+]|\d+[.)])\s+|\s*>)", lines[j]):
            buf.append(lines[j].strip())
            j += 1
        yield ("para", " ".join(buf))
        i = j


def render_md_chunk(lines, slide_no, out_dir, base_dir):
    mc = ModuleCounter(slide_no)
    parts = []
    topic = None
    for kind, payload in md_blocks(lines):
        if kind == "heading":
            lvl, txt = payload
            if lvl <= 2 and topic is None:
                topic = txt.strip()
                parts.append('<h1 class="lm-slide-title">%s</h1>' % md_inline(txt))
            else:
                mid = mc.next("t")
                lvl = max(lvl, 2)
                parts.append(module_div(mid, "text", txt.strip()[:60],
                                        "<h%d>%s</h%d>" % (lvl, md_inline(txt), lvl)))
        elif kind == "code":
            code, lang = payload
            mid = mc.next("c")
            inner = '<pre><code class="language-%s">%s</code></pre>' % (attr(lang), esc(code))
            parts.append(module_div(mid, "code", "code block %d" % mc.counts["c"], inner))
        elif kind == "list":
            buf = payload
            mid = mc.next("l")
            ordered = bool(re.match(r"^\s*\d+[.)]\s+", buf[0]))
            items = []
            for b in buf:
                m = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", b)
                if m:
                    items.append("<li>%s</li>" % md_inline(m.group(2)))
                elif items:
                    items[-1] = items[-1][:-5] + " " + md_inline(b.strip()) + "</li>"
            tag = "ol" if ordered else "ul"
            parts.append(module_div(mid, "list", "list %d" % mc.counts["l"],
                                    "<%s>%s</%s>" % (tag, "".join(items), tag)))
        elif kind == "quote":
            mid = mc.next("t")
            parts.append(module_div(mid, "text", "quote",
                                    "<blockquote>%s</blockquote>" % md_inline(" ".join(payload))))
        elif kind == "image":
            alt, src = payload
            mid = mc.next("f")
            local = copy_asset(src, out_dir, base_dir)
            cap = "<figcaption>%s</figcaption>" % md_inline(alt) if alt else ""
            if local:
                inner = ('<figure><img src="%s" alt="%s">%s</figure>'
                         % (attr(local), attr(alt), cap))
            else:
                inner = "<figure>%s%s</figure>" % (remote_placeholder(src, alt), cap)
            label = ("figure: " + alt) if alt else ("figure %d" % mc.counts["f"])
            parts.append(module_div(mid, "figure", label, inner))
        elif kind == "para":
            mid = mc.next("t")
            parts.append(module_div(mid, "text", "paragraph %d" % mc.counts["t"],
                                    "<p>%s</p>" % md_inline(payload)))
    return topic, "\n".join(parts)


def convert_md(path, out_dir, mode=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    base_dir = os.path.dirname(os.path.abspath(path))
    slides = []
    for i, chunk in enumerate(split_md_slides(text), 1):
        topic, body = render_md_chunk(chunk, i, out_dir, base_dir)
        slides.append({"n": i, "topic": topic or "Slide %d" % i, "body": body})
    return slides


# ---------------------------------------------------------------- pptx (native)

def _para_html(para, stage_w):
    """One <p>, with run formatting and sizes in cqw so text scales with the stage."""
    if not para["runs"]:
        return '<p class="lm-p">&nbsp;</p>'
    bits = []
    for run in para["runs"]:
        if run.get("br"):
            bits.append("<br>")
            continue
        style = []
        if run.get("sz"):
            px = run["sz"] / 100.0 * 96.0 / 72.0
            style.append("font-size:%.3fcqw" % (px / stage_w * 100.0))
        if run.get("b"):
            style.append("font-weight:700")
        if run.get("i"):
            style.append("font-style:italic")
        if run.get("u"):
            style.append("text-decoration:underline")
        if run.get("color"):
            style.append("color:%s" % run["color"])
        if run.get("font"):
            style.append("font-family:'%s',var(--lm-font)" % run["font"].replace("'", ""))
        text = esc(run["text"]).replace("\n", "<br>")
        bits.append('<span style="%s">%s</span>' % (";".join(style), text) if style else text)
    cls = "lm-p lm-bullet" if para["bullet"] else "lm-p"
    style = []
    if para["lvl"]:
        style.append("margin-left:%.2fem" % (para["lvl"] * 1.4))
    align = {"ctr": "center", "r": "right", "just": "justify", "l": "left"}.get(para["align"])
    if align:
        style.append("text-align:%s" % align)
    return '<p class="%s"%s>%s</p>' % (
        cls, ' style="%s"' % ";".join(style) if style else "", "".join(bits))


def _shape_style(box, w, h):
    s = ["left:%.3f%%" % (box["x"] / w * 100.0), "top:%.3f%%" % (box["y"] / h * 100.0),
         "width:%.3f%%" % (box["w"] / w * 100.0), "height:%.3f%%" % (box["h"] / h * 100.0)]
    tr = []
    if box.get("rot"):
        tr.append("rotate(%.2fdeg)" % box["rot"])
    if box.get("flipH"):
        tr.append("scaleX(-1)")
    if box.get("flipV"):
        tr.append("scaleY(-1)")
    if tr:
        s.append("transform:%s" % " ".join(tr))
    return ";".join(s)


def _label_for(shape, text, idx):
    ph = shape.get("ph")
    kind = shape["kind"]
    snippet = " ".join(text.split())[:48] if text else ""
    if kind == "picture":
        return ("figure: " + shape["alt"]) if shape.get("alt") else "figure %d" % idx
    if kind == "table":
        return "table: " + snippet if snippet else "table %d" % idx
    if kind == "chart":
        return "chart: " + (shape.get("name") or "chart %d" % idx)
    if ph and ph[0] in ("title", "ctrTitle"):
        return "title: " + snippet
    if snippet:
        return snippet
    return shape.get("name") or "%s %d" % (kind, idx)


def convert_pptx_native(path, out_dir):
    sys.path.insert(0, SCRIPT_DIR)
    import pptx_reader

    try:
        deck = pptx_reader.read(path)
    except ValueError as e:
        die(str(e), "check the file really is a .pptx (not .ppt or a renamed file)")
    w, h = float(deck["w"]), float(deck["h"])
    stage_w = w / EMU_PER_PX

    media_map = {}
    if deck["media"]:
        dest_dir = os.path.join(out_dir, "slides", "assets")
        os.makedirs(dest_dir, exist_ok=True)
        for part, blob in deck["media"].items():
            digest = hashlib.sha1(blob).hexdigest()[:10]
            base = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(part))
            name = "%s-%s" % (digest, base)
            with open(os.path.join(dest_dir, name), "wb") as f:
                f.write(blob)
            media_map[part] = "slides/assets/" + name

    slides = []
    for i, sl in enumerate(deck["slides"], 1):
        mc = ModuleCounter(i)
        parts = []
        for shape in sl["shapes"]:
            box = shape["box"]
            if box["w"] <= 0 or box["h"] <= 0:
                continue
            style = _shape_style(box, w, h)
            kind = shape["kind"]

            if kind == "line":
                parts.append('<div class="lm-shape lm-line" style="%s;border-top:1px solid %s">'
                             "</div>" % (style, shape.get("color") or "#888"))
                continue

            if kind == "picture":
                src = media_map.get(shape.get("media"))
                idx = mc.counts.get("f", 0) + 1
                label = _label_for(shape, "", idx)
                if src and pptx_reader.is_web_image(shape["media"]):
                    inner = '<img src="%s" alt="%s">' % (attr(src), attr(shape.get("alt") or ""))
                else:
                    fmt = os.path.splitext(shape.get("media") or "")[1].lstrip(".") or "unknown"
                    inner = ('<span class="lm-unsupported">%s image (not web-renderable)</span>'
                             % esc(fmt))
                parts.append(module_div(mc.next("f"), "figure", label, inner,
                                        extra_class=" lm-shape lm-pic",
                                        extra_attr=' style="%s"' % style))
                continue

            if kind == "table":
                rows = []
                for tr in shape["table"]["rows"]:
                    cells = []
                    for tc in tr:
                        txt = "".join(_para_html(p, stage_w)
                                      for p in (tc["text"]["paragraphs"] if tc["text"] else []))
                        bg = ' style="background:%s"' % tc["fill"] if tc["fill"] else ""
                        cells.append("<td%s>%s</td>" % (bg, txt))
                    rows.append("<tr>%s</tr>" % "".join(cells))
                flat = pptx_reader.plain_text(
                    shape["table"]["rows"][0][0]["text"]) if shape["table"]["rows"] else ""
                idx = mc.counts.get("l", 0) + 1
                parts.append(module_div(mc.next("l"), "list", _label_for(shape, flat, idx),
                                        "<table>%s</table>" % "".join(rows),
                                        extra_class=" lm-shape lm-table",
                                        extra_attr=' style="%s"' % style))
                continue

            if kind in ("chart", "object"):
                idx = mc.counts.get("f", 0) + 1
                parts.append(module_div(
                    mc.next("f"), "figure", _label_for(shape, "", idx),
                    '<span class="lm-unsupported">%s (embedded object — ask about it in chat)'
                    "</span>" % esc(shape.get("name") or kind),
                    extra_class=" lm-shape lm-object", extra_attr=' style="%s"' % style))
                continue

            text = shape.get("text")
            flat = pptx_reader.plain_text(text)
            box_style = style
            if shape.get("fill"):
                box_style += ";background:%s" % shape["fill"]
            if shape.get("line"):
                box_style += ";border:1px solid %s" % shape["line"]
            if not text:
                parts.append('<div class="lm-shape lm-block" style="%s"></div>' % box_style)
                continue
            anchor = {"ctr": "center", "b": "flex-end"}.get(text["anchor"], "flex-start")
            box_style += ";justify-content:%s" % anchor
            inner = "".join(_para_html(p, stage_w) for p in text["paragraphs"])
            ph = shape.get("ph")
            prefix = "t" if not (ph and ph[0] in ("title", "ctrTitle")) else "h"
            idx = mc.counts.get(prefix, 0) + 1
            parts.append(module_div(mc.next(prefix), "text", _label_for(shape, flat, idx),
                                    inner, extra_class=" lm-shape lm-text",
                                    extra_attr=' style="%s"' % box_style))

        canvas_style = "aspect-ratio:%.4f" % (w / h)
        if sl["bg"]:
            canvas_style += ";background:%s" % sl["bg"]
        body = ['<div class="lm-canvas" style="%s">%s</div>' % (canvas_style, "\n".join(parts))]
        if sl["notes"]:
            body.append('<details class="lm-notes lm-module" data-module="%s" data-kind="notes" '
                        'data-label="speaker notes"><summary>Speaker notes</summary>'
                        '<div class="lm-notes-body">%s</div></details>'
                        % (mc.next("n"), esc(sl["notes"])))
        flat_bits = [pptx_reader.plain_text(sh.get("text"))
                     for sh in sl["shapes"] if sh.get("text")]
        if sl["notes"]:
            flat_bits.append("Speaker notes: " + sl["notes"])
        slides.append({"n": i, "topic": sl["topic"] or "Slide %d" % i,
                       "body": "\n".join(body),
                       "text": "\n".join(b for b in flat_bits if b.strip())})
    return slides


# ---------------------------------------------------------------- rendering (image mode)

def rasterize(pdf, out_dir, dpi=None):
    dpi = int(dpi or load_skill_config().get("render_dpi", 200))
    if not shutil.which("pdftoppm"):
        die("pdftoppm not found — needed to render pages as images",
            "install poppler: brew install poppler (mac) / apt install poppler-utils (linux)"
            "; or use --mode native for pptx")
    sdir = os.path.join(out_dir, "slides")
    os.makedirs(sdir, exist_ok=True)
    for old in glob.glob(os.path.join(sdir, "page-*.png")):
        os.remove(old)
    r = subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf, os.path.join(sdir, "page")],
                       capture_output=True, text=True)
    if r.returncode != 0:
        tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unknown error"
        die("pdftoppm failed: %s" % tail, "check the pdf opens in a normal viewer")
    pngs = sorted(glob.glob(os.path.join(sdir, "page-*.png")),
                  key=lambda p: int(re.search(r"page-(\d+)", p).group(1)))
    if not pngs:
        die("no pages rendered from pdf", "check the pdf is not empty")
    return [os.path.relpath(p, out_dir) for p in pngs]


def _median(values, default=0.0):
    values = sorted(float(v) for v in values if float(v) > 0)
    if not values:
        return float(default)
    mid = len(values) // 2
    if len(values) % 2:
        return values[mid]
    return (values[mid - 1] + values[mid]) / 2.0


def _local_tag(node):
    return str(node.tag).rsplit("}", 1)[-1]


def _box_from_xml(node):
    try:
        x1 = float(node.attrib["xMin"])
        y1 = float(node.attrib["yMin"])
        x2 = float(node.attrib["xMax"])
        y2 = float(node.attrib["yMax"])
    except (KeyError, TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def parse_pdf_bbox_layout(blob):
    """Parse Poppler's XHTML bbox output into page/block/line/word geometry.

    Kept separate from the subprocess wrapper so segmentation can be exercised
    with small deterministic fixtures and no PDF-generation dependency.
    """
    try:
        root = ET.fromstring(blob)
    except (ET.ParseError, TypeError, ValueError):
        return []
    pages = []
    for page_node in (node for node in root.iter() if _local_tag(node) == "page"):
        try:
            page_w = float(page_node.attrib["width"])
            page_h = float(page_node.attrib["height"])
        except (KeyError, TypeError, ValueError):
            continue
        blocks = []
        for block_node in (node for node in page_node.iter()
                           if _local_tag(node) == "block"):
            box = _box_from_xml(block_node)
            if not box:
                continue
            lines, words = [], []
            for line_node in (node for node in block_node
                              if _local_tag(node) == "line"):
                line_box = _box_from_xml(line_node)
                line_words = []
                for word_node in (node for node in line_node
                                  if _local_tag(node) == "word"):
                    word_box = _box_from_xml(word_node)
                    word_text = "".join(word_node.itertext()).strip()
                    if not word_box or not word_text:
                        continue
                    word_box["text"] = word_text
                    line_words.append(word_box)
                    words.append(word_box)
                if line_box and line_words:
                    line_box["words"] = line_words
                    line_box["text"] = " ".join(word["text"] for word in line_words)
                    lines.append(line_box)
            if not words:
                continue
            box.update({"lines": lines, "words": words,
                        "text": "\n".join(line["text"] for line in lines),
                        "kind_hint": None})
            blocks.append(box)
        pages.append({"width": page_w, "height": page_h, "blocks": blocks})
    return pages


def extract_pdf_bbox_layout(pdf, npages):
    if not shutil.which("pdftotext"):
        return [None] * npages
    r = subprocess.run(["pdftotext", "-bbox-layout", pdf, "-"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        emit("warn", "PDF block extraction failed — using a page-wide text layer",
             "upgrade poppler or check the PDF text layer")
        return [None] * npages
    parsed = parse_pdf_bbox_layout(r.stdout)
    if not parsed:
        emit("warn", "PDF has no positioned text blocks",
             "image-only pages remain clickable but need OCR for text blocks")
        return [None] * npages
    return (parsed + [None] * npages)[:npages]


def extract_pdf_image_boxes(pdf, pages):
    """Return embedded raster-image boxes in the coordinate space of bbox pages.

    Vector plots have no image object in a PDF and are therefore covered by the
    page surface (and often by their positioned labels/caption), but ordinary
    embedded figures receive a first-class clickable region here.
    """
    result = [[] for _ in pages]
    tool = shutil.which("pdftohtml")
    if not tool or not pages:
        return result
    try:
        with tempfile.TemporaryDirectory(prefix="ar-meeting-pdf-layout-") as tmp:
            stem = os.path.join(tmp, "layout")
            r = subprocess.run([tool, "-xml", "-hidden", "-nodrm", "-q", pdf, stem],
                               capture_output=True, text=True, timeout=120)
            xml_path = stem + ".xml"
            if r.returncode != 0 or not os.path.isfile(xml_path):
                return result
            root = ET.parse(xml_path).getroot()
            html_pages = [node for node in root.iter() if _local_tag(node) == "page"]
            for idx, node in enumerate(html_pages[:len(pages)]):
                page = pages[idx]
                if not page:
                    continue
                try:
                    src_w = float(node.attrib["width"])
                    src_h = float(node.attrib["height"])
                except (KeyError, TypeError, ValueError):
                    continue
                if src_w <= 0 or src_h <= 0:
                    continue
                for image_node in (child for child in node
                                   if _local_tag(child) == "image"):
                    try:
                        box = {
                            "x": float(image_node.attrib["left"]) / src_w * page["width"],
                            "y": float(image_node.attrib["top"]) / src_h * page["height"],
                            "w": float(image_node.attrib["width"]) / src_w * page["width"],
                            "h": float(image_node.attrib["height"]) / src_h * page["height"],
                        }
                    except (KeyError, TypeError, ValueError):
                        continue
                    if box["w"] <= 0 or box["h"] <= 0:
                        continue
                    box.update({"lines": [], "words": [], "text": "",
                                "kind_hint": "figure"})
                    result[idx].append(box)
    except (OSError, ET.ParseError, subprocess.SubprocessError):
        return result
    return result


def _edge_gap(a1, a2, b1, b2):
    if a2 < b1:
        return b1 - a2
    if b2 < a1:
        return a1 - b2
    return 0.0


def _overlap(a1, a2, b1, b2):
    return max(0.0, min(a2, b2) - max(a1, b1))


def _equationish(text):
    text = " ".join((text or "").split())
    if not text or len(text) > 500:
        return False
    strong = bool(re.search(r"[=∑∏∫√≤≥≈≠±×÷∞∂∇λθβ⊤]", text))
    symbolic = len(re.findall(r"[^\w\s.,;:'\"!?()-]", text, flags=re.UNICODE))
    words = len(text.split())
    return strong and (words <= 45 or symbolic >= max(2, words // 3))


def _equation_number(text):
    return bool(re.match(r"^\(\s*\d+[A-Za-z]?\s*\)$", (text or "").strip()))


def _should_merge_pdf_blocks(a, b, page_w, median_h):
    if a.get("kind_hint") or b.get("kind_hint"):
        return False
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    xgap = _edge_gap(a["x"], ax2, b["x"], bx2)
    ygap = _edge_gap(a["y"], ay2, b["y"], by2)
    xov = _overlap(a["x"], ax2, b["x"], bx2)
    yov = _overlap(a["y"], ay2, b["y"], by2)

    same_band = yov >= min(a["h"], b["h"]) * 0.30
    if same_band and xgap <= max(page_w * 0.035, median_h * 2.5):
        return True

    # Consecutive fragments in display math and columns/rows in compact tables.
    aligned = (abs(a["x"] - b["x"]) <= median_h * 1.6 or
               abs(ax2 - bx2) <= median_h * 1.6)
    x_overlap_ratio = xov / max(1.0, min(a["w"], b["w"]))
    if ygap <= median_h * 1.35 and (x_overlap_ratio >= 0.22 or aligned):
        return True

    # TeX equation numbers are often far to the right of the equation body.
    if ((_equation_number(a.get("text")) and _equationish(b.get("text"))) or
            (_equation_number(b.get("text")) and _equationish(a.get("text")))):
        acy, bcy = a["y"] + a["h"] / 2.0, b["y"] + b["h"] / 2.0
        if abs(acy - bcy) <= median_h * 1.5 and xgap <= page_w * 0.40:
            return True
    return False


def _ordered_region_text(blocks):
    lines = [line for block in blocks for line in block.get("lines", [])]
    if not lines:
        return ""
    median_h = _median((line["h"] for line in lines), 10.0)
    rows = []
    for line in sorted(lines, key=lambda item: (item["y"] + item["h"] / 2.0,
                                                item["x"])):
        cy = line["y"] + line["h"] / 2.0
        if rows and abs(cy - rows[-1][0]) <= median_h * 0.45:
            rows[-1][1].append(line)
            count = len(rows[-1][1])
            rows[-1][0] = ((rows[-1][0] * (count - 1)) + cy) / count
        else:
            rows.append([cy, [line]])
    out = []
    for _, row in rows:
        words = [word for line in sorted(row, key=lambda item: item["x"])
                 for word in line.get("words", [])]
        if words:
            out.append(" ".join(word["text"] for word in words))
    return "\n".join(out)


def _cluster_count(values, tolerance):
    values = sorted(float(value) for value in values)
    if not values:
        return 0
    groups = 1
    anchor = values[0]
    for value in values[1:]:
        if value - anchor > tolerance:
            groups += 1
            anchor = value
    return groups


def _tableish_region(region, median_h):
    blocks = [block for block in region["blocks"] if block.get("words")]
    lines = [line for block in blocks for line in block.get("lines", [])]
    if len(blocks) < 3 or len(lines) < 3:
        return False
    columns = _cluster_count((block["x"] for block in blocks), median_h * 1.8)
    rows = _cluster_count((line["y"] + line["h"] / 2.0 for line in lines),
                          median_h * 0.75)
    return columns >= 2 and rows >= 2


def _classify_pdf_region(region, page_median_h):
    text = " ".join(region.get("text", "").split())
    hint = next((block.get("kind_hint") for block in region["blocks"]
                 if block.get("kind_hint")), None)
    if hint:
        return hint
    if re.match(r"^(?:fig(?:ure)?\.?)[\s ]*\d*\s*[:.]", text, re.I):
        return "figure"
    if re.match(r"^table[\s ]*\d*\s*[:.]", text, re.I):
        return "table"
    if _equationish(text):
        return "equation"
    if _tableish_region(region, page_median_h):
        return "table"
    word_count = len(text.split())
    line_heights = [line["h"] for block in region["blocks"]
                    for line in block.get("lines", [])]
    region_font_h = _median(line_heights, page_median_h)
    numbered_heading = bool(re.match(r"^\d+(?:\.\d+)*\s+\D", text))
    if word_count <= 20 and (region_font_h >= page_median_h * 1.18 or numbered_heading):
        return "heading"
    return "text"


def pdf_layout_regions(page, image_boxes=None):
    """Group low-level PDF text boxes into useful clickable page modules."""
    if not page:
        return []
    blocks = list(page.get("blocks") or []) + list(image_boxes or [])
    text_line_heights = [line["h"] for block in blocks
                         for line in block.get("lines", [])]
    median_h = _median(text_line_heights, max(8.0, page["height"] / 90.0))
    parent = list(range(len(blocks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        a, b = find(i), find(j)
        if a != b:
            parent[b] = a

    for i, first in enumerate(blocks):
        for j in range(i + 1, len(blocks)):
            if _should_merge_pdf_blocks(first, blocks[j], page["width"], median_h):
                union(i, j)

    grouped = {}
    for idx, block in enumerate(blocks):
        grouped.setdefault(find(idx), []).append(block)
    regions = []
    for members in grouped.values():
        x1 = min(block["x"] for block in members)
        y1 = min(block["y"] for block in members)
        x2 = max(block["x"] + block["w"] for block in members)
        y2 = max(block["y"] + block["h"] for block in members)
        region = {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                  "blocks": members, "words": [word for block in members
                                                  for word in block.get("words", [])]}
        region["text"] = _ordered_region_text(members)
        region["kind"] = _classify_pdf_region(region, median_h)
        regions.append(region)
    return sorted(regions, key=lambda item: (item["y"], item["x"]))


def _pdf_region_style(region, page_w, page_h):
    pad_x, pad_y = page_w * 0.002, page_h * 0.0015
    x = max(0.0, region["x"] - pad_x)
    y = max(0.0, region["y"] - pad_y)
    x2 = min(page_w, region["x"] + region["w"] + pad_x)
    y2 = min(page_h, region["y"] + region["h"] + pad_y)
    return ("left:%.3f%%;top:%.3f%%;width:%.3f%%;height:%.3f%%" %
            (x / page_w * 100.0, y / page_h * 100.0,
             (x2 - x) / page_w * 100.0, (y2 - y) / page_h * 100.0)), (x, y, x2, y2)


def _pdf_region_words(region, padded_box, page_w):
    x1, y1, x2, y2 = padded_box
    width, height = max(1.0, x2 - x1), max(1.0, y2 - y1)
    spans = []
    for word in sorted(region.get("words", []), key=lambda item: (item["y"], item["x"])):
        style = ("left:%.3f%%;top:%.3f%%;width:%.3f%%;height:%.3f%%;font-size:%.3fcqw" %
                 ((word["x"] - x1) / width * 100.0,
                  (word["y"] - y1) / height * 100.0,
                  word["w"] / width * 100.0, word["h"] / height * 100.0,
                  word["h"] / page_w * 100.0))
        spans.append('<span class="lm-pdf-word" style="%s">%s</span>' %
                     (style, esc(word["text"])))
    if not spans:
        return ""
    return '<span class="lm-pdf-block-text">%s</span>' % "".join(spans)


def pdf_region_overlays(page_no, page, image_boxes=None):
    regions = pdf_layout_regions(page, image_boxes)
    if not regions:
        return "", []
    mc = ModuleCounter(page_no)
    prefix = {"heading": "h", "equation": "e", "table": "l",
              "figure": "f", "text": "t"}
    parts, manifest = [], []
    for region in regions:
        kind = region["kind"]
        module_id = mc.next(prefix.get(kind, "t"))
        module_text = " ".join(region.get("text", "").split())[:500]
        snippet = module_text[:96]
        label = "%s: %s" % (kind, snippet) if snippet else kind
        style, padded_box = _pdf_region_style(region, page["width"], page["height"])
        inner = _pdf_region_words(region, padded_box, page["width"])
        parts.append('<div class="lm-overlay lm-module lm-pdf-block lm-pdf-%s" '
                     'data-module="%s" data-kind="%s" data-label="%s" '
                     'data-text="%s" aria-label="%s" title="%s" tabindex="0" '
                     'style="%s">%s</div>' %
                     (kind, module_id, kind, attr(label), attr(module_text),
                      attr(label), attr(label), style, inner))
        manifest.append({"id": module_id, "kind": kind, "label": label,
                         "text": region.get("text", ""),
                         "x": region["x"] / page["width"] * 100.0,
                         "y": region["y"] / page["height"] * 100.0,
                         "w": region["w"] / page["width"] * 100.0,
                         "h": region["h"] / page["height"] * 100.0})
    return "".join(parts), manifest


def extract_pdf_text(pdf, npages):
    if not shutil.which("pdftotext"):
        emit("warn", "pdftotext not found — selectable text layer skipped",
             "install poppler to enable text selection on pages")
        return [""] * npages
    texts = []
    for i in range(1, npages + 1):
        r = subprocess.run(["pdftotext", "-layout", "-f", str(i), "-l", str(i), pdf, "-"],
                           capture_output=True, text=True)
        texts.append(r.stdout if r.returncode == 0 else "")
    return texts


def build_page_slide(idx, png_rel, text="", overlays="", notes=None, topic=None,
                     coarse_text=True, page_module=True, modules=None):
    mc = ModuleCounter(idx)
    if not topic:
        first = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
        topic = first[:48] if first else "Page %d" % idx
    if page_module:
        pid = mc.next("p")
        opening = ('<div class="lm-page lm-module" data-module="%s" '
                   'data-kind="image-region" data-label="page %d" tabindex="0">' %
                   (pid, idx))
    else:
        opening = '<div class="lm-page">'
    parts = [opening]
    parts.append('<img class="lm-page-img" src="%s" alt="page %d" draggable="false">'
                 % (attr(png_rel), idx))
    if coarse_text and text.strip():
        parts.append('<div class="lm-textlayer">%s</div>' % esc(text))
    if overlays:
        parts.append(overlays)
    parts.append("</div>")
    if notes:
        nid = mc.next("n")
        parts.append('<details class="lm-notes lm-module" data-module="%s" data-kind="notes" '
                     'data-label="speaker notes"><summary>Speaker notes</summary>'
                     '<div class="lm-notes-body">%s</div></details>' % (nid, esc(notes)))
    return {"n": idx, "topic": topic, "body": "\n".join(parts),
            "text": ((text or "") + ("\n\nSpeaker notes: " + notes if notes else "")).strip(),
            "modules": list(modules or [])}


def convert_pdf(path, out_dir, mode=None):
    pdf = os.path.abspath(path)
    pngs = rasterize(pdf, out_dir)
    texts = extract_pdf_text(pdf, len(pngs))
    layouts = extract_pdf_bbox_layout(pdf, len(pngs))
    image_boxes = extract_pdf_image_boxes(pdf, layouts)
    slides = []
    for i, png in enumerate(pngs, 1):
        page = layouts[i - 1]
        overlays, modules = pdf_region_overlays(
            i, page, image_boxes[i - 1]) if page else ("", [])
        slides.append(build_page_slide(i, png, texts[i - 1], overlays,
                                       coarse_text=not bool(overlays),
                                       page_module=not bool(overlays), modules=modules))
    return slides


def find_soffice():
    """LibreOffice is the mature, headless pptx renderer — it is what server-side
    pipelines use. PowerPoint's macOS AppleScript dictionary exposes PDF export only
    for individual shapes, not whole presentations, and Keynote cannot be driven
    headlessly, so GUI automation is deliberately not attempted."""
    for c in ("soffice", "libreoffice"):
        p = shutil.which(c)
        if p:
            return p
    mac = "/Applications/LibreOffice.app/Contents/MacOS/soffice"
    return mac if os.path.exists(mac) else None


def render_pptx_to_pdf(src, tmp):
    soffice = find_soffice()
    if not soffice:
        return None, None
    try:
        r = subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp,
                            os.path.abspath(src)], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        emit("warn", "LibreOffice timed out rendering the deck", "retry, or use --mode native")
        return None, None
    pdfs = glob.glob(os.path.join(tmp, "*.pdf"))
    if not pdfs:
        tail = ((r.stderr or r.stdout or "").strip().splitlines() or ["no output"])[-1]
        emit("warn", "LibreOffice could not render the deck: %s" % tail[:120],
             "falling back to --mode native")
        return None, None
    return pdfs[0], "LibreOffice"


def convert_pptx_image(path, out_dir):
    with tempfile.TemporaryDirectory() as tmp:
        pdf, renderer = render_pptx_to_pdf(path, tmp)
        if not pdf:
            return None                      # caller decides: fall back or fail
        emit("ok", "rendered slides with %s" % renderer, "rasterising pages next")
        pngs = rasterize(pdf, out_dir)
        texts = extract_pdf_text(pdf, len(pngs))
    sys.path.insert(0, SCRIPT_DIR)
    import pptx_reader
    try:
        deck = pptx_reader.read(path)
    except ValueError:
        deck = None
    slides = []
    for i, png in enumerate(pngs, 1):
        overlays, notes, topic = "", None, None
        if deck and i <= len(deck["slides"]):
            sl = deck["slides"][i - 1]
            notes, topic = sl["notes"], sl["topic"]
            mc = ModuleCounter(i)
            w, h = float(deck["w"]), float(deck["h"])
            bits = []
            for shape in sl["shapes"]:
                box = shape["box"]
                if box["w"] <= 0 or box["h"] <= 0:
                    continue
                text = pptx_reader.plain_text(shape.get("text"))
                prefix = {"picture": "f", "table": "l", "chart": "f"}.get(shape["kind"], "t")
                kind = {"picture": "figure", "table": "list", "chart": "figure"}.get(
                    shape["kind"], "text")
                idx = mc.counts.get(prefix, 0) + 1
                label = _label_for(shape, text, idx)
                bits.append('<div class="lm-overlay lm-module" data-module="%s" data-kind="%s" '
                            'data-label="%s" title="%s" tabindex="0" style="%s"></div>'
                            % (mc.next(prefix), kind, attr(label), attr(label),
                               _shape_style(box, w, h)))
            overlays = "".join(bits)
        slides.append(build_page_slide(i, png, texts[i - 1], overlays, notes, topic))
    return slides


def convert_pptx(path, out_dir, mode=None):
    """Default to rendering with LibreOffice — it reproduces the deck exactly,
    charts and all. Native is the no-dependency fallback."""
    if mode == "native":
        return convert_pptx_native(path, out_dir)
    if find_soffice() and shutil.which("pdftoppm"):
        slides = convert_pptx_image(path, out_dir)
        if slides:
            return slides
        if mode == "image":
            die("LibreOffice could not render this deck",
                "open it in PowerPoint and re-save, or use --mode native")
        emit("warn", "LibreOffice could not render this deck — using the built-in renderer",
             "re-save the file from PowerPoint if the layout looks wrong")
        return convert_pptx_native(path, out_dir)
    if mode == "image":
        die("--mode image needs LibreOffice and poppler",
            "brew install --cask libreoffice && brew install poppler, or use --mode native")
    emit("warn", "LibreOffice/poppler not found — using the built-in renderer, "
         "which approximates the layout",
         "brew install --cask libreoffice for an exact copy of the deck")
    return convert_pptx_native(path, out_dir)


# ---------------------------------------------------------------- html

BLOCK_RE = re.compile(
    r"(<pre\b.*?</pre>|<figure\b.*?</figure>|<table\b.*?</table>"
    r"|<blockquote\b.*?</blockquote>|<ul\b.*?</ul>|<ol\b.*?</ol>"
    r"|<p\b.*?</p>|<img\b[^>]*>)", re.S | re.I)
HTML_KINDS = {"pre": ("code", "c"), "figure": ("figure", "f"), "img": ("figure", "f"),
              "table": ("list", "l"), "ul": ("list", "l"), "ol": ("list", "l"),
              "p": ("text", "t"), "blockquote": ("text", "t")}
SRC_RE = re.compile(r'(<(?:img|source|video|audio)\b[^>]*?\s(?:src|poster)=)(["\'])(.*?)\2',
                    re.I | re.S)


def localize_html_assets(chunk, out_dir, base_dir):
    """Copy relative assets next to the deck and rewrite their references."""
    def repl(m):
        head, q, src = m.group(1), m.group(2), m.group(3)
        local = copy_asset(html.unescape(src), out_dir, base_dir)
        if local:
            return "%s%s%s%s" % (head, q, attr(local), q)
        if re.match(r"^(https?:)?//", src, re.I):
            # drop the src entirely so opening the deck never contacts the remote host
            return 'data-lm-remote="%s"' % attr(src)
        return m.group(0)

    return SRC_RE.sub(repl, chunk)


def wrap_html_blocks(chunk, slide_no):
    mc = ModuleCounter(slide_no)

    def repl(m):
        blk = m.group(0)
        tag = re.match(r"<(\w+)", blk).group(1).lower()
        kind, prefix = HTML_KINDS[tag]
        mid = mc.next(prefix)
        text = re.sub(r"<[^>]+>", " ", blk)
        label = " ".join(text.split())[:48] or "%s %d" % (tag, mc.counts[prefix])
        return module_div(mid, kind, label, blk)

    return BLOCK_RE.sub(repl, chunk)


REMOTE_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?:https?:)?//[^)]*\)", re.I)
IMPORT_RE = re.compile(r"@import\s+[^;]*;", re.I)


def sanitize_css(block):
    """Preserved author CSS must not phone home when the deck is opened.

    An exported deck's inline <style> routinely pulls a web font or a background
    image from a remote origin; keeping those rules verbatim would contact that
    origin the moment the file is viewed, breaking the local-only guarantee.
    """
    cleaned = IMPORT_RE.sub("/* remote font import removed */", block)
    cleaned = REMOTE_URL_RE.sub("none /* remote asset removed */", cleaned)
    return cleaned


def convert_html(path, out_dir, mode=None):
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    base_dir = os.path.dirname(os.path.abspath(path))
    raw = re.sub(r"<script\b.*?</script>", "", raw, flags=re.S | re.I)
    head_extra = ""
    hm = re.search(r"<head[^>]*>(.*?)</head>", raw, flags=re.S | re.I)
    if hm:
        keep = [sanitize_css(b) for b in
                re.findall(r"<style\b.*?</style>", hm.group(1), flags=re.S | re.I)]
        for link in re.findall(r'<link\b[^>]*rel=["\']?stylesheet[^>]*>', hm.group(1), re.I):
            href = re.search(r'href=["\']([^"\']+)["\']', link, re.I)
            if href and not re.match(r"^(https?:)?//", href.group(1), re.I):
                local = copy_asset(href.group(1), out_dir, base_dir)
                if local and os.path.basename(local) != "theme.css":
                    keep.append('<link rel="stylesheet" href="%s">' % attr(local))
        head_extra = "\n".join(keep)
    m = re.search(r"<body[^>]*>(.*)</body>", raw, flags=re.S | re.I)
    body = m.group(1) if m else raw
    body = localize_html_assets(body, out_dir, base_dir)
    slides = []
    if re.search(r"<section\b[^>]*data-slide", body, flags=re.I):
        # already machine-style: keep every section attribute and its markup verbatim
        for i, m2 in enumerate(re.finditer(
                r"<section\b([^>]*data-slide[^>]*)>(.*?)</section>", body, flags=re.S | re.I), 1):
            attrs, inner = m2.group(1), m2.group(2)
            tm = re.search(r'data-topic="([^"]*)"', attrs)
            topic = html.unescape(tm.group(1)) if tm else "Slide %d" % i
            extra = re.sub(r'\s*(id|data-slide|data-topic)="[^"]*"', "", attrs).strip()
            slides.append({"n": i, "topic": topic, "body": inner, "attrs": extra})
        return slides, head_extra
    if re.search(r"<section\b", body, flags=re.I):
        parts = re.findall(r"<section\b[^>]*>(.*?)</section>", body, flags=re.S | re.I)
    else:
        parts = [p for p in re.split(r"(?=<h[12][\s>])", body, flags=re.I) if p.strip()]
    for i, part in enumerate(parts, 1):
        hm2 = re.search(r"<h[12][^>]*>(.*?)</h[12]>", part, flags=re.S | re.I)
        topic = re.sub(r"<[^>]+>", "", hm2.group(1)).strip()[:60] if hm2 else ""
        slides.append({"n": i, "topic": topic or "Slide %d" % i,
                       "body": wrap_html_blocks(part, i)})
    return slides, head_extra


# ---------------------------------------------------------------- assembly

def resolve_theme(cfg):
    """config.json 'theme' may be a bundled name or a path to the user's own css."""
    theme = cfg.get("theme") or "theme.css"
    for cand in (theme, os.path.join(ASSETS, theme),
                 os.path.expanduser(theme), os.path.join(SKILL_DIR, theme)):
        if os.path.isfile(cand):
            return cand
    emit("warn", "theme %r not found — using the bundled theme.css" % theme,
         "set config.json 'theme' to a filename in assets/ or a path")
    return os.path.join(ASSETS, "theme.css")


def write_slide_context(out_dir, slides):
    """Plain per-slide text so the answering agent knows what is on each slide
    without parsing HTML (and for image decks, where there is no DOM text)."""
    ctx = []
    for s in slides:
        text = s.get("text")
        if text is None:
            body = re.sub(r"<(script|style)\b.*?</\1>", " ", s["body"], flags=re.S | re.I)
            body = re.sub(r"<[^>]+>", " ", body)
            text = html.unescape(re.sub(r"[ \t]+", " ", body)).strip()
            text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
        row = {"n": s["n"], "topic": s["topic"], "text": text[:8000]}
        if s.get("modules"):
            row["modules"] = s["modules"]
        ctx.append(row)
    with open(os.path.join(out_dir, "slides.json"), "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=1)


def write_meeting_metadata(out_dir, source):
    """Keep source identity without touching the persistent comment state."""
    stat = os.stat(source)
    meta = {
        "source": os.path.realpath(source),
        "source_size": stat.st_size,
        "source_mtime": stat.st_mtime,
        "converted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(out_dir, "meeting.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build_index(out_dir, title, slides, head_extra=""):
    tpl_path = os.path.join(ASSETS, "meeting.html")
    if not os.path.isfile(tpl_path):
        die("skill assets missing (assets/meeting.html)", "reinstall the ar-meeting skill")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    sections = "\n".join(
        '<section id="s%d" data-slide="%d" data-topic="%s"%s>\n%s\n</section>'
        % (s["n"], s["n"], attr(str(s["topic"])[:60]),
           " " + s["attrs"] if s.get("attrs") else "", s["body"]) for s in slides)
    cfg = load_skill_config()
    conf = {"meeting": os.path.relpath(out_dir), "title": title, "slides": len(slides),
            "margin_position": cfg.get("margin_position") or cfg.get("chat_position") or "right"}
    out = (tpl.replace("<!--LM:TITLE-->", esc(title))
              .replace("<!--LM:CONFIG-->", json.dumps(conf))
              .replace("<!--LM:HEAD-->", head_extra or "")
              .replace("<!--LM:SLIDES-->", sections))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(out)
    shutil.copy2(resolve_theme(cfg), os.path.join(out_dir, "theme.css"))


def main(argv):
    args, out, name, mode = [], None, None, None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "--name", "--mode"):
            if i + 1 >= len(argv):
                die("%s needs a value" % a, USAGE, 2)
            val = argv[i + 1]
            if a == "--out":
                out = val
            elif a == "--name":
                name = val
            else:
                if val not in ("native", "image"):
                    die("unknown mode %r" % val, "--mode native | --mode image", 2)
                mode = val
            i += 2
        elif a in ("-h", "--help"):
            emit("ok", USAGE,
                 "default output stays under ~/.cache/ar-meeting/")
            return 0
        elif a.startswith("-"):
            die("unknown flag %s" % a, USAGE, 2)
        else:
            args.append(a)
            i += 1
    if len(args) != 1:
        die("expected exactly one input file", USAGE, 2)
    src = args[0]
    if not os.path.isfile(src):
        die("input not found: %s" % src, USAGE)
    stem = os.path.splitext(os.path.basename(src))[0]
    name = name or re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "deck"
    if out:
        out_dir = os.path.abspath(os.path.expanduser(out))
        os.makedirs(out_dir, exist_ok=True)
    else:
        cache_root = default_cache_root()
        os.makedirs(cache_root, mode=0o700, exist_ok=True)
        try:
            os.chmod(cache_root, 0o700)
        except OSError:
            pass
        out_dir = default_output_dir(src, name)
        os.makedirs(out_dir, mode=0o700, exist_ok=True)
    ext = os.path.splitext(src)[1].lower()
    head_extra = ""
    if ext == ".pptx":
        slides = convert_pptx(src, out_dir, mode)
    elif ext == ".pdf":
        if mode == "native":
            emit("warn", "pdf has no native mode — pages are rendered as images",
                 "text stays selectable via the overlay text layer")
        slides = convert_pdf(src, out_dir)
    elif ext in (".md", ".markdown"):
        slides = convert_md(src, out_dir)
    elif ext in (".html", ".htm"):
        slides, head_extra = convert_html(src, out_dir)
    elif ext == ".ppt":
        die("legacy .ppt is not supported",
            "open it in PowerPoint and save as .pptx, then convert that")
    else:
        die("unsupported input type %s" % (ext or "(none)"),
            "supported: .pptx .pdf .md .html")
    if not slides:
        die("no slides found in input", "check the file has content")
    write_slide_context(out_dir, slides)
    write_meeting_metadata(out_dir, src)
    build_index(out_dir, name, slides, head_extra)
    emit("ok", "wrote %s/index.html (%d slides)" % (out_dir, len(slides)),
         "python3 %s open %s" % (os.path.join(SCRIPT_DIR, "labmeet.py"), out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
