"""v2 review wrapper: preserves the v1 reviewer and adds exact visual suggestions."""
from __future__ import annotations

from pathlib import Path
from . import direct_review as _legacy
from .visual_suggestions import add_visual_suggestions

_CANDIDATE_PATH: Path | None = None
_original_write_html = _legacy._write_html


def _write_html(path, font_path, gid, line_cids, mapping=None):
    _original_write_html(path, font_path, gid, line_cids, mapping)
    candidate_path = _CANDIDATE_PATH
    if candidate_path is None or not candidate_path.exists():
        return
    try:
        add_visual_suggestions(Path(path), Path(font_path), Path(font_path).read_bytes(), int(gid), candidate_path)
    except Exception as exc:
        print(f"VISUAL SUGGESTIONS: unavailable ({exc})", flush=True)


_legacy._write_html = _write_html


def run(pdf_path: Path, page: int, db_path: Path, candidate_path: Path, only_gid=None, unresolved_cids=None, operations=None):
    global _CANDIDATE_PATH
    # Keep the v1 conjunct path untouched. v2 suggestions use the separate,
    # validated candidate database so the existing decoder behavior is unchanged.
    _CANDIDATE_PATH = Path("data/bangla_conjuncts_comprehensive_validated.json")
    try:
        return _legacy.run(pdf_path, page, db_path, candidate_path, only_gid, unresolved_cids, operations)
    finally:
        _CANDIDATE_PATH = None
