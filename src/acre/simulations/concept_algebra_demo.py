"""
Concept Algebra Demo — Visualization of algebraic operations on concepts.

This demo shows the four core algebraic operations that make ACRE different
from standard neural networks:

    compose:  Merge two concepts -> richer concept  (like mixing colors)
    apply:    Apply concept to problem -> solution   (like using a tool)
    subtract: Remove one concept from another       (like filtering)
    project:  Select specific elements               (like zooming in)

It also demonstrates algebraic consistency: composition should be
approximately commutative (A + B ~ B + A), which means the order of
combining knowledge doesn't change the result much.

Generates: figures/concept_algebra.png
"""

from __future__ import annotations

import math
import os
from typing import Dict, List, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Repo-root figures directory
# ---------------------------------------------------------------------------

def _get_figures_dir() -> str:
    """Resolve the figures/ directory at the repo root."""
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    return os.path.join(repo_root, "figures")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_ELEMENTS = 10
ELEMENT_DIM = 64
ELEMENT_NAMES = [
    "Ontology", "Abstraction", "Axioms", "Relations", "Inference",
    "Methods", "Code", "Goals", "Limits", "Inter-concept",
]


# ---------------------------------------------------------------------------
# Sample math concepts (pure numpy)
# ---------------------------------------------------------------------------

def create_math_concepts() -> Dict[str, np.ndarray]:
    """Create sample mathematical concepts as numpy arrays.

    Each concept has a distinct "signature" pattern across its 10 elements.
    """
    rng = np.random.RandomState(42)
    concepts: Dict[str, np.ndarray] = {}

    # Linear Algebra — strong in axioms and relations
    la = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    la[2] *= 3.0
    la[3] *= 2.5
    la[6] *= 2.0
    concepts["Linear Algebra"] = la

    # Calculus — strong in methods and inference
    calc = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    calc[4] *= 3.0
    calc[5] *= 2.5
    calc[0] *= 2.0
    concepts["Calculus"] = calc

    # Probability — strong in axioms and goals
    prob = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    prob[2] *= 2.5
    prob[7] *= 3.0
    prob[4] *= 2.0
    concepts["Probability"] = prob

    # Optimization — strong in methods and code
    opt = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    opt[5] *= 3.0
    opt[6] *= 3.0
    opt[7] *= 2.0
    concepts["Optimization"] = opt

    # Topology — strong in ontology and axioms
    topo = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    topo[0] *= 3.0
    topo[2] *= 3.0
    topo[8] *= 2.0
    concepts["Topology"] = topo

    # Statistics — strong in methods and goals
    stat = rng.randn(NUM_ELEMENTS, ELEMENT_DIM).astype(np.float32) * 0.3
    stat[5] *= 3.0
    stat[7] *= 2.5
    stat[6] *= 2.5
    concepts["Statistics"] = stat

    return concepts


# ---------------------------------------------------------------------------
# Algebraic operations (pure numpy)
# ---------------------------------------------------------------------------

def compose(a: np.ndarray, b: np.ndarray, alpha: float = 0.5) -> np.ndarray:
    """Compose two concepts: weighted combination with interaction."""
    interaction = np.tanh(a * b)
    return alpha * a + (1 - alpha) * b + 0.3 * interaction


def apply_concept(concept: np.ndarray, problem: np.ndarray) -> np.ndarray:
    """Apply concept to problem: modulated transformation."""
    gate = 1.0 / (1.0 + np.exp(-problem))  # sigmoid
    return concept * gate + 0.1 * np.tanh(concept + problem)


def subtract(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Subtract: remove b's contribution from a."""
    projection = (a * b).sum(axis=-1, keepdims=True) / (
        np.linalg.norm(b, axis=-1, keepdims=True) ** 2 + 1e-10
    )
    return a - projection * b


def project(concept: np.ndarray, element_mask: List[int]) -> np.ndarray:
    """Project: select specific elements."""
    mask = np.zeros((NUM_ELEMENTS, 1), dtype=np.float32)
    for idx in element_mask:
        mask[idx] = 1.0
    return concept * mask


# ---------------------------------------------------------------------------
# Commutativity test
# ---------------------------------------------------------------------------

def test_commutativity(concepts: Dict[str, np.ndarray], n_pairs: int = 50) -> Dict[str, float]:
    """Test algebraic consistency: A compose B should ~ B compose A."""
    names = list(concepts.keys())
    distances = []

    for _ in range(n_pairs):
        i, j = np.random.choice(len(names), 2, replace=False)
        a = concepts[names[i]]
        b = concepts[names[j]]

        ab = compose(a, b)
        ba = compose(b, a)

        dist = np.linalg.norm(ab - ba)
        max_norm = max(np.linalg.norm(ab), np.linalg.norm(ba), 1e-10)
        rel_dist = dist / max_norm
        distances.append(rel_dist)

    return {
        "mean_relative_distance": float(np.mean(distances)),
        "std_relative_distance": float(np.std(distances)),
        "max_relative_distance": float(np.max(distances)),
        "is_approximately_commutative": float(np.mean(distances)) < 0.5,
    }


# ---------------------------------------------------------------------------
# Dimensionality reduction
# ---------------------------------------------------------------------------

def reduce_to_2d(tensors: List[np.ndarray]) -> np.ndarray:
    """Reduce concept tensors to 2D for visualization using PCA."""
    flat = np.stack([t.reshape(-1) for t in tensors])
    flat = flat - flat.mean(axis=0)
    U, S, Vt = np.linalg.svd(flat, full_matrices=False)
    return U[:, :2] * S[:2]


# ---------------------------------------------------------------------------
# Figure generation
# ---------------------------------------------------------------------------

def generate_figures(output_dir: str | None = None) -> None:
    """Generate publication-quality concept algebra figure."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if output_dir is None:
        output_dir = _get_figures_dir()
    os.makedirs(output_dir, exist_ok=True)

    concepts = create_math_concepts()
    names = list(concepts.keys())

    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(
        "ACRE Concept Algebra -- Operations & Consistency",
        fontsize=18, fontweight="bold", y=0.98,
    )

    # --- Panel 1: Concept space visualization ---
    ax = axes[0, 0]
    all_tensors = list(concepts.values())

    composed = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            comp_name = f"{names[i][:3]}+{names[j][:3]}"
            composed[comp_name] = compose(concepts[names[i]], concepts[names[j]])
    all_tensors_with_composed = all_tensors + list(composed.values())

    coords = reduce_to_2d(all_tensors_with_composed)

    n_orig = len(names)
    colors = plt.cm.Set2(np.linspace(0, 1, n_orig))
    ax.scatter(coords[:n_orig, 0], coords[:n_orig, 1], c=colors, s=200,
               zorder=5, edgecolors="black", linewidth=1.5, marker="o")
    for i, name in enumerate(names):
        ax.annotate(name, (coords[i, 0], coords[i, 1]),
                    fontsize=8, fontweight="bold",
                    xytext=(8, 8), textcoords="offset points")

    ax.scatter(coords[n_orig:, 0], coords[n_orig:, 1], c="#999999", s=60,
               marker="^", alpha=0.5, zorder=3)

    ax.set_title("Concept Space (PCA projection)", fontsize=13, fontweight="bold")
    ax.set_xlabel("PC1")
    ax.set_ylabel("PC2")
    ax.grid(alpha=0.2)

    # --- Panel 2: Element-level heatmap ---
    ax = axes[0, 1]
    element_norms = np.array([
        [np.linalg.norm(concepts[n][e]) for e in range(NUM_ELEMENTS)]
        for n in names
    ])
    im = ax.imshow(element_norms, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(NUM_ELEMENTS))
    ax.set_xticklabels(ELEMENT_NAMES, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, fontsize=8)
    ax.set_title("Element Strength per Concept", fontsize=13, fontweight="bold")
    plt.colorbar(im, ax=ax, label="L2 Norm")

    # --- Panel 3: Operation effects ---
    ax = axes[1, 0]
    la = concepts["Linear Algebra"]
    calc = concepts["Calculus"]
    prob = concepts["Probability"]

    ops = {
        "LA+Calc": compose(la, calc),
        "LA*Prob": apply_concept(la, prob),
        "Calc-LA": subtract(calc, la),
        "LA P[0,2,6]": project(la, [0, 2, 6]),
    }

    x = np.arange(NUM_ELEMENTS)
    width = 0.18
    op_colors = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0"]

    for idx, (op_name, result) in enumerate(ops.items()):
        norms = [np.linalg.norm(result[e]) for e in range(NUM_ELEMENTS)]
        ax.bar(x + idx * width, norms, width, label=op_name, color=op_colors[idx],
               edgecolor="white", linewidth=0.5)

    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(ELEMENT_NAMES, rotation=45, ha="right", fontsize=7)
    ax.set_ylabel("Element L2 Norm")
    ax.set_title("Effect of Algebraic Operations", fontsize=13, fontweight="bold")
    ax.legend(fontsize=8, loc="upper right")
    ax.grid(axis="y", alpha=0.3)

    # --- Panel 4: Commutativity test ---
    ax = axes[1, 1]

    n_test = 100
    distances = []

    for trial in range(n_test):
        np.random.seed(trial)
        i, j = np.random.choice(len(names), 2, replace=False)
        a = concepts[names[i]]
        b = concepts[names[j]]
        ab = compose(a, b)
        ba = compose(b, a)
        dist = np.linalg.norm(ab - ba) / max(np.linalg.norm(ab), 1e-10)
        distances.append(dist)

    ax.hist(distances, bins=25, color="#2196F3", edgecolor="white", alpha=0.8)
    ax.axvline(np.mean(distances), color="#E53935", linestyle="--", linewidth=2,
               label=f"Mean = {np.mean(distances):.4f}")
    ax.set_xlabel("Relative Distance: ||A+B - B+A|| / ||A+B||")
    ax.set_ylabel("Count")
    ax.set_title("Commutativity Test: A+B ~ B+A?", fontsize=13, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(alpha=0.3)

    comm_result = "Approximately commutative" if np.mean(distances) < 0.5 else "Not commutative"
    ax.text(0.98, 0.95, comm_result, transform=ax.transAxes, fontsize=11,
            fontweight="bold", ha="right", va="top",
            color="#43A047" if np.mean(distances) < 0.5 else "#E53935",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", edgecolor="#ccc"))

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    path = os.path.join(output_dir, "concept_algebra.png")
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"\nFigure saved -> {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("ACRE -- Concept Algebra Demo")
    print("=" * 60)

    concepts = create_math_concepts()

    # Show element strengths
    print("\nConcept Element Norms:")
    print(f"{'Concept':20s}", end="")
    for name in ELEMENT_NAMES:
        print(f" {name[:6]:>7s}", end="")
    print()
    print("-" * 95)
    for cname, arr in concepts.items():
        print(f"{cname:20s}", end="")
        for e in range(NUM_ELEMENTS):
            print(f" {np.linalg.norm(arr[e]):>7.2f}", end="")
        print()

    # Demonstrate operations
    print("\n--- Algebraic Operations ---")
    la = concepts["Linear Algebra"]
    calc = concepts["Calculus"]

    comp = compose(la, calc)
    print(f"  LA + Calc norm:     {np.linalg.norm(comp):.3f}")

    applied = apply_concept(la, concepts["Probability"])
    print(f"  LA * Prob norm:     {np.linalg.norm(applied):.3f}")

    diff = subtract(calc, la)
    print(f"  Calc - LA norm:     {np.linalg.norm(diff):.3f}")

    proj = project(la, [0, 2, 6])
    print(f"  LA P[ont,ax,code]:  {np.linalg.norm(proj):.3f}")

    # Commutativity test
    print("\n--- Commutativity Test ---")
    comm_results = test_commutativity(concepts)
    for k, v in comm_results.items():
        print(f"  {k}: {v}")

    # Generate figure
    generate_figures()
    print("\nDone.")
