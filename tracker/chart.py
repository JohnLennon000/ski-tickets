"""Картинка для утренней сводки: лучшая цена по каждому курорту за последние недели."""
from __future__ import annotations

import datetime as dt
import io
import math

from tracker.config import Config
from tracker.store import BestRow
from tracker.util import MONTHS_SHORT, MSK, human_day, num, rub

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
ACCENT = "#2a78d6"

DAYS = 30


def _thousands(value: float, _pos: int | None = None) -> str:
    if value >= 1000:
        text = f"{value / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"{text} тыс."
    return f"{value:.0f}"


def _day_label(value: float, _pos: int | None = None) -> str:
    from matplotlib import dates as mdates

    day = mdates.num2date(value)
    return f"{day.day} {MONTHS_SHORT[day.month - 1]}"


def digest_chart(cfg: Config, history: list[BestRow], now: dt.datetime) -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import dates as mdates
    from matplotlib import pyplot as plt
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    since = now - dt.timedelta(days=DAYS)
    resorts = list(cfg.resorts)
    cols = 2 if len(resorts) > 1 else 1
    rows = math.ceil(len(resorts) / cols)

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, axes = plt.subplots(rows, cols, figsize=(9, 2.45 * rows + 1.0), dpi=130, squeeze=False)
    fig.patch.set_facecolor(SURFACE)
    fig.text(0.035, 0.985, "Лучшая цена туда-обратно с багажом", ha="left", va="top",
             fontsize=14, fontweight="bold", color=INK)
    fig.text(0.035, 0.985 - 0.32 / (2.45 * rows + 1.0) * 1.6,
             f"по проверкам за {DAYS} дней · {human_day(now.astimezone(MSK).date())}",
             ha="left", va="top", fontsize=10, color=INK_2)

    for index, ax in enumerate(axes.flat):
        if index >= len(resorts):
            ax.set_visible(False)
            continue
        resort = resorts[index]
        points = [
            (r.time.astimezone(MSK).replace(tzinfo=None), r.total)
            for r in history
            if r.resort == resort.id and r.total and r.time >= since
        ]
        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
        ax.tick_params(colors=MUTED, labelsize=8.5, length=0)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.set_title(resort.name, loc="left", fontsize=11.5, fontweight="bold", color=INK, pad=8)

        if not points:
            ax.set_yticks([])
            ax.set_xticks([])
            ax.text(0.5, 0.5, "нет цен на прямые рейсы", transform=ax.transAxes,
                    ha="center", va="center", color=MUTED, fontsize=10)
            continue

        times = [p[0] for p in points]
        values = [p[1] for p in points]
        ax.set_title(rub(values[-1]), loc="right", fontsize=11.5, color=INK, pad=8)

        low, high = min(values), max(values)
        threshold = resort.threshold
        if threshold and threshold >= low * 0.7:
            low, high = min(low, threshold), max(high, threshold)
        pad = max((high - low) * 0.18, high * 0.03)
        ax.set_ylim(low - pad, high + pad)

        if len(points) == 1:
            ax.set_xlim(times[0] - dt.timedelta(hours=12), times[0] + dt.timedelta(hours=12))
        ax.plot(times, values, color=ACCENT, linewidth=2, solid_capstyle="round", solid_joinstyle="round",
                drawstyle="steps-post" if len(points) > 1 else "default")
        ax.scatter([times[-1]], [values[-1]], s=46, color=ACCENT, edgecolors=SURFACE, linewidths=2, zorder=3)

        min_value = min(values)
        if min_value < values[-1]:
            idx = values.index(min_value)
            ax.scatter([times[idx]], [min_value], s=30, color=MUTED, edgecolors=SURFACE, linewidths=2, zorder=3)
            ax.annotate(f"мин {num(min_value)}", (times[idx], min_value), xytext=(0, -13),
                        textcoords="offset points", ha="center", fontsize=8, color=INK_2)

        if threshold:
            ymin, ymax = ax.get_ylim()
            if ymin <= threshold <= ymax:
                ax.axhline(threshold, color=MUTED, linewidth=1, dashes=(4, 3), zorder=1)
                ax.annotate(f"порог {num(threshold)}", (1, threshold), xycoords=("axes fraction", "data"),
                            xytext=(0, 3), textcoords="offset points", ha="right", va="bottom",
                            fontsize=8, color=INK_2)
            else:
                ax.text(1, 0.02, f"порог {num(threshold)} ниже графика", transform=ax.transAxes,
                        ha="right", va="bottom", fontsize=8, color=INK_2)

        ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
        ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2, maxticks=4))
        ax.xaxis.set_major_formatter(FuncFormatter(_day_label))

    fig.tight_layout(rect=(0.02, 0.0, 0.98, 1 - 0.75 / (2.45 * rows + 1.0)), h_pad=2.2, w_pad=2.5)
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    plt.close(fig)
    return buffer.getvalue()
