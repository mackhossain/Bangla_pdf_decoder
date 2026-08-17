from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .bangla_harfbuzz import shape_gids
from .direct_pdf_fixed import embedded_fonts, ttf_gid_map, used_gids
from . import direct_pdf as _direct


def _candidate_status(text: str, candidate_path: Path = Path("data/bangla_conjuncts_comprehensive_validated.json")) -> str:
    if not candidate_path.exists():
        return "candidate database not found"
    try:
        data = json.loads(candidate_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"candidate database unreadable: {exc}"
    for item in data.get("conjuncts", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("glyph") == text:
            return f"candidate database: YES ({item.get('combination', '')})"
    return "candidate database: NO"


def _write_temp_font(raw: bytes) -> Path:
    fd, name = tempfile.mkstemp(suffix=".ttf")
    os.close(fd)
    path = Path(name)
    path.write_bytes(raw)
    return path


def _render_font_sequence(raw: bytes, gids: list[int]) -> "Image.Image":
    """Render glyph outlines into a white grayscale image, then crop to ink.

    This diagnostic intentionally compares glyph ink instead of a large shared
    white canvas. It is still an outline-level diagnostic, not a browser/PDF
    rasterizer.
    """
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw

    font_path = _write_temp_font(raw)
    font = None
    image = Image.new("L", (900, 450), 255)
    try:
        font = TTFont(str(font_path), lazy=False)
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        draw = ImageDraw.Draw(image)
        outlines = []

        class CollectPen(BasePen):
            def __init__(self, glyphSet):
                super().__init__(glyphSet)
                self.points = []
            def _moveTo(self, pt): self.points.append(pt)
            def _lineTo(self, pt): self.points.append(pt)
            def _curveToOne(self, p1, p2, p3): self.points.extend((p1, p2, p3))
            def _qCurveToOne(self, p1, p2): self.points.extend((p1, p2))
            def _closePath(self): pass
            def _endPath(self): pass

        all_points = []
        for gid in gids:
            if gid < 0 or gid >= len(order):
                continue
            name = order[gid]
            glyph = glyph_set[name]
            pen = CollectPen(glyph_set)
            glyph.draw(pen)
            if pen.points:
                outlines.append((glyph, pen.points))
                all_points.extend(pen.points)

        if not all_points:
            return image.crop((0, 0, 1, 1))

        min_x = min(float(x) for x, _ in all_points)
        max_x = max(float(x) for x, _ in all_points)
        min_y = min(float(y) for _, y in all_points)
        max_y = max(float(y) for _, y in all_points)
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        scale = min(300.0 / width, 300.0 / height)
        ox = 450.0 - (min_x + max_x) * scale / 2.0
        oy = 335.0 + (min_y + max_y) * scale / 2.0
        line_width = max(1, int(round(scale / 24.0)))

        class RasterPen(BasePen):
            def __init__(self, glyphSet):
                super().__init__(glyphSet)
                self.cur = None
            def _p(self, pt):
                x, y = pt
                return (ox + x * scale, oy - y * scale)
            def _moveTo(self, pt): self.cur = self._p(pt)
            def _lineTo(self, pt):
                q = self._p(pt)
                if self.cur is not None:
                    draw.line([self.cur, q], fill=0, width=line_width)
                self.cur = q
            def _curveToOne(self, p1, p2, p3):
                if self.cur is None:
                    self.cur = self._p(p3)
                    return
                p0 = self.cur
                q1, q2, q3 = self._p(p1), self._p(p2), self._p(p3)
                pts = [p0]
                for i in range(1, 49):
                    t = i / 48.0
                    mt = 1.0 - t
                    pts.append((
                        mt**3*p0[0] + 3*mt**2*t*q1[0] + 3*mt*t**2*q2[0] + t**3*q3[0],
                        mt**3*p0[1] + 3*mt**2*t*q1[1] + 3*mt*t**2*q2[1] + t**3*q3[1],
                    ))
                draw.line(pts, fill=0, width=line_width)
                self.cur = q3
            def _qCurveToOne(self, p1, p2):
                if self.cur is None:
                    self.cur = self._p(p2)
                    return
                p0 = self.cur
                q1, q2 = self._p(p1), self._p(p2)
                pts = [p0]
                for i in range(1, 49):
                    t = i / 48.0
                    mt = 1.0 - t
                    pts.append((
                        mt**2*p0[0] + 2*mt*t*q1[0] + t**2*q2[0],
                        mt**2*p0[1] + 2*mt*t*q1[1] + t**2*q2[1],
                    ))
                draw.line(pts, fill=0, width=line_width)
                self.cur = q2
            def _closePath(self): self.cur = None
            def _endPath(self): self.cur = None

        for glyph, _points in outlines:
            glyph.draw(RasterPen(glyph_set))

        # Crop to actual ink so the comparison is not dominated by white pixels.
        bbox = image.point(lambda p: 0 if p >= 250 else 255).getbbox()
        if bbox is None:
            return image.crop((0, 0, 1, 1))
        left, top, right, bottom = bbox
        pad = 10
        return image.crop((max(0, left-pad), max(0, top-pad), min(image.width, right+pad), min(image.height, bottom+pad)))
    finally:
        if font is not None:
            font.close()
        try:
            font_path.unlink(missing_ok=True)
        except PermissionError:
            pass


def _normalize_for_compare(image, size: int = 320):
    from PIL import Image
    image = image.convert("L")
    bbox = image.point(lambda p: 0 if p >= 250 else 255).getbbox()
    if bbox:
        image = image.crop(bbox)
    if image.width <= 0 or image.height <= 0:
        return Image.new("L", (size, size), 255)
    scale = min((size - 16) / image.width, (size - 16) / image.height)
    nw = max(1, int(round(image.width * scale)))
    nh = max(1, int(round(image.height * scale)))
    image = image.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("L", (size, size), 255)
    canvas.paste(image, ((size-nw)//2, (size-nh)//2))
    return canvas


def _png_similarity(a, b) -> float:
    from PIL import ImageChops
    ia = _normalize_for_compare(a)
    ib = _normalize_for_compare(b)
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()
    total = sum(i*n for i,n in enumerate(hist))
    denom = ia.width * ia.height * 255
    return max(0.0, 100.0 * (1.0 - total / max(denom, 1)))


def _ink_iou(a, b, threshold: int = 240) -> float:
    ia = _normalize_for_compare(a)
    ib = _normalize_for_compare(b)
    pa, pb = ia.load(), ib.load()
    intersection = union = 0
    for y in range(ia.height):
        for x in range(ia.width):
            a_ink = pa[x,y] < threshold
            b_ink = pb[x,y] < threshold
            if a_ink and b_ink: intersection += 1
            if a_ink or b_ink: union += 1
    return 100.0 * intersection / union if union else 100.0


def debug_genglyph(pdf: bytes, page: int, text: str) -> None:
    if not text:
        raise ValueError("debug-genglyph text cannot be empty")

    print("DEBUG GENGLYPH")
    print("==============")
    print(f"PAGE: {page}")
    print(f"TARGET: {text!r}")
    print(_candidate_status(text))

    seen_font = False
    for resource, base_font, raw in embedded_fonts(pdf, page):
        seen_font = True
        cmap = ttf_gid_map(raw)
        used = sorted(set(int(cid) for cid in used_gids(pdf, page)))
        unresolved = [cid for cid in used if cid not in cmap]
        shaped = shape_gids(raw, text)

        print()
        print(f"PDF RESOURCE: {resource}")
        print(f"EMBEDDED FONT: {base_font}")
        print(f"EMBEDDED FONT GID COUNT: {len(cmap)} mapped-by-cmap/name entries")
        print(f"PDF USED CIDS: {used}")
        print(f"PDF UNMAPPED CIDS: {unresolved}")
        print(f"EMBEDDED FONT HARFBUZZ GIDS FOR {text!r}: {shaped}")
        if shaped:
            for gid in shaped:
                print(f"  SHAPED TTF GID {gid}")

        if shaped and unresolved:
            try:
                candidate = _render_font_sequence(raw, [int(g) for g in shaped])
                print("INK NORMALIZED COMPARISON VS UNRESOLVED PDF CIDS:")
                scored = []
                for cid in unresolved:
                    target = _render_font_sequence(raw, [int(cid)])
                    pixel_score = _png_similarity(candidate, target)
                    iou_score = _ink_iou(candidate, target)
                    combined = 0.60 * pixel_score + 0.40 * iou_score
                    scored.append((combined, pixel_score, iou_score, cid))
                for combined, pixel_score, iou_score, cid in sorted(scored, reverse=True):
                    print(f"  CID {cid}: pixel={pixel_score:.3f}% ink_iou={iou_score:.3f}% combined={combined:.3f}%")
                if scored:
                    best = max(scored)
                    print(f"BEST CURRENT CANDIDATE: CID {best[3]} ({best[0]:.3f}% combined)")
            except Exception as exc:
                print(f"PIXEL COMPARISON UNAVAILABLE: {exc}")

    if not seen_font:
        print("\nNO EMBEDDED FontFile2 RESOURCE FOUND ON THE SELECTED PAGE")
    print("\nNOTE: this command is diagnostic only. It does not modify mappings or SVG files.")
