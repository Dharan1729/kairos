"""Figures for the README and docs/benchmarks.md. Every plotted number is read
from a committed file in results/; nothing is typed in by hand.

    pip install matplotlib
    python -m benchmarks.plots            # writes results/figures/*.png
"""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

RESULTS = Path(__file__).resolve().parent.parent / "results"
OUT = RESULTS / "figures"

# Validated categorical slots (light surface). Tables in docs/benchmarks.md
# carry the same numbers for readers who cannot rely on colour.
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA, YELLOW, MAGENTA = "#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"

plt.rcParams.update(
    {
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.size": 11,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_2,
        "xtick.color": INK_2,
        "ytick.color": INK_2,
        "text.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.titleweight": "bold",
        "axes.titlesize": 12,
        "axes.titlelocation": "left",
    }
)


def _load(path: str) -> dict:
    return json.loads((RESULTS / path).read_text())


def _grid(ax, axis: str = "y") -> None:
    ax.grid(axis=axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _save(fig, name: str, note: str) -> None:
    fig.text(0.01, -0.02, note, fontsize=8.5, color=INK_2, ha="left", va="top")
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print("wrote", OUT / name)


def leak_probe() -> None:
    old = _load("leak/leak_probe_v0.1.0.json")["summary"]
    new = _load("leak/leak_probe_v0.2.0.json")["summary"]
    panels = [
        ("Attack requests that got a forbidden item", "leak_requests_with_forbidden_items", "leak_requests"),
        ("Edit / expiry scenarios served stale", "staleness_scenarios_serving_stale", "staleness_scenarios"),
    ]
    versions = [("0.1.0", old, ORANGE), ("0.2.0", new, BLUE)]

    fig, axes = plt.subplots(1, 2, figsize=(10, 2.8))
    for ax, (title, key, total_key) in zip(axes, panels, strict=True):
        total = old[total_key]
        for y, (_version, data, color) in enumerate(versions):
            n = data[key]
            ax.barh(y, n, color=color, height=0.55, edgecolor=SURFACE, linewidth=2)
            ax.text(n + total * 0.02, y, f"{n} of {total}", va="center", fontsize=10, color=INK)
        ax.set_yticks(range(len(versions)), [f"Kairos {v}" for v, _, _ in versions])
        ax.invert_yaxis()
        ax.set_xlim(0, total * 1.25)
        ax.set_title(title)
        _grid(ax, "x")
    fig.suptitle(
        "The same attack probe, run against the old and the new release",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    _save(
        fig,
        "fig1_leak_probe.png",
        "Source: results/leak/leak_probe_v0.1.0.json, leak_probe_v0.2.0.json · public HTTP API only · "
        f"0.1.0 also returned HTTP {old['fresh_request_after_expiry_status']} after an item expired",
    )


def selection_quality() -> None:
    d = _load("selection_quality.json")["results"]
    budgets = sorted(int(b) for b in d["kairos"])
    lines = [
        ("Kairos (default ranker)", "kairos", BLUE, "o"),
        ("vector top-k", "vector_topk", ORANGE, "s"),
        ("newest first", "recency", AQUA, "^"),
        ("layers in order", "dump_layers", YELLOW, "D"),
    ]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10.5, 3.9), gridspec_kw={"width_ratios": [1.35, 1]})
    for name, key, color, marker in lines:
        ys = [d[key][str(b)]["recall"] for b in budgets]
        ax.plot(
            budgets,
            ys,
            color=color,
            linewidth=2,
            marker=marker,
            markersize=7,
            markeredgecolor=SURFACE,
            markeredgewidth=1.5,
            label=name,
        )
    ax.set_xticks(budgets)
    ax.set_xlim(60, 440)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("token budget")
    ax.set_ylabel("recall of gold items")
    ax.set_title("Recall within a budget (Kairos and vector top-k overlap)")
    ax.legend(frameon=False, loc="lower right", fontsize=9.5)
    _grid(ax)

    forbidden = [d["whole_org_vector"][str(b)]["forbidden_items_selected"] for b in budgets]
    kairos_forbidden = [d["kairos"][str(b)]["forbidden_items_selected"] for b in budgets]
    xs = [str(b) for b in budgets]
    bars = ax2.bar(xs, forbidden, color=MAGENTA, width=0.55, edgecolor=SURFACE, linewidth=2)
    for bar, n in zip(bars, forbidden, strict=True):
        ax2.text(bar.get_x() + bar.get_width() / 2, n + 8, str(n), ha="center", fontsize=10, color=INK)
    kairos_note = f"Kairos: {', '.join(map(str, kairos_forbidden))} at budgets {', '.join(xs)}"
    ax2.text(
        0.02,
        0.95,
        f"bars: retrieval over the whole org\n{kairos_note}",
        transform=ax2.transAxes,
        va="top",
        fontsize=9.5,
        color=INK,
    )
    ax2.set_ylim(0, max(forbidden) * 1.25)
    ax2.set_xlabel("token budget")
    ax2.set_title("Items selected the asker may not read")
    _grid(ax2)

    fig.suptitle(
        "Scoping, not ranking, is what Kairos adds: no better recall than similarity search, zero forbidden items",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    _save(
        fig,
        "fig2_selection_quality.png",
        "Source: results/selection_quality.json · 40 queries over a synthetic 84-item org · one run",
    )


def cache() -> None:
    configs = [c for c in _load("cache_bench.json")["configs"] if c["cache_enabled"] and not c["cross_user_reuse"]]
    configs.sort(key=lambda c: c["threshold"])
    xs = [f"{c['threshold']:.2f}" + (" (default)" if c["threshold"] == 0.9 else "") for c in configs]
    wrong = [c["hits_differing_from_fresh_assembly"] for c in configs]
    hits = [sum(k["hits"] for k in c["by_request_kind"].values()) for c in configs]
    right = [h - w for h, w in zip(hits, wrong, strict=True)]

    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.bar(
        xs, right, color=BLUE, width=0.55, edgecolor=SURFACE, linewidth=2, label="hit, same bundle as a fresh request"
    )
    ax.bar(
        xs,
        wrong,
        bottom=right,
        color=ORANGE,
        width=0.55,
        edgecolor=SURFACE,
        linewidth=2,
        label="hit, different bundle (wrong)",
    )
    for i, (r, w) in enumerate(zip(right, wrong, strict=True)):
        label = f"{w} wrong" if w else "0 wrong"
        ax.text(i, r + w + 0.6, label, ha="center", fontsize=10, color=INK)
    ax.set_ylim(0, max(hits) * 1.3)
    ax.set_xlabel("similarity threshold for a cache hit")
    ax.set_ylabel("cache hits (of 96 requests)")
    ax.set_title("At 0.75 every paraphrase hit was the wrong bundle; at 0.90 only exact repeats hit, all correct")
    ax.legend(frameon=False, loc="upper right", fontsize=9.5)
    _grid(ax)
    fig.tight_layout()
    _save(
        fig,
        "fig3_cache_threshold.png",
        "Source: results/cache_bench.json · cross-user reuse off · every hit compared with a fresh, uncached assembly",
    )


def latency() -> None:
    rows = [
        ("embedding only", "embed"),
        ("cache off", "uncached"),
        ("cache on, miss", "miss"),
        ("cache on, hit", "hit"),
    ]
    backends = [("SQLite", _load("latency_sqlite.json")), ("Postgres 16", _load("latency_postgres.json"))]

    fig, axes = plt.subplots(1, 2, figsize=(10, 3.2), sharex=True)
    top = max(b["latency"][k]["p95_ms"] for _, b in backends for _, k in rows)
    for ax, (title, data) in zip(axes, backends, strict=True):
        p50 = [data["latency"][k]["p50_ms"] for _, k in rows]
        p95 = [data["latency"][k]["p95_ms"] for _, k in rows]
        ax.barh([r for r, _ in rows], p50, color=BLUE, height=0.55, edgecolor=SURFACE, linewidth=2)
        for y, (a, b) in enumerate(zip(p50, p95, strict=True)):
            ax.text(a + top * 0.02, y, f"p50 {a:.1f} · p95 {b:.1f} ms", va="center", fontsize=9.5, color=INK)
        ax.invert_yaxis()
        ax.set_xlim(0, top * 1.6)
        ax.set_title(title)
        ax.set_xlabel("ms per assemble_context, p50")
        _grid(ax, "x")
    fig.suptitle(
        "Embedding dominates on SQLite, database round trips on Postgres; a cache hit saves under 1 ms",
        x=0.01,
        ha="left",
        fontweight="bold",
        fontsize=12,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    _save(
        fig,
        "fig4_latency.png",
        "Source: results/latency_sqlite.json, latency_postgres.json · 40 queries × 3 · "
        "in process, no HTTP · one laptop",
    )


def main() -> None:
    leak_probe()
    selection_quality()
    cache()
    latency()


if __name__ == "__main__":
    main()
