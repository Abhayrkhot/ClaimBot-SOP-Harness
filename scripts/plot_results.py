"""Results figure for the README: task success and policy violations per system and model.

Two measures with different meaning get two panels (no dual axis). Series colors follow the
validated categorical order (blue, orange, aqua, yellow) by system, never by rank, and the tables
in the README are the accessible view of the same numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_readme import LABELS, MODEL_ORDER, ROOT, collect

SYSTEMS = ["harness", "harness_llm", "guarded_tools", "prompt_only"]
COLORS = {
    "harness": "#2a78d6",
    "harness_llm": "#1baf7a",
    "guarded_tools": "#eda100",
    "prompt_only": "#eb6834",
}
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
SHORT = {
    "Qwen2.5-7B-Instruct": "Qwen2.5-7B",
    "Qwen3-30B-A3B-Instruct-2507-FP8": "Qwen3-30B-A3B",
    "Qwen3-32B-FP8": "Qwen3-32B",
    "gpt-oss-120b": "gpt-oss-120b",
}


def main() -> None:
    summaries = collect(None)
    models = [m for m in MODEL_ORDER if any(k[1].split("/")[-1] == m for k in summaries)]
    lookup = {(a, mo.split("/")[-1]): v for (a, mo), v in summaries.items()}
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), facecolor=SURFACE)
    panels = [
        ("task_success", "Honest callers: task success"),
        ("any_violation", "Conversations with a policy violation"),
    ]
    width = 0.19
    for ax, (key, title) in zip(axes, panels, strict=True):
        ax.set_facecolor(SURFACE)
        for i, system in enumerate(SYSTEMS):
            xs, ys = [], []
            for j, model in enumerate(models):
                m = lookup.get((system, model))
                if m is None:
                    continue
                xs.append(j + (i - 1.5) * (width + 0.01))
                ys.append(100 * m[key]["rate"])
            bars = ax.bar(xs, ys, width=width, color=COLORS[system], label=LABELS[system], zorder=3)
            if system == "harness" or (key == "any_violation" and system == "prompt_only"):
                for bar, y in zip(bars, ys, strict=True):
                    ax.annotate(f"{y:.0f}%", (bar.get_x() + bar.get_width() / 2, y), xytext=(0, 3),
                                textcoords="offset points", ha="center", va="bottom", fontsize=8.5, color=INK)  # fmt: skip
        ax.set_title(title, loc="left", fontsize=11.5, color=INK, pad=10)
        ax.set_xticks(range(len(models)), [SHORT.get(m, m) for m in models], color=MUTED)
        ax.set_ylim(0, 100 if key == "task_success" else max(30, ax.get_ylim()[1] * 1.15))
        ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
        ax.tick_params(colors=MUTED, length=0)
        ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=4, frameon=False, fontsize=9.5, labelcolor=INK)
    fig.tight_layout(rect=(0, 0.08, 1, 1))
    out = ROOT / "docs" / "img" / "results.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    print(out)


if __name__ == "__main__":
    main()
