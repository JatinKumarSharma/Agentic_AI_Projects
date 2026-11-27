"""Deterministic visualization helpers for tool-returned data.

The renderer never invents values. It accepts data that a data tool has
already returned, reduces it to labels and numbers, and emits a compact chart
specification plus a text fallback. The web UI renders the specification as
SVG; other clients can use the text form.
"""
import math
import re

from datetime import datetime, timezone

_ALLOWED_TYPES = ("line", "bar", "pie", "table")
_SPARK = "▁▂▃▄▅▆▇█"
_MAX_POINTS = 60


def _retrieved_at():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _clean_label(value, limit=28):
    text = re.sub(r"\s+", " ", str(value if value not in (None, "") else "—")).strip()
    return text[:limit]


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 4)


def _records_from(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("prices", "observations", "facts", "rows", "results", "data"):
        value = data.get(key)
        if isinstance(value, list) and value:
            return value
    if "labels" in data and "values" in data:
        labels = data.get("labels") or []
        values = data.get("values") or []
        return [
            {"label": label, "value": value}
            for label, value in zip(labels, values)
        ]
    return [data]


def _extract_series(data, x_key="date", y_key="close"):
    labels = []
    values = []
    for record in _records_from(data):
        if not isinstance(record, dict):
            continue
        label = (
            record.get(x_key)
            or record.get("label")
            or record.get("date")
            or record.get("symbol")
            or record.get("end")
        )
        value = (
            record.get(y_key)
            if y_key in record
            else record.get("value")
            if "value" in record
            else record.get("close")
            if "close" in record
            else None
        )
        number = _number(value)
        if number is None:
            continue
        labels.append(_clean_label(label))
        values.append(number)
    return labels[:_MAX_POINTS], values[:_MAX_POINTS]


def _failure(error_type, message, **extra):
    result = {
        "success": False,
        "source": "Chart renderer",
        "error_type": error_type,
        "message": message,
    }
    result.update(extra)
    return result


def _sparkline(values):
    if not values:
        return ""
    low, high = min(values), max(values)
    if high == low:
        return _SPARK[3] * len(values)
    return "".join(
        _SPARK[min(len(_SPARK) - 1, int((value - low) / (high - low) * (len(_SPARK) - 1)))]
        for value in values
    )


def _bar_text(labels, values, limit=12):
    if not values:
        return ""
    peak = max(abs(value) for value in values) or 1
    lines = []
    for label, value in list(zip(labels, values))[:limit]:
        width = max(1, int(round(abs(value) / peak * 24)))
        lines.append(f"{label:<20} {'█' * width} {value:g}")
    return "\n".join(lines)


def _pie_text(labels, values, limit=12):
    total = sum(values)
    if total <= 0:
        return ""
    lines = []
    for label, value in list(zip(labels, values))[:limit]:
        pct = value / total * 100
        width = max(1, int(round(pct / 5)))
        lines.append(f"{label:<20} {'█' * width} {pct:.1f}%")
    return "\n".join(lines)


def _table_text(labels, values, x_label, y_label, limit=12):
    lines = [f"| {x_label} | {y_label} |", "|---|---:|"]
    for label, value in list(zip(labels, values))[:limit]:
        lines.append(f"| {label} | {value:g} |")
    return "\n".join(lines)


def _mermaid(title, chart_type, labels, values, y_label):
    safe_title = title.replace('"', "'")
    if chart_type == "pie":
        lines = ["pie showData", f'  title "{safe_title}"']
        for label, value in zip(labels, values):
            safe = label.replace('"', "'")
            lines.append(f'  "{safe}" : {value:g}')
        return "\n".join(lines)

    joined_labels = ", ".join(f'"{label.replace(chr(34), chr(39))}"' for label in labels)
    joined_values = ", ".join(f"{value:g}" for value in values)
    marker = "bar" if chart_type == "bar" else "line"
    low, high = min(values), max(values)
    if low == high:
        low -= 1
        high += 1
    return "\n".join(
        [
            "xychart-beta",
            f'  title "{safe_title}"',
            f"  x-axis [{joined_labels}]",
            f'  y-axis "{y_label}" {low:g} --> {high:g}',
            f"  {marker} [{joined_values}]",
        ]
    )


def render_chart(
    data,
    chart_type: str = "line",
    title: str = "",
    x_key: str = "date",
    y_key: str = "close",
):
    """Build a chart specification from an already-retrieved data result."""
    requested = str(chart_type or "line").strip().lower()
    if requested not in _ALLOWED_TYPES:
        return _failure(
            "unsupported_chart_type",
            f"Unsupported chart type '{chart_type}'. Supported: {', '.join(_ALLOWED_TYPES)}.",
        )

    labels, values = _extract_series(data, x_key=x_key, y_key=y_key)
    if not values:
        return _failure(
            "no_numeric_data",
            "The supplied result contains no numeric values that can be plotted.",
        )

    if requested == "pie" and (min(values) < 0 or sum(values) <= 0):
        return _failure(
            "unsupported_values",
            "Pie charts require non-negative values with a positive total. Use a bar or line chart instead.",
        )

    resolved_title = str(title or "").strip() or f"{y_key} by {x_key}"
    if requested == "pie":
        ascii_text = _pie_text(labels, values)
    elif requested == "bar":
        ascii_text = _bar_text(labels, values)
    elif requested == "table":
        ascii_text = _table_text(labels, values, x_key, y_key)
    else:
        ascii_text = (
            f"{_sparkline(values)}\n"
            f"min {min(values):g} · max {max(values):g} · last {values[-1]:g}"
        )

    return {
        "success": True,
        "source": "Chart renderer",
        "source_type": "derived_visualization",
        "retrieved_at": _retrieved_at(),
        "chart_type": requested,
        # The web client consumes `type`; keep both keys so older cached
        # specs and API clients that read `chart_type` still render correctly.
        "type": requested,
        "title": resolved_title,
        "x_label": x_key,
        "y_label": y_key,
        "labels": labels,
        "values": values,
        "ascii": ascii_text,
        "mermaid": _mermaid(resolved_title, requested, labels, values, y_key),
        "derived_from": "tool_result",
    }
