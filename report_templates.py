# -*- coding: utf-8 -*-
"""Fixed Vietnamese report templates; no AI or free-form inference."""
from math import isfinite


REPORT_TEMPLATE_VERSION = "1.0.0"


def _fmt(value, digits=2):
    if value is None or isinstance(value, bool):
        return "Chưa có dữ liệu"
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return "Chưa có dữ liệu"
    if not isfinite(number):
        return "Chưa có dữ liệu"
    return f"{number:,.{digits}f}"


def render_analysis_report(decision, scoring_result=None):
    """Render a concise, reproducible Vietnamese report from engine outputs."""
    decision = decision if isinstance(decision, dict) else {}
    scoring = scoring_result if isinstance(scoring_result, dict) else {}
    status = decision.get("status") or "unknown"
    status_labels = {
        "analysis_ready": "Đủ điều kiện phân tích định lượng",
        "insufficient_data": "Chưa đủ dữ liệu để kết luận",
        "score_available_valuation_unavailable": "Có điểm nền tảng, chưa đủ dữ liệu định giá",
    }
    lines = [
        "BÁO CÁO PHÂN TÍCH ĐỊNH LƯỢNG",
        "Trạng thái: " + status_labels.get(status, "Trạng thái chưa xác định"),
        "Điểm nền tảng: " + _fmt(decision.get("score"), 2),
        "Độ phủ dữ liệu: " + _fmt(decision.get("coverage_pct"), 2) + "%",
    ]

    comparison = decision.get("valuation_comparison") or {}
    if comparison.get("available"):
        lines.extend([
            "Phương pháp định giá: " + str(decision.get("valuation_method") or "Không xác định"),
            "Giá trị mô hình/cổ phiếu: " + _fmt(comparison.get("fair_value_per_share"), 2),
            "Giá thị trường: " + _fmt(comparison.get("market_price"), 2),
            "Chênh lệch so với giá trị mô hình: " + _fmt(comparison.get("discount_pct"), 2) + "%",
        ])
    else:
        lines.append("Định giá: Chưa đủ dữ liệu hoặc giả định được xác minh.")

    blockers = decision.get("blocking_reasons") or []
    if blockers:
        lines.append("Điều kiện đang chặn:")
        lines.extend("- " + str(item) for item in blockers)
    missing = []
    for metric_id, result in (scoring.get("metric_results") or {}).items():
        if not result.get("available"):
            missing.append(str(metric_id) + ": " + str(result.get("reason") or "không khả dụng"))
    if missing:
        lines.append("Chỉ tiêu chưa khả dụng:")
        lines.extend("- " + item for item in sorted(missing))

    lines.append(
        "Lưu ý: Đây là kết quả phân tích định lượng theo dữ liệu và giả định đã khai báo; "
        "không phải chỉ dẫn mua, bán hoặc nắm giữ."
    )
    return {
        "template_version": REPORT_TEMPLATE_VERSION,
        "status": status,
        "lines": lines,
        "text": "\n".join(lines),
    }
