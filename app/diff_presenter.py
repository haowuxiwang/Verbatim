from __future__ import annotations

from html import escape
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QTabWidget

from app.view_models import CompareViewModel
from core.field_mapper import FieldDiff
from core.models import DiffOpType


def _preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _inline_char_diff_html(left: str, right: str) -> tuple[str, str]:
    """Return (left_html, right_html) with per-character diff highlighting.

    Differing characters are wrapped in ``<span>`` elements with background
    colours: light-red for left-only (deleted), light-green for right-only
    (inserted).  Unchanged characters are escaped but unstyled.

    For long text (>200 chars) a context window of 40 chars around the first
    and last difference is shown, with ``…`` markers for truncated regions.
    """
    from core.diff_engine import _lcs_match_pairs

    if not left and not right:
        return ("", "")

    # Fast path: identical strings.
    if left == right:
        esc = escape(left)
        return (f"<code>{esc}</code>", f"<code>{esc}</code>")

    pairs = _lcs_match_pairs(left, right)

    # Build per-character status: 0=matched, 1=left-only(deleted), 2=right-only(inserted)
    left_status = [1] * len(left)   # default: deleted
    right_status = [2] * len(right)  # default: inserted
    for i, j in pairs:
        left_status[i] = 0
        right_status[j] = 0

    # --- context window for long text ---
    ctx = 40
    if len(left) > 200 or len(right) > 200:
        # Find first and last differing positions.
        first_diff_l = next((k for k, s in enumerate(left_status) if s != 0), len(left))
        first_diff_r = next((k for k, s in enumerate(right_status) if s != 0), len(right))
        first_diff = min(first_diff_l, first_diff_r)

        last_diff_l = len(left) - 1 - next(
            (k for k, s in enumerate(reversed(left_status)) if s != 0), len(left)
        )
        last_diff_r = len(right) - 1 - next(
            (k for k, s in enumerate(reversed(right_status)) if s != 0), len(right)
        )
        last_diff = max(last_diff_l, last_diff_r)

        win_start = max(0, first_diff - ctx)
        win_end = min(max(len(left), len(right)), last_diff + ctx + 1)

        left_status = left_status[win_start:win_end]
        right_status = right_status[win_start:win_end]
        left_slice = left[win_start:win_end]
        right_slice = right[win_start:win_end]
        has_truncation = True
    else:
        left_slice = left
        right_slice = right
        has_truncation = False

    def _build_html(text: str, statuses: list[int], full_len: int) -> str:
        parts: list[str] = []
        for ch, st in zip(text, statuses):
            esc_ch = escape(ch)
            if st == 0:
                parts.append(esc_ch)
            elif st == 1:  # deleted (left-only)
                parts.append(f'<span style="background:#fdd">{esc_ch}</span>')
            else:  # inserted (right-only)
                parts.append(f'<span style="background:#dfd">{esc_ch}</span>')
        result = "".join(parts)
        if has_truncation:
            prefix = "…" if win_start > 0 else ""
            suffix = "…" if win_end < full_len else ""
            result = prefix + result + suffix
        return f"<code>{result}</code>"

    return (_build_html(left_slice, left_status, len(left)), _build_html(right_slice, right_status, len(right)))


def describe_field_diff(fd: FieldDiff) -> str:
    if fd.diff_type == "add":
        return f'[field+] {fd.field_name}: "{_preview(fd.right_value or "", 20)}"'
    if fd.diff_type == "del":
        return f'[field-] {fd.field_name}: "{_preview(fd.left_value or "", 20)}"'
    if fd.diff_type == "replace":
        left_preview = _preview(fd.left_value or "", 15)
        right_preview = _preview(fd.right_value or "", 15)
        return f'[field~] {fd.field_name}: "{left_preview}" -> "{right_preview}"'
    return f"[field] {fd.field_name}"


def describe_op(op: Any) -> str:
    if op.type == DiffOpType.FORMAT_CHANGE:
        reasons = op.meta.get("reasons", [])
        reason_str = ", ".join(str(r) for r in reasons) if reasons else "format"
        char_count = len(op.left_indices) or len(op.right_indices)
        return f"[F] format diff: {reason_str} ({char_count} chars)"

    if op.type == DiffOpType.DEL:
        text = str(op.meta.get("left_text", ""))
        return f'[-] left only: "{_preview(text, 15)}" ({len(text)} chars)'

    if op.type == DiffOpType.ADD:
        text = str(op.meta.get("right_text", ""))
        return f'[+] right only: "{_preview(text, 15)}" ({len(text)} chars)'

    if op.type == DiffOpType.REPLACE:
        left_text = str(op.meta.get("left_text", ""))
        right_text = str(op.meta.get("right_text", ""))
        return f'[~] replace: "{_preview(left_text, 10)}" -> "{_preview(right_text, 10)}"'

    if op.type == DiffOpType.VISUAL_DIFF:
        score = float(op.meta.get("score", 0.0) or 0.0)
        diff_pixels = int(op.meta.get("diff_pixels", 0) or 0)
        return f"[V] visual diff: score={score:.3f}, pixels={diff_pixels}"

    return "[?] unknown diff type"


def build_field_diff_details_html(fd: FieldDiff) -> str:
    details = "<div style='line-height: 1.6; font-family: Microsoft YaHei, sans-serif;'>"

    if fd.diff_type == "match":
        details += (
            "<h3 style='color:#27ae60;margin-top:0'>Field Match</h3>"
            f"<p><b>{escape(fd.field_name)}</b>: {escape(fd.left_value or '')}</p>"
        )
    elif fd.diff_type == "add":
        details += (
            "<h3 style='color:#27ae60;margin-top:0'>Field Added</h3>"
            f"<p><b>{escape(fd.field_name)}</b></p>"
            f"<p>{escape(fd.right_value or '')}</p>"
        )
    elif fd.diff_type == "del":
        details += (
            "<h3 style='color:#c0392b;margin-top:0'>Field Removed</h3>"
            f"<p><b>{escape(fd.field_name)}</b></p>"
            f"<p>{escape(fd.left_value or '')}</p>"
        )
    elif fd.diff_type == "replace":
        details += (
            "<h3 style='color:#2980b9;margin-top:0'>Field Value Changed</h3>"
            f"<p><b>{escape(fd.field_name)}</b></p>"
            f"<p>Left: {escape(fd.left_value or '')}</p>"
            f"<p>Right: {escape(fd.right_value or '')}</p>"
        )

    details += "<p style='color:#e67e22'>Field-level mapping is approximate and may not have exact coordinates.</p>"
    details += "</div>"
    return details


def build_diff_details_html(op: Any) -> str:
    details = "<div style='line-height: 1.6; font-family: Microsoft YaHei, sans-serif;'>"

    if op.type == DiffOpType.DEL:
        text = str(op.meta.get("left_text", ""))
        details += "<h3 style='color:#c0392b;margin-top:0'>[-] Left Only</h3>"
        details += (
            "<div style='background:#fdf2f2;padding:10px;border-left:3px solid #c0392b'>"
            f"<code>{escape(text)}</code></div>"
        )
        details += f"<p style='color:#7f8c8d'>Characters: {len(text)}</p>"
    elif op.type == DiffOpType.ADD:
        text = str(op.meta.get("right_text", ""))
        details += "<h3 style='color:#27ae60;margin-top:0'>[+] Right Only</h3>"
        details += (
            "<div style='background:#f0faf4;padding:10px;border-left:3px solid #27ae60'>"
            f"<code>{escape(text)}</code></div>"
        )
        details += f"<p style='color:#7f8c8d'>Characters: {len(text)}</p>"
    elif op.type == DiffOpType.REPLACE:
        left_text = str(op.meta.get("left_text", ""))
        right_text = str(op.meta.get("right_text", ""))
        left_html, right_html = _inline_char_diff_html(left_text, right_text)
        details += "<h3 style='color:#2980b9;margin-top:0'>[~] Content Changed</h3>"
        details += (
            "<p><b>Left</b></p>"
            "<div style='background:#f0f7fb;padding:10px;border-left:3px solid #2980b9'>"
            f"{left_html}</div>"
        )
        details += (
            "<p><b>Right</b></p>"
            "<div style='background:#f0f7fb;padding:10px;border-left:3px solid #2980b9'>"
            f"{right_html}</div>"
        )
        details += f"<p style='color:#7f8c8d'>Left {len(left_text)} chars | Right {len(right_text)} chars</p>"
    elif op.type == DiffOpType.FORMAT_CHANGE:
        reasons = op.meta.get("reasons", [])
        char_count = len(op.left_indices) or len(op.right_indices)
        reason_str = ", ".join(str(r) for r in reasons) if reasons else "unknown"
        details += "<h3 style='color:#b8860b;margin-top:0'>[F] Format Changed</h3>"
        details += f"<p><b>Reasons:</b> {escape(reason_str)}</p>"
        details += f"<p style='color:#7f8c8d'>Affected chars: {char_count}</p>"
    elif op.type == DiffOpType.VISUAL_DIFF:
        score = float(op.meta.get("score", 0.0) or 0.0)
        diff_pixels = int(op.meta.get("diff_pixels", 0) or 0)
        details += "<h3 style='color:#8e44ad;margin-top:0'>[V] Visual Difference</h3>"
        details += "<p>Text/OCR was not reliable enough, so this result comes from raster comparison.</p>"
        details += f"<p><b>Confidence:</b> {score:.3f}</p>"
        details += f"<p><b>Different pixels:</b> {diff_pixels}</p>"
    else:
        details += "<h3 style='margin-top:0'>Unknown Diff</h3>"

    details += "</div>"
    return details


def populate_diff_lists(
    content_diff_list: QListWidget,
    format_diff_list: QListWidget,
    diff_tab_widget: QTabWidget,
    ops: list[Any],
    *,
    compare_vm: CompareViewModel | None,
    fallback_quality_warnings: list[str],
    fallback_quality_scores: dict[str, int] | None,
    field_note: str,
    field_diffs: list[FieldDiff] | None,
    pure_content_mode: bool,
) -> tuple[str, Any] | None:
    content_diff_list.clear()
    format_diff_list.clear()

    content_ops = [op for op in ops if op.type != DiffOpType.FORMAT_CHANGE]
    format_ops = [op for op in ops if op.type == DiffOpType.FORMAT_CHANGE]

    if compare_vm is not None:
        quality_warnings = list(compare_vm.warnings or [])
        quality_scores = compare_vm.quality_scores
    else:
        quality_warnings = list(fallback_quality_warnings)
        quality_scores = fallback_quality_scores

    if quality_scores:
        left_score = quality_scores.get("left", 0)
        right_score = quality_scores.get("right", 0)
        score_item = QListWidgetItem(f"[i] text quality: left {left_score}/100, right {right_score}/100")
        score_item.setFlags(score_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        score_item.setForeground(QColor("#7f8c8d"))
        content_diff_list.addItem(score_item)

    if quality_warnings:
        warning_header = QListWidgetItem("[!] quality warnings")
        warning_header.setFlags(warning_header.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        warning_header.setForeground(QColor("#c0392b"))
        warning_header.setBackground(QColor("#fdf2f2"))
        content_diff_list.addItem(warning_header)

        for warning in quality_warnings:
            warning_item = QListWidgetItem(f"    {warning}")
            warning_item.setFlags(warning_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            warning_item.setForeground(QColor("#c0392b"))
            content_diff_list.addItem(warning_item)

        separator = QListWidgetItem("-" * 30)
        separator.setFlags(separator.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        separator.setForeground(QColor("#bdc3c7"))
        content_diff_list.addItem(separator)

    if compare_vm is not None:
        if compare_vm.decision_basis or compare_vm.gate_reason or compare_vm.fallback_reason:
            basis = compare_vm.decision_basis or "text"
            meta_note = f"[i] basis={basis}"
            if compare_vm.gate_reason:
                meta_note += f" | gate={compare_vm.gate_reason}"
            if compare_vm.fallback_reason:
                meta_note += f" | fallback={compare_vm.fallback_reason}"
            note_item = QListWidgetItem(meta_note)
            note_item.setFlags(note_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            note_item.setForeground(QColor("#7f8c8d"))
            content_diff_list.addItem(note_item)

    if field_note:
        note_item = QListWidgetItem(f"[i] {field_note}")
        note_item.setFlags(note_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        note_item.setForeground(QColor("#7f8c8d"))
        content_diff_list.addItem(note_item)

    field_match: list[FieldDiff] = []
    if field_diffs:
        field_non_match = [d for d in field_diffs if d.diff_type != "match"]
        field_match = [d for d in field_diffs if d.diff_type == "match"]

        if field_non_match:
            header_item = QListWidgetItem("field differences")
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            header_item.setForeground(QColor("#7f8c8d"))
            content_diff_list.addItem(header_item)

            for fd in field_non_match:
                item = QListWidgetItem(describe_field_diff(fd))
                item.setData(Qt.ItemDataRole.UserRole, ("field_diff", fd))
                content_diff_list.addItem(item)

        if field_match:
            header_item = QListWidgetItem("field matches")
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            header_item.setForeground(QColor("#27ae60"))
            content_diff_list.addItem(header_item)

            for fd in field_match:
                item = QListWidgetItem(f"[ok] {fd.field_name}: matched")
                item.setForeground(QColor("#27ae60"))
                item.setData(Qt.ItemDataRole.UserRole, ("field_match", fd))
                content_diff_list.addItem(item)

        if content_ops:
            header_item = QListWidgetItem("character differences")
            header_item.setFlags(header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            header_item.setForeground(QColor("#7f8c8d"))
            content_diff_list.addItem(header_item)

    total_content_diffs = len(content_ops) + (len(field_diffs) - len(field_match) if field_diffs else 0)
    diff_tab_widget.setTabText(0, f"content ({total_content_diffs})")

    if pure_content_mode:
        diff_tab_widget.setTabText(1, "format (disabled)")
        mode_hint = QListWidgetItem("[i] pure content mode; format differences are disabled")
        mode_hint.setFlags(mode_hint.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        mode_hint.setForeground(QColor("#7f8c8d"))
        format_diff_list.addItem(mode_hint)
    else:
        diff_tab_widget.setTabText(1, f"format ({len(format_ops)})")

    has_non_match_field_diffs = bool(field_diffs and len(field_diffs) > len(field_match))
    if not content_ops and not has_non_match_field_diffs:
        if not content_diff_list.count():
            item = QListWidgetItem("no content differences")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
            content_diff_list.addItem(item)
    else:
        for op in content_ops:
            item = QListWidgetItem(describe_op(op))
            item.setData(Qt.ItemDataRole.UserRole, ("char_diff", op))
            content_diff_list.addItem(item)

    if pure_content_mode:
        pass
    elif not format_ops:
        item = QListWidgetItem("no format differences")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable & ~Qt.ItemFlag.ItemIsEnabled)
        format_diff_list.addItem(item)
    else:
        for op in format_ops:
            item = QListWidgetItem(describe_op(op))
            item.setData(Qt.ItemDataRole.UserRole, ("format_diff", op))
            format_diff_list.addItem(item)

    if content_diff_list.count() > 0:
        for row in range(content_diff_list.count()):
            item = content_diff_list.item(row)
            first_data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(first_data, tuple) and len(first_data) == 2:
                content_diff_list.setCurrentRow(row)
                return first_data[0], first_data[1]
        content_diff_list.setCurrentRow(0)
        return None
    if format_ops:
        format_diff_list.setCurrentRow(0)
        return "format_diff", format_ops[0]
    return None
