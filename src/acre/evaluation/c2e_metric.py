"""
C2E Metric — Concept-to-Evaluation scoring system.

This implements the full C2E-Metric from the ACRE specification: a weighted,
multi-faceted scoring system that grades how well a *generated* concept
description matches a *ground-truth* standard.

Think of it like a report card with 10 subjects, where some subjects (like
code quality and axioms) count much more than others (like inter-concept
relationships).

Weight tiers:
    Tier 1 (56%): w7=0.20 (Code), w1=0.18 (Ontology), w3=0.18 (Axioms)
    Tier 2 (32%): w2=0.08, w4=0.08, w6=0.08, w8=0.08
    Tier 3 (12%): w5=0.04, w9=0.04, w10=0.04

Scoring methods:
    - Semantic similarity (for text fields)
    - Structural comparison (for XML/formal structures)
    - Categorical accuracy (for abstraction levels)
    - Functional verification (for code)

Classes:
    ElementScorer: Computes score for a single element.
    C2EResult: Container for detailed scoring results.
    C2EMetric: Main metric class with score() and batch_score().
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from torch import Tensor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Weight configuration (from Concept_Evaluation_V1.0.md)
# ---------------------------------------------------------------------------

# Element names and their weights
ELEMENT_NAMES = [
    "Ontological Scaffolding",      # 1
    "Abstraction Level",            # 2
    "Axiomatic Base",               # 3
    "Relational Network",           # 4
    "Inferential Framework",        # 5
    "Methodological Apparatus",     # 6
    "Illustrative Corpus / Code",   # 7
    "Goal Orientation & Scope",     # 8
    "Limitations & Risks",          # 9
    "Inter-Concept Relationships",  # 10
]

# Tier-based weights (sum = 1.0)
ELEMENT_WEIGHTS = {
    1: 0.18,   # Tier 1: Ontology
    2: 0.08,   # Tier 2: Abstraction
    3: 0.18,   # Tier 1: Axioms
    4: 0.08,   # Tier 2: Relations
    5: 0.04,   # Tier 3: Inference
    6: 0.08,   # Tier 2: Methods
    7: 0.20,   # Tier 1: Code (highest!)
    8: 0.08,   # Tier 2: Goals
    9: 0.04,   # Tier 3: Limitations
    10: 0.04,  # Tier 3: Inter-concept
}

TIER_LABELS = {
    1: "Tier 1 — Core Identity",
    2: "Tier 2 — Structure & Application",
    3: "Tier 3 — Context & Nuance",
}

ELEMENT_TIERS = {
    1: 1, 2: 2, 3: 1, 4: 2, 5: 3,
    6: 2, 7: 1, 8: 2, 9: 3, 10: 3,
}


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class C2EResult:
    """Detailed scoring result from the C2E metric."""
    total_score: float                              # 0-100
    element_scores: Dict[int, float]                # per-element 0-100
    weighted_contributions: Dict[int, float]        # per-element weighted
    tier_scores: Dict[int, float]                   # per-tier average
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"C2EResult(score={self.total_score:.1f}/100)"

    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            f"C2E Score: {self.total_score:.1f} / 100",
            "",
            "Element Breakdown:",
        ]
        for i in range(1, 11):
            name = ELEMENT_NAMES[i - 1]
            score = self.element_scores.get(i, 0)
            weight = ELEMENT_WEIGHTS[i]
            tier = ELEMENT_TIERS[i]
            contrib = self.weighted_contributions.get(i, 0)
            lines.append(
                f"  [{i:2d}] {name:35s}  "
                f"Score: {score:5.1f}  × w={weight:.2f}  "
                f"= {contrib:5.2f}  (Tier {tier})"
            )
        lines.append("")
        for tier in [1, 2, 3]:
            lines.append(f"  {TIER_LABELS[tier]}: {self.tier_scores.get(tier, 0):.1f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Element scoring functions
# ---------------------------------------------------------------------------

class ElementScorer:
    """Computes scores for individual concept elements.

    Uses different methods depending on the element type:
        - Semantic similarity for text-based elements
        - Structural comparison for formal elements
        - Categorical match for abstraction level
        - Functional verification for code
    """

    def __init__(self) -> None:
        pass

    def _cosine_sim_score(self, gen: Tensor, gt: Tensor) -> float:
        """Cosine similarity scaled to [0, 100]."""
        if gen.dim() == 1:
            gen = gen.unsqueeze(0)
        if gt.dim() == 1:
            gt = gt.unsqueeze(0)
        sim = F.cosine_similarity(gen, gt, dim=-1).mean().item()
        return max(0.0, (sim + 1) / 2 * 100)  # Scale [-1,1] → [0,100]

    def _l2_distance_score(self, gen: Tensor, gt: Tensor, max_dist: float = 10.0) -> float:
        """L2 distance mapped to [0, 100]: closer = higher score."""
        dist = (gen - gt).norm().item()
        return max(0.0, 100 * (1 - dist / max_dist))

    def score_s1_ontology(self, gen: Tensor, gt: Tensor) -> float:
        """S1 — Ontological Scaffolding.

        Uses holistic vector comparison across the full embedding.
        The dense representation captures definitions, taxonomy, and modular
        composition jointly — arbitrary slicing would yield noise since
        there is no disentangling loss in the pipeline.
        """
        return self._cosine_sim_score(gen, gt)

    def score_s2_abstraction(self, gen: Tensor, gt: Tensor) -> float:
        """S2 — Abstraction Level (categorical)."""
        # Map element norm to level 1-4
        gen_level = max(1, min(4, int(gen.norm().item() * 4 / gen.numel() + 1)))
        gt_level = max(1, min(4, int(gt.norm().item() * 4 / gt.numel() + 1)))
        return 100 * max(0, 1 - abs(gen_level - gt_level) / 3)

    def score_s3_axioms(self, gen: Tensor, gt: Tensor) -> float:
        """S3 — Axiomatic Base.

        Uses a blend of cosine similarity (textual fidelity) and L2 distance
        (structural precision) over the full holistic embedding.
        """
        textual = self._cosine_sim_score(gen, gt)
        formal = self._l2_distance_score(gen, gt)
        return 0.5 * textual + 0.5 * formal

    def score_s4_relations(self, gen: Tensor, gt: Tensor) -> float:
        """S4 — Relational Network.

        Uses a blend of cosine similarity (structural topology) and L2 distance
        (relational precision) over the full holistic embedding.
        """
        textual = self._cosine_sim_score(gen, gt)
        formal = self._l2_distance_score(gen, gt)
        return 0.4 * textual + 0.6 * formal

    def score_s5_inference(self, gen: Tensor, gt: Tensor) -> float:
        """S5 — Inferential Framework (textual similarity)."""
        return self._cosine_sim_score(gen, gt)

    def score_s6_methods(self, gen: Tensor, gt: Tensor) -> float:
        """S6 — Methodological Apparatus.

        Uses holistic vector comparison across the full embedding.
        """
        return self._cosine_sim_score(gen, gt)

    def score_s7_code(self, gen: Tensor, gt: Tensor) -> float:
        """S7 — Illustrative Corpus / Code.

        Uses the CQRS formula: S7 = 100 * C_exec * C_correct * (0.6*C_relev + 0.4*C_compl)
        In tensor mode, we approximate these with structural comparisons.
        """
        # C_exec: does the embedding represent valid structure? (norm > threshold)
        c_exec = 1.0 if gen.norm().item() > 0.1 else 0.0
        # C_correct: structural match (high cosine sim = likely correct)
        sim = F.cosine_similarity(gen.reshape(1, -1), gt.reshape(1, -1)).item()
        c_correct = 1.0 if sim > 0.5 else 0.0
        # C_relev: semantic relevance
        c_relev = max(0.0, (sim + 1) / 2)
        # C_compl: completeness (fraction of non-zero elements)
        c_compl = (gen.abs() > 0.01).float().mean().item()

        return 100 * c_exec * c_correct * (0.6 * c_relev + 0.4 * c_compl)

    def score_s8_goals(self, gen: Tensor, gt: Tensor) -> float:
        """S8 — Goal Orientation & Scope."""
        return self._cosine_sim_score(gen, gt)

    def score_s9_limitations(self, gen: Tensor, gt: Tensor) -> float:
        """S9 — Limitations & Risks."""
        return self._cosine_sim_score(gen, gt)

    def score_s10_interconcept(self, gen: Tensor, gt: Tensor) -> float:
        """S10 — Inter-Concept Relationships."""
        return self._cosine_sim_score(gen, gt)

    def score_element(self, element_idx: int, gen: Tensor, gt: Tensor) -> float:
        """Route to the appropriate scoring function for an element.

        Args:
            element_idx: 1-based element index.
            gen: Generated element tensor.
            gt: Ground truth element tensor.

        Returns:
            Score between 0 and 100.
        """
        scorers = {
            1: self.score_s1_ontology,
            2: self.score_s2_abstraction,
            3: self.score_s3_axioms,
            4: self.score_s4_relations,
            5: self.score_s5_inference,
            6: self.score_s6_methods,
            7: self.score_s7_code,
            8: self.score_s8_goals,
            9: self.score_s9_limitations,
            10: self.score_s10_interconcept,
        }
        return scorers[element_idx](gen, gt)


# ---------------------------------------------------------------------------
# Main C2E Metric class
# ---------------------------------------------------------------------------

class C2EMetric:
    """C2E Concept-to-Evaluation metric.

    Produces a weighted score (0-100) measuring fidelity of a generated
    concept description to a ground-truth standard.

    Usage::

        metric = C2EMetric()
        result = metric.score(generated_tensor, ground_truth_tensor)
        print(result.summary())
        print(result.total_score)

        # Batch mode
        results = metric.batch_score(generated_list, ground_truth_list)
    """

    def __init__(self, weights: Optional[Dict[int, float]] = None) -> None:
        self.weights = weights or ELEMENT_WEIGHTS
        self.scorer = ElementScorer()

        # Validate weights sum to ~1.0
        w_sum = sum(self.weights.values())
        assert abs(w_sum - 1.0) < 1e-6, f"Weights must sum to 1.0, got {w_sum}"

    def score(
        self,
        generated: Tensor,
        ground_truth: Tensor,
    ) -> C2EResult:
        """Score a generated concept tensor against ground truth.

        Args:
            generated: (10, d) generated concept tensor.
            ground_truth: (10, d) ground truth concept tensor.

        Returns:
            C2EResult with detailed scoring breakdown.
        """
        assert generated.shape[0] == 10, f"Expected 10 elements, got {generated.shape[0]}"
        assert ground_truth.shape[0] == 10

        element_scores: Dict[int, float] = {}
        weighted_contributions: Dict[int, float] = {}

        for i in range(1, 11):
            gen_elem = generated[i - 1]
            gt_elem = ground_truth[i - 1]
            s_i = self.scorer.score_element(i, gen_elem, gt_elem)
            element_scores[i] = s_i
            weighted_contributions[i] = self.weights[i] * s_i

        total_score = sum(weighted_contributions.values())

        # Per-tier averages
        tier_scores: Dict[int, float] = {}
        for tier in [1, 2, 3]:
            tier_elements = [i for i, t in ELEMENT_TIERS.items() if t == tier]
            tier_weight = sum(self.weights[i] for i in tier_elements)
            tier_contrib = sum(weighted_contributions[i] for i in tier_elements)
            tier_scores[tier] = tier_contrib / tier_weight if tier_weight > 0 else 0

        return C2EResult(
            total_score=total_score,
            element_scores=element_scores,
            weighted_contributions=weighted_contributions,
            tier_scores=tier_scores,
        )

    def batch_score(
        self,
        generated_list: List[Tensor],
        ground_truth_list: List[Tensor],
    ) -> List[C2EResult]:
        """Score a batch of generated vs ground-truth concept pairs.

        Returns:
            List of C2EResult, one per pair.
        """
        assert len(generated_list) == len(ground_truth_list)
        return [
            self.score(gen, gt)
            for gen, gt in zip(generated_list, ground_truth_list)
        ]

    def aggregate_results(self, results: List[C2EResult]) -> Dict[str, float]:
        """Compute aggregate statistics over a batch of results."""
        scores = [r.total_score for r in results]
        return {
            "mean_score": sum(scores) / len(scores),
            "min_score": min(scores),
            "max_score": max(scores),
            "std_score": (sum((s - sum(scores)/len(scores))**2 for s in scores) / len(scores)) ** 0.5,
            "n_samples": len(scores),
        }


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — C2E Metric Demo")
    print("=" * 60)

    metric = C2EMetric()
    d = 64

    # Example 1: High-quality generation (close to ground truth)
    gt = torch.randn(10, d)
    gen_good = gt + torch.randn(10, d) * 0.1  # Small noise
    result_good = metric.score(gen_good, gt)

    print("\n--- Example 1: High-quality generation ---")
    print(result_good.summary())

    # Example 2: Medium-quality (moderate noise)
    gen_medium = gt + torch.randn(10, d) * 0.5
    result_medium = metric.score(gen_medium, gt)

    print("\n--- Example 2: Medium-quality generation ---")
    print(f"C2E Score: {result_medium.total_score:.1f} / 100")

    # Example 3: Poor quality (random)
    gen_bad = torch.randn(10, d)
    result_bad = metric.score(gen_bad, gt)

    print("\n--- Example 3: Poor-quality generation ---")
    print(f"C2E Score: {result_bad.total_score:.1f} / 100")

    # Batch evaluation
    print("\n--- Batch Evaluation ---")
    results = metric.batch_score(
        [gen_good, gen_medium, gen_bad],
        [gt, gt, gt],
    )
    agg = metric.aggregate_results(results)
    for k, v in agg.items():
        print(f"  {k}: {v:.2f}")

    print("\nDone ✓")
