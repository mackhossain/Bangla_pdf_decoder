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


def _render_font_sequence(raw: bytes, gids: list[int]) -> tuple[bytes, tuple[int, int]]:
    """Render a glyph sequence from one TTF into a grayscale PNG bitmap.

    This diagnostic rasterizer intentionally uses only the font outline geometry;
    it does not modify the PDF, mappings, or SVG output. Temporary font files are
    explicitly closed before deletion so the code works on Windows too.
    """
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw

    font_path = _write_temp_font(raw)
    font = None
    image = Image.new("L", (512, 256), 255)
    try:
        font = TTFont(str(font_path), lazy=False)
        glyph_set = font.getGlyphSet()
        order = font.getGlyphOrder()
        draw = ImageDraw.Draw(image)

        # Scale the whole sequence from its actual glyph bounds into a stable
        # comparison canvas. This avoids treating different whitespace/advance
        # widths as shape differences.
        outlines = []
        for gid in gids:
            if gid < 0 or gid >= len(order):
                continue
            name = order[gid]
            glyph = glyph_set[name]
            points = []

            class CollectPen(BasePen):
                def __init__(self, glyphSet):
                    super().__init__(glyphSet)
                def _moveTo(self, pt): points.append(pt)
                def _lineTo(self, pt): points.append(pt)
                def _curveToOne(self, p1, p2, p3): points.extend((p1, p2, p3))
                def _qCurveToOne(self, p1, p2): points.extend((p1, p2))
                def _closePath(self): pass
                def _endPath(self): pass

            glyph.draw(CollectPen(glyph_set))
            outlines.append((gid, glyph, points))

        if not outlines:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out:
                png_path = Path(out.name)
            try:
                image.save(png_path, format="PNG")
                return png_path.read_bytes(), image.size
            finally:
                png_path.unlink(missing_ok=True)

        xs=[]; ys=[]
        for _gid, _glyph, pts in outlines:
            for x,y in pts:
                xs.append(float(x)); ys.append(float(y))
        if not xs or not ys:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out:
                png_path = Path(out.name)
            try:
                image.save(png_path, format="PNG")
                return png_path.read_bytes(), image.size
            finally:
                png_path.unlink(missing_ok=True)

        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(max_x - min_x, 1.0)
        height = max(max_y - min_y, 1.0)
        scale = min(220.0 / width, 220.0 / height)
        ox = 256.0 - ((min_x + max_x) * scale / 2.0)
        oy = 192.0 + ((min_y + max_y) * scale / 2.0)

        class RasterPen(BasePen):
            def __init__(self, glyphSet):
                super().__init__(glyphSet)
                self.cur = None
            def _p(self, pt):
                x,y = pt
                return (ox + x*scale, oy - y*scale)
            def _moveTo(self, pt): self.cur = self._p(pt)
            def _lineTo(self, pt):
                q=self._p(pt)
                draw.line([self.cur,q],fill=0,width=max(1,int(scale/20)))
                self.cur=q
            def _curveToOne(self, p1, p2, p3):
                # Dense polyline approximation sufficient for diagnostics.
                start=self.cur
                q3=self._p(p3)
                draw.line([start,q3],fill=0,width=max(1,int(scale/20)))
                self.cur=q3
            def _qCurveToOne(self, p1, p2):
                start=self.cur
                q=self._p(p2)
                draw.line([start,q],fill=0,width=max(1,int(scale/20)))
                self.cur=q
            def _closePath(self):
                self.cur=None
            def _endPath(self):
                self.cur=None

        for _gid, glyph, _pts in outlines:
            glyph.draw(RasterPen(glyph_set))

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out:
            png_path = Path(out.name)
        try:
            image.save(png_path, format="PNG")
            return png_path.read_bytes(), image.size
        finally:
            png_path.unlink(missing_ok=True)
    finally:
        # Explicitly release all FontTools objects before deleting the temp file.
        if font is not None:
            font.close()
        font = None
        try:
            font_path.unlink(missing_ok=True)
        except PermissionError:
            # Windows can hold the file briefly through a fontTools table object;
            # leave the temp file rather than failing the entire diagnostic.
            pass


def _png_similarity(a: bytes, b: bytes) -> float:
    from PIL import Image, ImageChops
    import io
    ia = Image.open(io.BytesIO(a)).convert("L")
    ib = Image.open(io.BytesIO(b)).convert("L")
    if ia.size != ib.size:
        w=max(ia.width, ib.width); h=max(ia.height, ib.height)
        ca=Image.new("L",(w,h),255); cb=Image.new("L",(w,h),255)
        ca.paste(ia,((w-ia.width)//2,(h-ia.height)//2)); cb.paste(ib,((w-ib.width)//2,(h-ib.height)//2)); ia,ib=ca,cb
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()
    total = sum(i*n for i,n in enumerate(hist))
    denom = ia.width * ia.height * 255
    return max(0.0, 100.0 * (1.0 - total / denom))


def debug_genglyph(pdf: bytes, page: int, text: str) -> None:
    if not text:
        raise ValueError("debug-genglyph text cannot be empty")

    print("DEBUG GENGLYPH")
    print("==============")
    print(f"PAGE: {page}")
    print(f"TARGET: {text!r}")
    print(_candidate_status(text))

    seen_font=False
    for resource, base_font, raw in embedded_fonts(pdf, page):
        seen_font=True
        cmap=ttf_gid_map(raw)
        used=sorted(set(int(cid) for cid in used_gids(pdf, page)))
        unresolved=[cid for cid in used if cid not in cmap]
        shaped=shape_gids(raw, text)
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
                candidate_png, _ = _render_font_sequence(raw, [int(g) for g in shaped])
                print("PIXEL SIMILARITY VS UNRESOLVED PDF CIDS:")
                scored=[]
                for cid in unresolved:
                    pdf_png, _ = _render_font_sequence(raw, [int(cid)])
                    score=_png_similarity(candidate_png,pdf_png)
                    scored.append((score,cid))
                for score,cid in sorted(scored, reverse=True):
                    print(f"  CID {cid}: {score:.3f}%")
            except Exception as exc:
                print(f"PIXEL COMPARISON UNAVAILABLE: {exc}")

    if not seen_font:
        print("\nNO EMBEDDED FontFile2 RESOURCE FOUND ON THE SELECTED PAGE")
    print("\nNOTE: this command is diagnostic only. It does not modify mappings or SVG files.")
