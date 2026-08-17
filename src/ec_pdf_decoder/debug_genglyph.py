from __future__ import annotations

import json
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


def _render_font_sequence(raw: bytes, gids: list[int], positions: list[tuple[int, int, int, int]] | None = None) -> tuple[bytes, tuple[int, int]]:
    """Render a glyph sequence from one TTF into a grayscale PNG-like bitmap.

    Returns PNG bytes plus (width, height). Uses Pillow/FontTools only for the
    diagnostic. The actual PDF glyphs remain the source of truth.
    """
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw

    # This is a deliberately simple outline rasterizer for comparison. If the
    # environment lacks Pillow, the caller will report that limitation.
    fd, name = tempfile.mkstemp(suffix=".ttf")
    Path(name).write_bytes(raw)
    try:
        font = TTFont(name, lazy=False)
        try:
            upem = int(font["head"].unitsPerEm)
            glyph_set = font.getGlyphSet()
            order = font.getGlyphOrder()
            scale = 1.0
            margin = 16
            canvas_w = 512
            canvas_h = 256
            image = Image.new("L", (canvas_w, canvas_h), 255)

            class Pen(BasePen):
                def __init__(self, glyphSet, draw, ox, oy, scale):
                    super().__init__(glyphSet)
                    self.draw = draw; self.ox = ox; self.oy = oy; self.scale = scale
                def _p(self, pt):
                    x, y = pt
                    return (self.ox + x * self.scale, self.oy - y * self.scale)
                def _moveTo(self, pt): self.cur = self._p(pt)
                def _lineTo(self, pt):
                    q = self._p(pt); self.draw.line([self.cur, q], fill=0, width=2); self.cur=q
                def _curveToOne(self, p1, p2, p3):
                    q3=self._p(p3); self.draw.line([self.cur, q3], fill=0, width=2); self.cur=q3
                def _qCurveToOne(self, p1, p2):
                    q=self._p(p2); self.draw.line([self.cur, q], fill=0, width=2); self.cur=q
                def _closePath(self): pass
                def _endPath(self): pass

            x_cursor = margin
            baseline = 190
            for gid in gids:
                if gid < 0 or gid >= len(order):
                    continue
                gname = order[gid]
                glyph = glyph_set[gname]
                pen = Pen(glyph_set, ImageDraw.Draw(image), x_cursor, baseline, scale)
                glyph.draw(pen)
                try:
                    aw = int(font["hmtx"].metrics[gname][0])
                except Exception:
                    aw = 500
                x_cursor += max(aw, 200) * scale
                if x_cursor > canvas_w - margin:
                    break
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as out:
                png_path = Path(out.name)
            image.save(png_path, format="PNG")
            png = png_path.read_bytes()
            png_path.unlink(missing_ok=True)
            return png, image.size
        finally:
            font.close()
    finally:
        Path(name).unlink(missing_ok=True)


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

        # Compare the candidate's rendered shaped sequence against each
        # unresolved PDF glyph as a single-glyph image. This is diagnostic only.
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
