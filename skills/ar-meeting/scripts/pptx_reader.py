#!/usr/bin/env python3
"""Read .pptx with the Python standard library only — no python-pptx, no LibreOffice.

A .pptx is a zip of OOXML parts. This pulls out everything a meeting deck needs:
slide size, per-shape geometry, real text with run formatting, embedded images,
tables and speaker notes. Results are plain dicts so convert.py can emit semantic
HTML — real selectable text in real modules — instead of a picture of a slide.

Deliberately partial: it reads what a deck says, not everything OOXML can express.
Gradients, 3-D effects, animations and chart internals are ignored; charts and
unrenderable image formats (emf/wmf) come back as labelled placeholders.
"""
import re
import xml.etree.ElementTree as ET
import zipfile

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

EMU_PER_PX = 9525          # 914400 EMU/inch ÷ 96 px/inch
DEFAULT_SZ = 1800          # hundredths of a point
WEB_IMAGE = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg")


# ---------------------------------------------------------------- parts & rels

def _rels_for(zf, part):
    d, _, n = part.rpartition("/")
    rp = "%s/_rels/%s.rels" % (d, n)
    out = {}
    try:
        root = ET.fromstring(zf.read(rp))
    except (KeyError, ET.ParseError):
        return out
    for rel in root:
        out[rel.get("Id")] = (rel.get("Target", ""), rel.get("Type", ""))
    return out


def _resolve(base_part, target):
    if target.startswith("/"):
        return target.lstrip("/")
    stack = []
    for seg in (base_part.rpartition("/")[0] + "/" + target).split("/"):
        if seg == "..":
            if stack:
                stack.pop()
        elif seg and seg != ".":
            stack.append(seg)
    return "/".join(stack)


def _first_rel(zf, part, suffix):
    for _, (tgt, typ) in _rels_for(zf, part).items():
        if typ.endswith(suffix):
            return _resolve(part, tgt)
    return None


# ---------------------------------------------------------------- colors

def _theme_colors(zf, master_part):
    colors = {}
    theme_part = _first_rel(zf, master_part, "/theme") if master_part else None
    if not theme_part:
        return colors
    try:
        root = ET.fromstring(zf.read(theme_part))
    except (KeyError, ET.ParseError):
        return colors
    scheme = root.find("%sthemeElements/%sclrScheme" % (A, A))
    for c in scheme if scheme is not None else []:
        name = c.tag[len(A):]
        srgb, sysc = c.find(A + "srgbClr"), c.find(A + "sysClr")
        if srgb is not None:
            colors[name] = srgb.get("val")
        elif sysc is not None:
            colors[name] = sysc.get("lastClr") or "000000"
    # OOXML swaps these aliases relative to the scheme element names
    for alias, real in (("tx1", "dk1"), ("bg1", "lt1"), ("tx2", "dk2"), ("bg2", "lt2")):
        if alias not in colors and real in colors:
            colors[alias] = colors[real]
    return colors


def _clr_from(el, theme):
    """Colour of a <a:solidFill>-ish container, honouring lumMod/lumOff shading."""
    if el is None:
        return None
    srgb = el.find(A + "srgbClr")
    node = srgb
    if srgb is not None:
        hexv = srgb.get("val")
    else:
        sc = el.find(A + "schemeClr")
        if sc is None:
            return None
        node = sc
        hexv = theme.get(sc.get("val"))
        if not hexv:
            return None
    try:
        rgb = [int(hexv[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, TypeError):
        return None
    lum_mod = node.find(A + "lumMod")
    lum_off = node.find(A + "lumOff")
    if lum_mod is not None:
        f = int(lum_mod.get("val", "100000")) / 100000.0
        rgb = [c * f for c in rgb]
    if lum_off is not None:
        f = int(lum_off.get("val", "0")) / 100000.0
        rgb = [c + 255 * f for c in rgb]
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c)))) for c in rgb)


def _fill_color(sp_pr, theme):
    if sp_pr is None:
        return None
    if sp_pr.find(A + "noFill") is not None:
        return None
    return _clr_from(sp_pr.find(A + "solidFill"), theme)


# ---------------------------------------------------------------- geometry

def _xfrm_of(el):
    if el is None:
        return None
    x = el.find("%sspPr/%sxfrm" % (P, A))
    if x is None:
        x = el.find("%sgrpSpPr/%sxfrm" % (P, A))
    if x is None:
        x = el.find("%sxfrm" % P)      # graphicFrame uses p:xfrm, not a:xfrm
    if x is None:
        x = el.find("%sxfrm" % A)
    if x is None:
        return None
    off, ext = x.find(A + "off"), x.find(A + "ext")
    if off is None or ext is None:
        return None
    try:
        box = {"x": int(off.get("x")), "y": int(off.get("y")),
               "w": int(ext.get("cx")), "h": int(ext.get("cy")),
               "rot": int(x.get("rot") or 0) / 60000.0,
               "flipH": x.get("flipH") in ("1", "true"),
               "flipV": x.get("flipV") in ("1", "true")}
    except (TypeError, ValueError):
        return None
    ch_off, ch_ext = x.find(A + "chOff"), x.find(A + "chExt")
    if ch_off is not None and ch_ext is not None:
        try:
            box["chOff"] = (int(ch_off.get("x")), int(ch_off.get("y")))
            box["chExt"] = (int(ch_ext.get("cx")), int(ch_ext.get("cy")))
        except (TypeError, ValueError):
            pass
    return box


def _ph_of(el):
    ph = el.find("%snvSpPr/%snvPr/%sph" % (P, P, P))
    if ph is None:
        ph = el.find("%snvPicPr/%snvPr/%sph" % (P, P, P))
    if ph is None:
        ph = el.find("%snvGraphicFramePr/%snvPr/%sph" % (P, P, P))
    if ph is None:
        return None
    return (ph.get("type") or "body", ph.get("idx"))


def _index_placeholders(root):
    """Map (type, idx) -> shape element for a layout or master."""
    out = {}
    tree = root.find("%scSld/%sspTree" % (P, P))
    for sp in tree if tree is not None else []:
        ph = _ph_of(sp)
        if ph:
            out[ph] = sp
            out.setdefault((ph[0], None), sp)
            if ph[1] is not None:
                out.setdefault((None, ph[1]), sp)
    return out


def _inherited_xfrm(ph, chain):
    if not ph:
        return None
    for table in chain:
        for key in (ph, (ph[0], None), (None, ph[1])):
            sp = table.get(key)
            if sp is not None:
                box = _xfrm_of(sp)
                if box:
                    return box
    return None


# ---------------------------------------------------------------- text

def _master_style_sz(master_root, ph_type, lvl):
    if master_root is None:
        return None
    tag = {"title": "titleStyle", "ctrTitle": "titleStyle",
           "subTitle": "bodyStyle", "body": "bodyStyle"}.get(ph_type, "otherStyle")
    style = master_root.find("%stxStyles/%s%s" % (P, P, tag))
    if style is None:
        return None
    lvl_el = style.find("%slvl%dpPr" % (A, min(max(lvl, 0), 8) + 1))
    if lvl_el is None:
        return None
    d = lvl_el.find(A + "defRPr")
    return int(d.get("sz")) if d is not None and d.get("sz") else None


def _runs_of(para, theme, inherit_sz, inherit_color):
    runs = []
    for node in para:
        tag = node.tag
        if tag == A + "br":
            runs.append({"text": "\n", "br": True})
            continue
        if tag not in (A + "r", A + "fld"):
            continue
        t = node.find(A + "t")
        if t is None or t.text is None:
            continue
        rpr = node.find(A + "rPr")
        run = {"text": t.text, "sz": inherit_sz, "b": False, "i": False,
               "u": False, "color": inherit_color, "font": None}
        if rpr is not None:
            if rpr.get("sz"):
                run["sz"] = int(rpr.get("sz"))
            run["b"] = rpr.get("b") in ("1", "true")
            run["i"] = rpr.get("i") in ("1", "true")
            run["u"] = bool(rpr.get("u")) and rpr.get("u") != "none"
            c = _clr_from(rpr.find(A + "solidFill"), theme)
            if c:
                run["color"] = c
            latin = rpr.find(A + "latin")
            if latin is not None and latin.get("typeface"):
                run["font"] = latin.get("typeface")
        runs.append(run)
    return runs


def _text_of(sp, theme, ph, master_root):
    tx = sp.find(P + "txBody")
    if tx is None:
        tx = sp.find("%stxBody" % A)      # table cells
    if tx is None:
        return None
    body_pr = tx.find(A + "bodyPr")
    anchor = (body_pr.get("anchor") if body_pr is not None else None) or "t"
    ph_type = ph[0] if ph else None
    paras = []
    for para in tx.findall(A + "p"):
        p_pr = para.find(A + "pPr")
        lvl = int(p_pr.get("lvl") or 0) if p_pr is not None else 0
        align = (p_pr.get("algn") if p_pr is not None else None) or None
        sz = _master_style_sz(master_root, ph_type, lvl) or DEFAULT_SZ
        if p_pr is not None:
            d = p_pr.find(A + "defRPr")
            if d is not None and d.get("sz"):
                sz = int(d.get("sz"))
        runs = _runs_of(para, theme, sz, None)
        if not runs:
            paras.append({"lvl": lvl, "align": align, "runs": [], "bullet": False})
            continue
        bullet = False
        if p_pr is not None:
            if p_pr.find(A + "buNone") is not None:
                bullet = False
            elif p_pr.find(A + "buChar") is not None or p_pr.find(A + "buAutoNum") is not None:
                bullet = True
            elif ph_type in ("body", "subTitle", None):
                bullet = True
        elif ph_type in ("body", "subTitle"):
            bullet = True
        if ph_type in ("title", "ctrTitle"):
            bullet = False
        paras.append({"lvl": lvl, "align": align, "runs": runs, "bullet": bullet})
    if not any(p["runs"] for p in paras):
        return None
    return {"anchor": anchor, "paragraphs": paras}


def plain_text(text):
    """Flatten a text dict to a single string."""
    if not text:
        return ""
    out = []
    for p in text["paragraphs"]:
        out.append("".join(r["text"] for r in p["runs"]))
    return "\n".join(s for s in out if s.strip())


# ---------------------------------------------------------------- shapes

def _table_of(gf, theme, master_root):
    tbl = gf.find("%sgraphic/%sgraphicData/%stbl" % (A, A, A))
    if tbl is None:
        return None
    widths = []
    grid = tbl.find(A + "tblGrid")
    for gc in grid if grid is not None else []:
        try:
            widths.append(int(gc.get("w")))
        except (TypeError, ValueError):
            widths.append(0)
    rows = []
    for tr in tbl.findall(A + "tr"):
        cells = []
        for tc in tr.findall(A + "tc"):
            cells.append({"text": _text_of(tc, theme, None, master_root),
                          "fill": _clr_from(tc.find("%stcPr/%ssolidFill" % (A, A)), theme)})
        rows.append(cells)
    return {"widths": widths, "rows": rows}


def _apply(box, t):
    """Map a raw box through transform t: X = ox + (x - cx) * sx."""
    if not t:
        return box
    b = dict(box)
    b["x"] = t["ox"] + (box["x"] - t["cx"]) * t["sx"]
    b["y"] = t["oy"] + (box["y"] - t["cy"]) * t["sy"]
    b["w"] = box["w"] * t["sx"]
    b["h"] = box["h"] * t["sy"]
    return b


def _walk(tree, zf, part, rels, theme, layout_phs, master_phs, master_root,
          offset=None, out=None):
    """Flatten the shape tree, mapping group-child coordinates into slide space."""
    out = [] if out is None else out
    for el in tree:
        tag = el.tag
        if tag == P + "grpSp":
            box = _xfrm_of(el)
            child_off = offset
            if box and "chExt" in box and box["chExt"][0] and box["chExt"][1]:
                # place the group itself in slide space, then scale its child space into it
                g = _apply(box, offset)
                child_off = {"ox": g["x"], "oy": g["y"],
                             "sx": g["w"] / float(box["chExt"][0]),
                             "sy": g["h"] / float(box["chExt"][1]),
                             "cx": box["chOff"][0], "cy": box["chOff"][1]}
            _walk(el, zf, part, rels, theme, layout_phs, master_phs, master_root,
                  child_off, out)
            continue
        if tag not in (P + "sp", P + "pic", P + "graphicFrame", P + "cxnSp"):
            continue
        ph = _ph_of(el)
        box = _xfrm_of(el) or _inherited_xfrm(ph, (layout_phs, master_phs))
        if not box:
            continue
        box = _apply(box, offset)
        name = ""
        nv = el.find(".//" + P + "cNvPr")
        if nv is not None:
            name = nv.get("name") or ""
        shape = {"box": box, "name": name, "ph": ph}

        if tag == P + "pic":
            blip = el.find("%sblipFill/%sblip" % (P, A))
            rid = blip.get(R + "embed") if blip is not None else None
            target = rels.get(rid, "") if rid else ""
            shape["kind"] = "picture"
            shape["media"] = _resolve(part, target) if target else None
            shape["alt"] = (nv.get("descr") if nv is not None else "") or name
            out.append(shape)
            continue

        if tag == P + "graphicFrame":
            table = _table_of(el, theme, master_root)
            if table:
                shape["kind"] = "table"
                shape["table"] = table
            else:
                data = el.find("%sgraphic/%sgraphicData" % (A, A))
                uri = data.get("uri", "") if data is not None else ""
                shape["kind"] = "chart" if "chart" in uri else "object"
                shape["uri"] = uri
            out.append(shape)
            continue

        if tag == P + "cxnSp":
            shape["kind"] = "line"
            shape["color"] = _clr_from(el.find("%sspPr/%sln/%ssolidFill" % (P, A, A)), theme)
            out.append(shape)
            continue

        text = _text_of(el, theme, ph, master_root)
        sp_pr = el.find(P + "spPr")
        fill = _fill_color(sp_pr, theme)
        if text is None and fill is None:
            continue
        shape["kind"] = "text" if text else "block"
        shape["text"] = text
        shape["fill"] = fill
        shape["line"] = _clr_from(sp_pr.find("%sln/%ssolidFill" % (A, A)), theme) \
            if sp_pr is not None else None
        out.append(shape)
    return out


# ---------------------------------------------------------------- background

def _bg_of(root, theme):
    bg = root.find("%scSld/%sbg" % (P, P))
    if bg is None:
        return None
    return _clr_from(bg.find("%sbgPr/%ssolidFill" % (P, A)), theme)


# ---------------------------------------------------------------- entry point

def read(path):
    """Return {'w','h','slides':[...]}; raises ValueError if this is not a pptx."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("not a valid .pptx (not a zip archive)")
    with zf:
        names = set(zf.namelist())
        if "ppt/presentation.xml" not in names:
            raise ValueError("not a PowerPoint file (no ppt/presentation.xml)")
        pres = ET.fromstring(zf.read("ppt/presentation.xml"))
        sz = pres.find(P + "sldSz")
        w = int(sz.get("cx")) if sz is not None else 9144000
        h = int(sz.get("cy")) if sz is not None else 6858000
        pres_rels = _rels_for(zf, "ppt/presentation.xml")

        order = []
        lst = pres.find(P + "sldIdLst")
        for sld in lst if lst is not None else []:
            rid = sld.get(R + "id")
            if rid in pres_rels:
                order.append(_resolve("ppt/presentation.xml", pres_rels[rid][0]))
        if not order:
            order = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                           key=lambda n: int(re.search(r"(\d+)", n).group(1)))

        slides = []
        for part in order:
            try:
                root = ET.fromstring(zf.read(part))
            except (KeyError, ET.ParseError):
                continue
            rels = {rid: tgt for rid, (tgt, _) in _rels_for(zf, part).items()}
            layout_part = _first_rel(zf, part, "/slideLayout")
            layout_root = master_root = None
            layout_phs = master_phs = {}
            master_part = None
            if layout_part:
                try:
                    layout_root = ET.fromstring(zf.read(layout_part))
                    layout_phs = _index_placeholders(layout_root)
                except (KeyError, ET.ParseError):
                    pass
                master_part = _first_rel(zf, layout_part, "/slideMaster")
                if master_part:
                    try:
                        master_root = ET.fromstring(zf.read(master_part))
                        master_phs = _index_placeholders(master_root)
                    except (KeyError, ET.ParseError):
                        pass
            theme = _theme_colors(zf, master_part) if master_part else {}

            tree = root.find("%scSld/%sspTree" % (P, P))
            shapes = _walk(tree if tree is not None else [], zf, part, rels, theme,
                           layout_phs, master_phs, master_root)

            notes = None
            notes_part = _first_rel(zf, part, "/notesSlide")
            if notes_part:
                try:
                    nroot = ET.fromstring(zf.read(notes_part))
                    chunks = []
                    ntree = nroot.find("%scSld/%sspTree" % (P, P))
                    for sp in ntree if ntree is not None else []:
                        if sp.tag != P + "sp":
                            continue
                        ph = _ph_of(sp)
                        if ph and ph[0] == "sldNum":
                            continue
                        txt = plain_text(_text_of(sp, theme, ph, None))
                        if txt.strip():
                            chunks.append(txt)
                    notes = "\n\n".join(chunks) or None
                except (KeyError, ET.ParseError):
                    notes = None

            bg = _bg_of(root, theme)
            if bg is None and layout_root is not None:
                bg = _bg_of(layout_root, theme)
            if bg is None and master_root is not None:
                bg = _bg_of(master_root, theme)

            topic = ""
            for s in shapes:
                if s["ph"] and s["ph"][0] in ("title", "ctrTitle") and s.get("text"):
                    topic = plain_text(s["text"]).splitlines()[0]
                    break
            if not topic:
                for s in shapes:
                    if s.get("text"):
                        first = plain_text(s["text"]).strip().splitlines()
                        if first:
                            topic = first[0]
                            break

            slides.append({"shapes": shapes, "notes": notes, "bg": bg,
                           "topic": topic[:60], "part": part})

        media = {}
        for s in slides:
            for sh in s["shapes"]:
                if sh["kind"] == "picture" and sh.get("media") and sh["media"] in names:
                    media[sh["media"]] = zf.read(sh["media"])
        return {"w": w, "h": h, "slides": slides, "media": media}


def is_web_image(part_name):
    return part_name.lower().endswith(WEB_IMAGE)
