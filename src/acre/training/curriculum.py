"""
Curriculum Scheduler — 3-phase progressive difficulty training.

Imagine teaching a child: first single words, then short sentences, then
complex paragraphs with trick questions.  The curriculum scheduler does
exactly that for concept algebra training:

    Phase 1 (Warmup):   Single concept + single problem pairs
    Phase 2 (Compose):  Multi-concept composition with 2-3 concepts
    Phase 3 (OOD):      Out-of-distribution problems + distractors

Phase transitions are automatic: once accuracy exceeds a threshold on the
current phase, the system advances to the next one.

Classes:
    Phase: Enum for curriculum phases.
    ProblemGenerator: Generates problems of appropriate difficulty.
    CurriculumScheduler: Manages phase transitions and difficulty scaling.
"""

from __future__ import annotations

import enum
import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
from torch import Tensor

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------

class Phase(enum.Enum):
    """Curriculum training phases with increasing difficulty."""
    WARMUP = 1       # Single concept + single problem
    COMPOSE = 2      # Multi-concept composition (2–3 concepts)
    OOD = 3          # Out-of-distribution + distractors


@dataclass
class PhaseConfig:
    """Configuration for a single curriculum phase."""
    name: str
    phase: Phase
    # How many concepts per problem
    min_concepts: int = 1
    max_concepts: int = 1
    # Number of distractor concepts mixed in
    num_distractors: int = 0
    # Noise injected into problem tensors (simulates OOD)
    noise_scale: float = 0.0
    # Accuracy threshold to advance to next phase
    advance_threshold: float = 0.85
    # Minimum steps before considering advancement
    min_steps: int = 500
    # Maximum steps before forced advancement
    max_steps: int = 5_000


PHASE_CONFIGS = {
    Phase.WARMUP: PhaseConfig(
        name="Phase 1 — Warmup (single pairs)",
        phase=Phase.WARMUP,
        min_concepts=1, max_concepts=1,
        num_distractors=0, noise_scale=0.0,
        advance_threshold=0.85, min_steps=500, max_steps=3_000,
    ),
    Phase.COMPOSE: PhaseConfig(
        name="Phase 2 — Composition (multi-concept)",
        phase=Phase.COMPOSE,
        min_concepts=2, max_concepts=3,
        num_distractors=0, noise_scale=0.05,
        advance_threshold=0.80, min_steps=1_000, max_steps=5_000,
    ),
    Phase.OOD: PhaseConfig(
        name="Phase 3 — OOD + Distractors",
        phase=Phase.OOD,
        min_concepts=2, max_concepts=4,
        num_distractors=3, noise_scale=0.15,
        advance_threshold=0.90, min_steps=2_000, max_steps=10_000,
    ),
}


# ---------------------------------------------------------------------------
# Concept library (synthetic)
# ---------------------------------------------------------------------------

class ConceptLibrary:
    """A pool of reusable concept tensors.

    In production this holds real extracted concepts; here we generate
    synthetic ones with controllable semantic clustering.
    """

    def __init__(
        self,
        n_concepts: int = 200,
        n_clusters: int = 20,
        element_dim: int = ELEMENT_DIM,
    ) -> None:
        self.n_concepts = n_concepts
        self.element_dim = element_dim

        # Create clustered concepts (similar within cluster, different across)
        self.concepts: List[Tensor] = []
        self.cluster_ids: List[int] = []
        per_cluster = n_concepts // n_clusters

        for cid in range(n_clusters):
            centroid = torch.randn(NUM_ELEMENTS, element_dim)
            for _ in range(per_cluster):
                concept = centroid + torch.randn_like(centroid) * 0.3
                self.concepts.append(concept)
                self.cluster_ids.append(cid)

    def sample(self, n: int = 1) -> List[Tensor]:
        """Sample n random concepts from the library."""
        indices = torch.randperm(len(self.concepts))[:n].tolist()
        return [self.concepts[i] for i in indices]

    def sample_from_same_cluster(self, n: int = 2) -> List[Tensor]:
        """Sample n concepts from the same semantic cluster."""
        cid = torch.randint(0, max(self.cluster_ids) + 1, (1,)).item()
        matching = [i for i, c in enumerate(self.cluster_ids) if c == cid]
        if len(matching) < n:
            matching = list(range(len(self.concepts)))
        indices = [matching[i] for i in torch.randperm(len(matching))[:n].tolist()]
        return [self.concepts[i] for i in indices]

    def sample_distractors(self, exclude_cluster: int, n: int = 3) -> List[Tensor]:
        """Sample concepts from DIFFERENT clusters (distractors)."""
        non_matching = [i for i, c in enumerate(self.cluster_ids) if c != exclude_cluster]
        if len(non_matching) < n:
            non_matching = list(range(len(self.concepts)))
        indices = [non_matching[i] for i in torch.randperm(len(non_matching))[:n].tolist()]
        return [self.concepts[i] for i in indices]


# ---------------------------------------------------------------------------
# Problem generator
# ---------------------------------------------------------------------------

class ProblemGenerator:
    """Generates training problems at the appropriate difficulty level.

    Each problem is a tuple of:
        - relevant_concepts: list of concepts needed to solve it
        - distractors: irrelevant concepts mixed in (Phase 3)
        - problem_tensor: the problem itself
        - ground_truth: expected solution (composition of relevant concepts)
    """

    def __init__(self, library: Optional[ConceptLibrary] = None) -> None:
        self.library = library or ConceptLibrary()

    def generate(
        self,
        phase_config: PhaseConfig,
    ) -> Dict[str, Tensor | List[Tensor]]:
        """Generate one training example for the given phase.

        Returns:
            Dict with keys: concepts, distractors, problem, target.
        """
        n_concepts = torch.randint(
            phase_config.min_concepts, phase_config.max_concepts + 1, (1,)
        ).item()

        # Sample relevant concepts
        concepts = self.library.sample_from_same_cluster(n_concepts)

        # Build problem from concepts
        problem = self._build_problem(concepts, noise_scale=phase_config.noise_scale)

        # Target: mean of concept elements (simplified ground truth)
        target = torch.stack(concepts).mean(dim=0)

        # Distractors
        distractors = self.library.sample(phase_config.num_distractors)

        return {
            "concepts": concepts,
            "distractors": distractors,
            "problem": problem,
            "target": target,
        }

    def _build_problem(self, concepts: List[Tensor], noise_scale: float = 0.0) -> Tensor:
        """Create a problem tensor that requires the given concepts to solve.

        The problem encodes a noisy projection of the concept space, so the
        solver must learn to reconstruct the clean concept combination.
        """
        combined = torch.stack(concepts).mean(dim=0)
        # Rotate to create a distinct representation
        perm = torch.randperm(NUM_ELEMENTS)
        problem = combined[perm]
        # Add OOD noise
        if noise_scale > 0:
            problem = problem + torch.randn_like(problem) * noise_scale
        return problem


# ---------------------------------------------------------------------------
# Accuracy tracker (windowed)
# ---------------------------------------------------------------------------

class WindowedAccuracy:
    """Tracks accuracy over a sliding window of recent attempts."""

    def __init__(self, window_size: int = 200) -> None:
        self.window: List[bool] = []
        self.window_size = window_size
        self.total_correct = 0
        self.total_attempts = 0

    def record(self, correct: bool) -> None:
        self.window.append(correct)
        self.total_correct += int(correct)
        self.total_attempts += 1
        if len(self.window) > self.window_size:
            removed = self.window.pop(0)
            # (total_correct tracks all-time, window gives recent accuracy)

    @property
    def windowed_accuracy(self) -> float:
        if not self.window:
            return 0.0
        return sum(self.window) / len(self.window)

    @property
    def overall_accuracy(self) -> float:
        return self.total_correct / max(self.total_attempts, 1)


# ---------------------------------------------------------------------------
# Curriculum scheduler
# ---------------------------------------------------------------------------

@dataclass
class CurriculumState:
    """Persistent state of the curriculum."""
    current_phase: Phase = Phase.WARMUP
    steps_in_phase: int = 0
    total_steps: int = 0
    phase_history: List[Dict] = field(default_factory=list)


class CurriculumScheduler:
    """Manages 3-phase curriculum with automatic difficulty scaling.

    The scheduler:
        1. Starts at Phase 1 (simple single pairs)
        2. Monitors windowed accuracy
        3. When accuracy > threshold (and min_steps met), advances to next phase
        4. Logs phase transitions for analysis

    Usage::

        scheduler = CurriculumScheduler()
        while not scheduler.is_complete:
            example = scheduler.next_example()
            # ... train on example ...
            scheduler.record_result(correct=True)
    """

    def __init__(
        self,
        concept_library: Optional[ConceptLibrary] = None,
        phase_configs: Optional[Dict[Phase, PhaseConfig]] = None,
    ) -> None:
        self.library = concept_library or ConceptLibrary()
        self.configs = phase_configs or PHASE_CONFIGS
        self.generator = ProblemGenerator(self.library)
        self.state = CurriculumState()
        self.accuracy = WindowedAccuracy()
        self._phase_order = [Phase.WARMUP, Phase.COMPOSE, Phase.OOD]

    @property
    def current_phase(self) -> Phase:
        return self.state.current_phase

    @property
    def current_config(self) -> PhaseConfig:
        return self.configs[self.state.current_phase]

    @property
    def is_complete(self) -> bool:
        """True if we've completed all phases or hit max steps on the last."""
        if self.state.current_phase != Phase.OOD:
            return False
        cfg = self.current_config
        return (
            self.state.steps_in_phase >= cfg.max_steps
            or (
                self.state.steps_in_phase >= cfg.min_steps
                and self.accuracy.windowed_accuracy >= cfg.advance_threshold
            )
        )

    def next_example(self) -> Dict[str, Tensor | List[Tensor]]:
        """Generate the next training example at the current difficulty."""
        return self.generator.generate(self.current_config)

    def record_result(self, correct: bool) -> None:
        """Record whether the model got the latest example correct.

        May trigger phase advancement.
        """
        self.accuracy.record(correct)
        self.state.steps_in_phase += 1
        self.state.total_steps += 1

        self._maybe_advance()

    def _maybe_advance(self) -> None:
        """Check if we should move to the next phase."""
        cfg = self.current_config
        phase_idx = self._phase_order.index(self.state.current_phase)

        # Can't advance past the last phase
        if phase_idx >= len(self._phase_order) - 1:
            return

        should_advance = False

        # Forced advancement: too many steps
        if self.state.steps_in_phase >= cfg.max_steps:
            should_advance = True
            reason = "max steps reached"

        # Merit-based: accuracy threshold met after minimum steps
        elif (
            self.state.steps_in_phase >= cfg.min_steps
            and self.accuracy.windowed_accuracy >= cfg.advance_threshold
        ):
            should_advance = True
            reason = f"accuracy {self.accuracy.windowed_accuracy:.2%} ≥ {cfg.advance_threshold:.0%}"

        if should_advance:
            old_phase = self.state.current_phase
            new_phase = self._phase_order[phase_idx + 1]

            self.state.phase_history.append({
                "phase": old_phase.name,
                "steps": self.state.steps_in_phase,
                "final_accuracy": self.accuracy.windowed_accuracy,
                "reason": reason,
            })

            self.state.current_phase = new_phase
            self.state.steps_in_phase = 0
            self.accuracy = WindowedAccuracy()  # Reset window for new phase

            logger.info(f"Phase transition: {old_phase.name} → {new_phase.name} ({reason})")
            print(f"\n{'='*50}")
            print(f"PHASE TRANSITION: {old_phase.name} → {new_phase.name}")
            print(f"  Reason: {reason}")
            print(f"  Total steps so far: {self.state.total_steps}")
            print(f"{'='*50}\n")

    def get_status(self) -> Dict:
        """Get a summary of the current curriculum state."""
        return {
            "phase": self.state.current_phase.name,
            "steps_in_phase": self.state.steps_in_phase,
            "total_steps": self.state.total_steps,
            "windowed_accuracy": self.accuracy.windowed_accuracy,
            "overall_accuracy": self.accuracy.overall_accuracy,
            "is_complete": self.is_complete,
            "phase_history": self.state.phase_history,
        }


# ---------------------------------------------------------------------------
# Standalone demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — Curriculum Scheduler Demo")
    print("=" * 60)

    scheduler = CurriculumScheduler()

    # Simulate training with gradually improving accuracy
    import random

    step = 0
    while not scheduler.is_complete and step < 8_000:
        example = scheduler.next_example()
        step += 1

        # Simulate: accuracy improves over time within each phase
        phase_step = scheduler.state.steps_in_phase
        base_acc = 0.5 + 0.4 * min(phase_step / 800, 1.0)
        correct = random.random() < base_acc

        scheduler.record_result(correct)

        if step % 500 == 0:
            status = scheduler.get_status()
            print(
                f"Step {step}: Phase={status['phase']}  "
                f"acc={status['windowed_accuracy']:.2%}  "
                f"phase_step={status['steps_in_phase']}"
            )

    print("\n--- Final Status ---")
    status = scheduler.get_status()
    for k, v in status.items():
        print(f"  {k}: {v}")

    print("\nDone ✓")
