"""Terminal rendering for the airtel CLI: gauges, sparklines and dashboards.

Pure presentation — nothing here talks to the router. Everything takes the
raw `sys_info()` dict (string values, "" / "-" when absent) plus optional
time series, and returns `rich` renderables.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from rich import box
from rich.console import Console, Group, RenderableType
from rich.constrain import Constrain
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()
err_console = Console(stderr=True)

MAX_WIDTH = 100
_SPARK = "▁▂▃▄▅▆▇█"
GAUGE_CELLS = 22

# quality index -> (label, style)
QUALITY = [
    ("Poor", "red"),
    ("Fair", "yellow"),
    ("Good", "green3"),
    ("Excellent", "bold green"),
]


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str
    lo: float  # gauge empty
    hi: float  # gauge full
    cuts: tuple  # lower bounds for Fair, Good, Excellent
    hint: str


# Rating bands follow the common LTE thresholds used by router vendors.
METRICS = [
    Metric("RSRP", "RSRP", "dBm", -130, -70, (-100, -90, -80), "signal strength"),
    Metric("RSRQ", "RSRQ", "dB", -20, -3, (-12, -9, -5), "signal quality"),
    Metric("SINR", "SINR", "dB", -5, 30, (0, 13, 20), "signal vs. noise"),
    Metric("RSSI", "RSSI", "dBm", -115, -50, (-85, -75, -65), "total power"),
]


def num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rate(value: float, m: Metric) -> int:
    """0=Poor .. 3=Excellent."""
    return sum(value >= c for c in m.cuts)


def sparkline(values: Sequence[Optional[float]], width: int, style: str = "cyan") -> Text:
    """Unicode sparkline of the last `width` values; None renders as a red gap."""
    vals = list(values)[-width:]
    present = [v for v in vals if v is not None]
    out = Text()
    if not present:
        return Text()
    lo, hi = min(present), max(present)
    span = hi - lo
    for v in vals:
        if v is None:
            out.append("×", style="red")
        elif span == 0:
            out.append(_SPARK[3], style=style)
        else:
            out.append(_SPARK[round((v - lo) / span * 7)], style=style)
    return out


def gauge(value: Optional[float], m: Metric, cells: int = GAUGE_CELLS) -> Text:
    if value is None:
        return Text("░" * cells, style="grey37")
    frac = min(max((value - m.lo) / (m.hi - m.lo), 0.0), 1.0)
    filled = max(1, round(frac * cells)) if frac > 0 else 0
    style = QUALITY[rate(value, m)][1]
    t = Text()
    t.append("█" * filled, style=style)
    t.append("░" * (cells - filled), style="grey37")
    return t


def human_bytes(kb: Optional[float]) -> str:
    """Router counters are reported in KB."""
    if kb is None:
        return "—"
    units = ["KB", "MB", "GB", "TB"]
    v, i = float(kb), 0
    while v >= 1024 and i < len(units) - 1:
        v /= 1024
        i += 1
    return f"{v:,.1f} {units[i]}" if i else f"{v:,.0f} KB"


def human_rate(kb_s: Optional[float]) -> str:
    if kb_s is None:
        return "—"
    if kb_s >= 1024:
        return f"{kb_s / 1024:.2f} MB/s"
    return f"{kb_s:.1f} KB/s"


def human_duration(seconds: float) -> str:
    s = int(max(seconds, 0))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = [f"{d}d"] if d else []
    if h or d:
        parts.append(f"{h}h")
    if m or h or d:
        parts.append(f"{m}m")
    if not (d or h):
        parts.append(f"{s}s")
    return " ".join(parts)


def _online_seconds(info: Mapping[str, Any]) -> Optional[float]:
    # onlineDuration is "days-hours-minutes-seconds", e.g. "0-1-25-31"
    try:
        d, h, m, s = (int(x) for x in str(info.get("onlineDuration", "")).split("-"))
    except ValueError:
        return None
    return d * 86400 + h * 3600 + m * 60 + s


def _bars(level: Optional[float]) -> Text:
    lvl = int(level) if level is not None else 0
    t = Text()
    for i, ch in enumerate("▂▃▅▆█", start=1):
        t.append(ch, style="green" if i <= lvl and lvl >= 3 else "yellow" if i <= lvl and lvl == 2 else "red" if i <= lvl else "grey37")
    return t


def _dash(v: Any) -> str:
    s = str(v).strip() if v is not None else ""
    return s if s and s != "-" else "—"


def overall_quality(info: Mapping[str, Any]) -> Optional[int]:
    """Worst of RSRP and SINR — the two that decide whether a link is usable."""
    scores = []
    for m in METRICS:
        if m.key in ("RSRP", "SINR"):
            v = num(info.get(m.key))
            if v is not None:
                scores.append(rate(v, m))
    return min(scores) if scores else None


_VERDICTS = [
    "Expect drops, retransmits and stalls that a router reboot cannot fix — "
    "repositioning the unit or adding an external antenna will help more than firmware.",
    "Usable, but throughput will sag and stalls are plausible under load.",
    "Stalls are unlikely to be radio-related.",
    "Any stalls are unlikely to be radio-related.",
]


# --------------------------------------------------------------------- panels


def _panel(body: RenderableType, title: str, subtitle: Optional[str] = None) -> Panel:
    return Panel(
        body,
        title=Text(title, style="bold"),
        subtitle=subtitle,
        title_align="left",
        subtitle_align="right",
        border_style="grey50",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def header_panel(
    info: Mapping[str, Any],
    daemon: Optional[Mapping[str, Any]],
    online: Optional[bool] = None,
) -> Panel:
    net = _dash(info.get("network_type_str"))
    band = _dash(info.get("currentband"))
    up = _online_seconds(info)

    left = Text()
    left.append(_dash(info.get("network_operator")), style="bold")
    left.append(f"  {net}", style="bold cyan")
    left.append(f"  · Band {band}", style="dim")
    left.append("   ")
    left.append_text(_bars(num(info.get("signal_lvl"))))

    right = Text(justify="right")
    if online is None:
        right.append("● ", style="grey50")
        right.append("link up" if up is not None else "—", style="dim")
    elif online:
        right.append("● ", style="green")
        right.append("ONLINE", style="bold green")
    else:
        right.append("● ", style="red")
        right.append("NO INTERNET", style="bold red")
    if up is not None:
        right.append(f"  up {human_duration(up)}", style="dim")

    g = Table.grid(expand=True)
    g.add_column(ratio=1)
    g.add_column(justify="right")
    g.add_row(left, right)

    mon = Text()
    if daemon and daemon.get("running"):
        mon.append("● ", style="green")
        mon.append("watchdog running", style="green")
        mon.append(f"  pid {daemon['pid']} · {human_duration(daemon['uptime'])}", style="dim")
        last = daemon.get("last_event")
        if last:
            mon.append(f"  · last recovery: {last['what']} {human_duration(time.time() - last['t'])} ago", style="dim")
    else:
        mon.append("○ ", style="grey50")
        mon.append("watchdog stopped", style="dim")
        mon.append("  run `airtel` to start", style="grey50")
    g.add_row(mon, Text(""))
    return _panel(g, "AIRTEL ZLT X25", None)


def signal_panel(
    info: Mapping[str, Any],
    series: Optional[Mapping[str, Sequence[Optional[float]]]] = None,
    span_label: str = "",
    width: int = MAX_WIDTH,
) -> Panel:
    series = series or {}
    show_spark = width >= 86
    g = Table.grid(padding=(0, 2))
    g.add_column(no_wrap=True)  # label
    g.add_column(no_wrap=True)  # gauge
    g.add_column(no_wrap=True, justify="right")  # value
    g.add_column(no_wrap=True)  # rating
    if show_spark:
        g.add_column(no_wrap=True)  # spark

    for m in METRICS:
        v = num(info.get(m.key))
        q = QUALITY[rate(v, m)] if v is not None else ("n/a", "grey50")
        value = Text()
        if v is not None:
            value.append(f"{v:g}", style="bold")
            value.append(f" {m.unit}", style="dim")
        else:
            value.append("—", style="dim")
        row: list[RenderableType] = [
            Text(m.label, style="bold"),
            gauge(v, m),
            value,
            Text(q[0], style=q[1]),
        ]
        if show_spark:
            s = series.get(m.key, [])
            row.append(sparkline(s, 24) if len(s) > 1 else Text(""))
        g.add_row(*row)

    parts: list[RenderableType] = [g]
    oq = overall_quality(info)
    if oq is not None:
        name, style = QUALITY[oq]
        v = Text()
        v.append("\n")
        v.append(f"{name} link. ", style=style)
        v.append(_VERDICTS[oq], style="dim")
        parts.append(v)
    return _panel(Group(*parts), "Signal", span_label or None)


def connection_panel(info: Mapping[str, Any]) -> Panel:
    bw = _dash(info.get("bandwidth"))
    cell = [
        ("Operator", f"{_dash(info.get('network_operator'))} ({_dash(info.get('PLMN'))})"),
        ("Cell ID", _dash(info.get("CELL_ID"))),
        ("eNodeB", _dash(info.get("ENODEBID"))),
        ("PCI", _dash(info.get("PCI"))),
        ("EARFCN", _dash(info.get("FREQ"))),
        ("Bandwidth", f"{bw} MHz" if bw != "—" else bw),
    ]
    radio = [
        ("CQI", _dash(info.get("CQI"))),
        ("MCS ↓ / ↑", f"{_dash(info.get('dl_mcs'))} / {_dash(info.get('ul_mcs'))}"),
        ("Max QAM", f"{_dash(info.get('max_dl_qam'))} ↓ · {_dash(info.get('max_ul_qam'))} ↑"),
        ("MIMO rank", _dash(info.get("rank_4g"))),
        ("BLER", f"{_dash(info.get('bler_4g'))} %"),
        ("TAC", _dash(info.get("tac_4g"))),
    ]
    g = Table.grid(padding=(0, 2))
    for _ in range(5):
        g.add_column(no_wrap=True)
    g.columns[1].min_width = 26
    g.columns[2].min_width = 2
    for (l1, v1), (l2, v2) in zip(cell, radio):
        g.add_row(Text(l1, style="dim"), Text(v1), Text(""), Text(l2, style="dim"), Text(v2))
    return _panel(g, "Connection")


def traffic_panel(
    info: Mapping[str, Any],
    dl_rates: Optional[Sequence[Optional[float]]] = None,
    ul_rates: Optional[Sequence[Optional[float]]] = None,
    width: int = MAX_WIDTH,
) -> Panel:
    g = Table.grid(padding=(0, 2))
    for _ in range(4):
        g.add_column(no_wrap=True)
    spark_w = max(10, min(40, width - 56))

    def line(label: str, arrow: str, style: str, rates: Optional[Sequence[Optional[float]]], total: Any) -> None:
        cur = rates[-1] if rates else None
        spark = sparkline(rates, spark_w, style) if rates and len(rates) > 1 else Text("")
        g.add_row(
            Text(f"{arrow} {label}", style=f"bold {style}"),
            Text(human_rate(cur), style="bold") if rates is not None else Text(""),
            spark,
            Text(f"{human_bytes(num(total))} this session", style="dim"),
        )

    line("Download", "↓", "cyan", dl_rates, info.get("flow_dl"))
    line("Upload", "↑", "magenta", ul_rates, info.get("flow_ul"))
    total = num(info.get("mon_total_flow"))
    foot = Text(f"\nRouter total counter: {human_bytes(total)}", style="dim")
    return _panel(Group(g, foot), "Traffic")


def latency_panel(
    rtts: Sequence[Optional[float]],
    target: str,
    span_label: str = "",
    width: int = MAX_WIDTH,
) -> Panel:
    got = [r for r in rtts if r is not None]
    n = len(rtts)
    loss = 100 * (n - len(got)) / n if n else 0.0
    g = Table.grid(padding=(0, 2))
    for _ in range(2):
        g.add_column(no_wrap=True)
    spark_w = max(10, min(60, width - 8))

    stats = Text()
    if got:
        avg = sum(got) / len(got)
        jitter = (sum((r - avg) ** 2 for r in got) / len(got)) ** 0.5
        stats.append(f"{avg:.0f} ms", style="bold")
        stats.append(" avg   ", style="dim")
        stats.append(f"{min(got):.0f}", style="bold")
        stats.append(" min   ", style="dim")
        stats.append(f"{max(got):.0f}", style="bold")
        stats.append(" max   ", style="dim")
        stats.append(f"±{jitter:.0f} ms", style="bold")
        stats.append(" jitter   ", style="dim")
    else:
        stats.append("no replies", style="bold red")
        stats.append("   ")
    stats.append(f"{loss:.0f}%", style="bold red" if loss else "bold green")
    stats.append(" loss", style="dim")

    body = Group(stats, sparkline(rtts, spark_w, "cyan") if len(rtts) > 1 else Text(""))
    return _panel(body, f"Latency → {target}", span_label or None)


# ----------------------------------------------------------------- dashboards


def dashboard(
    info: Mapping[str, Any],
    *,
    daemon: Optional[Mapping[str, Any]] = None,
    signal_series: Optional[Mapping[str, Sequence[Optional[float]]]] = None,
    signal_span: str = "",
    rtts: Optional[Sequence[Optional[float]]] = None,
    rtt_span: str = "",
    ping_target: str = "",
    dl_rates: Optional[Sequence[Optional[float]]] = None,
    ul_rates: Optional[Sequence[Optional[float]]] = None,
    online: Optional[bool] = None,
    footer: Optional[str] = None,
    width: Optional[int] = None,
) -> RenderableType:
    width = min(width or console.width, MAX_WIDTH)
    panels: list[RenderableType] = [
        header_panel(info, daemon, online),
        signal_panel(info, signal_series, signal_span, width),
        connection_panel(info),
    ]
    if rtts:
        panels.append(latency_panel(rtts, ping_target, rtt_span, width))
    panels.append(traffic_panel(info, dl_rates, ul_rates, width))
    if footer:
        panels.append(Text(footer, style="dim"))
    return Constrain(Group(*panels), width)


def ok(msg: str) -> None:
    console.print(f"[green]✓[/green] {msg}")


def fail(msg: str) -> None:
    err_console.print(f"[red]✗[/red] {msg}")


def info_line(msg: str) -> None:
    console.print(f"[cyan]›[/cyan] {msg}")
