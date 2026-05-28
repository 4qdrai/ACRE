"""
Self-Learning Loop — Solve, verify, store, consolidate.

This implements the core self-improvement cycle: the system *tries* to solve
a problem, *checks* whether the solution is correct using the GPF verification
code, and then either stores the successful triple in Latent RAG (a kind of
experience replay buffer) or uses the failure as a targeted training signal.

Periodically, successful triples are "consolidated" — compressed from explicit
stored examples into the model's weights, like how sleep consolidates
short-term memory into long-term memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from torch.optim import AdamW

from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.lare import LARE
from acre.core.latent_rag import LatentRAG

logger = logging.getLogger(__name__)


@dataclass
class LearningStats:
    """Tracks self-learning progress over time."""
    total_attempts: int = 0
    successes: int = 0
    failures: int = 0
    success_rate_history: List[float] = field(default_factory=list)
    loss_history: List[float] = field(default_factory=list)
    rag_size_history: List[int] = field(default_factory=list)
    consolidation_count: int = 0

    @property
    def success_rate(self) -> float:
        return self.successes / max(self.total_attempts, 1)

    def record_attempt(self, success: bool) -> None:
        self.total_attempts += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1
        self.success_rate_history.append(self.success_rate)


@dataclass
class SelfLearningConfig:
    """Configuration for the self-learning loop."""
    max_iterations: int = 5000
    consolidation_interval: int = 500
    consolidation_epochs: int = 5
    consolidation_batch_size: int = 64
    lr: float = 1e-4
    verification_threshold: float = 0.6
    rag_max_size: int = 50000
    device: str = "cpu"
    log_every: int = 50
    element_dim: int = 128  # Align with LARE default


class SelfLearningLoop:
    """Orchestrates the self-learning cycle: solve → verify → store/train.

    The loop operates in two modes:
        1. **Online**: Solve problems one at a time, verify, store successes
        2. **Consolidation**: Periodically distill RAG entries into model weights
    """

    def __init__(
        self,
        config: Optional[SelfLearningConfig] = None,
        pipeline: Optional[Any] = None,
    ) -> None:
        self.cfg = config or SelfLearningConfig()
        self.device = torch.device(self.cfg.device)

        self.solver = LARE(d=self.cfg.element_dim).to(self.device)
        self.rag = LatentRAG(d_embed=256, max_entries=self.cfg.rag_max_size)
        self.stats = LearningStats()
        self.pipeline = pipeline

        # Define joint optimization parameters if pipeline is provided
        params = list(self.solver.parameters())
        if pipeline is not None:
            params.extend(list(pipeline.text_encoder.parameters()))
            params.extend(list(pipeline.concept_head.parameters()))
            params.extend(list(pipeline.problem_head.parameters()))

        self.optimizer = AdamW(params, lr=self.cfg.lr)

    def solve_and_verify(
        self,
        concept: ConceptTensor,
        problem: ProblemTensor,
    ) -> Tuple[SolutionTensor, bool, float]:
        """Solve a single problem and verify the result.

        Args:
            concept: ConceptTensor
            problem: ProblemTensor

        Returns:
            (solution, passed, score)
        """
        self.solver.eval()
        with torch.no_grad():
            c = concept.to(self.device)
            p = problem.to(self.device)
            solution = self.solver([c], p).to("cpu")

        # Verify solution against problem
        passed = solution.verify(problem)
        return solution, passed, solution.confidence

    def _store_success(self, concept: ConceptTensor, problem: ProblemTensor, solution: SolutionTensor) -> None:
        """Store a verified triple in Latent RAG."""
        self.rag.store(
            concept.to("cpu"),
            problem.to("cpu"),
            solution.to("cpu"),
        )

    def _train_on_failure(
        self,
        concept: ConceptTensor,
        problem: ProblemTensor,
        keywords: Optional[str] = None,
        question: Optional[str] = None,
    ) -> float:
        """Use a failed attempt as a targeted training signal.

        We train the solver (and distillation pipeline) to produce solutions 
        aligned with the problem's own formal specification and constraints,
        guiding it to the mathematically correct manifold.
        """
        self.solver.train()
        if self.pipeline is not None:
            self.pipeline.text_encoder.train()
            self.pipeline.concept_head.train()
            self.pipeline.problem_head.train()

        # Differentiable forward pass for end-to-end backpropagation
        if self.pipeline is not None and keywords is not None and question is not None:
            c = self.pipeline.extract_concepts_differentiable(keywords)
            p = self.pipeline.extract_problems_differentiable(question)
        else:
            c = concept.to(self.device)
            p = problem.to(self.device)

        spec_vec = p.get_formal_specification()

        pred_solution = self.solver([c], p)
        pred = pred_solution.result_tensor

        # Recommendation: Aspect-specific training target to preserve structured partitioning
        pred_proj = pred[0]  # First aspect (ontological scaffolding) verified by spec
        loss_spec = F.mse_loss(pred_proj, spec_vec)

        # Recommendation: Apply constraint violation penalty to prevent boundary violations
        constraints = p.get_constraint_vector()
        limitations = c.limitations_risks
        violation = self.solver.constraint_mask.compute_violation_score(constraints, limitations)
        loss = loss_spec + 0.5 * violation.mean()

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return loss.item()

    def consolidate(self) -> float:
        """Distill RAG knowledge into model weights via replay training.

        Replays stored experiences to strengthen the solver's internal representations.
        """
        candidates = self.rag.consolidate(threshold=2)
        if not candidates:
            return 0.0

        data = self.rag.get_consolidation_data(candidates)
        self.solver.train()
        if self.pipeline is not None:
            self.pipeline.text_encoder.train()
            self.pipeline.concept_head.train()
            self.pipeline.problem_head.train()

        total_loss = 0.0
        for _ in range(self.cfg.consolidation_epochs):
            for concept, problem, solution in data:
                c = concept.to(self.device)
                p = problem.to(self.device)
                spec_vec = p.get_formal_specification()

                pred_solution = self.solver([c], p)
                pred = pred_solution.result_tensor

                # Aspect-specific training and constraint violation penalties in consolidation
                pred_proj = pred[0]
                loss_spec = F.mse_loss(pred_proj, spec_vec)

                constraints = p.get_constraint_vector()
                limitations = c.limitations_risks
                violation = self.solver.constraint_mask.compute_violation_score(constraints, limitations)
                loss = loss_spec + 0.5 * violation.mean()

                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()
                total_loss += loss.item()

        avg_loss = total_loss / (self.cfg.consolidation_epochs * len(data))
        self.stats.consolidation_count += 1
        return avg_loss

    def run(
        self,
        problem_generator: Optional[Callable[[], Tuple[ConceptTensor, ProblemTensor]]] = None,
    ) -> LearningStats:
        """Run the full self-learning loop.

        Args:
            problem_generator: Callable that yields (concept, problem) pairs.
                If None, uses random tensors for demonstration.

        Returns:
            LearningStats with history.
        """
        def _default_generator() -> Tuple[ConceptTensor, ProblemTensor]:
            return (
                ConceptTensor.random(d=self.cfg.element_dim),
                ProblemTensor.random(d=self.cfg.element_dim),
            )

        gen = problem_generator or _default_generator

        for step in range(1, self.cfg.max_iterations + 1):
            concept, problem = gen()

            solution, passed, _ = self.solve_and_verify(concept, problem)
            self.stats.record_attempt(passed)

            if passed:
                self._store_success(concept, problem, solution)
            else:
                loss = self._train_on_failure(concept, problem)
                self.stats.loss_history.append(loss)

            self.stats.rag_size_history.append(self.rag.num_entries)

            # Periodic consolidation
            if step % self.cfg.consolidation_interval == 0:
                c_loss = self.consolidate()
                logger.info(f"Consolidation at step {step}: loss={c_loss:.4f}")

            if step % self.cfg.log_every == 0:
                print(
                    f"Step {step}/{self.cfg.max_iterations}  "
                    f"success_rate={self.stats.success_rate:.2%}  "
                    f"RAG_size={self.rag.num_entries}  "
                    f"consolidations={self.stats.consolidation_count}"
                )

        return self.stats


if __name__ == "__main__":
    import sys
    # Quick sanity test run
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — Self-Learning Loop Integration Test")
    print("=" * 60)

    cfg = SelfLearningConfig(
        max_iterations=10,
        consolidation_interval=5,
        log_every=2,
        element_dim=128
    )
    loop = SelfLearningLoop(cfg)
    # Store a dummy success entry first to verify retrieve / train on failure / consolidate works
    c = ConceptTensor.random(d=128)
    p = ProblemTensor.random(d=128)
    s = SolutionTensor.empty(d=128)
    # Artificially force verification pass for testing
    s.verification_passed = True
    loop._store_success(c, p, s)

    print("Running loop...")
    stats = loop.run()

    print(f"\n--- Final Stats ---")
    print(f"  Total attempts:    {stats.total_attempts}")
    print(f"  Successes:         {stats.successes}")
    print(f"  Failures:          {stats.failures}")
    print(f"  Final success rate: {stats.success_rate:.2%}")
    print(f"  RAG size:          {loop.rag.num_entries}")
    print(f"  Consolidations:    {stats.consolidation_count}")

    print("\nDone [OK]")
