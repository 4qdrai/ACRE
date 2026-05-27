"""
Solution Tensor — Formalized Solution Space with Proof Traces
=============================================================

A **SolutionTensor** represents the output of the LARE algebraic reasoning
pipeline. Unlike traditional model outputs (a sequence of tokens), a Solution
is a *structured algebraic object* that records:

1. The ordered sequence of algebraic resolution steps that produced it.
2. Which Concepts were applied and via which algebraic operations.
3. The final result tensor in latent space.
4. A confidence score and formal verification status.
5. A full proof trace for auditability.

Mathematical Formulation
------------------------
Given a Problem :math:`\\mathbf{p}` and a set of Concepts
:math:`\\{\\mathbf{c}_j\\}`, the LARE produces:

.. math::

    \\mathbf{s} = \\Pi\\left(
        \\bigoplus_{t=1}^{T} \\mathcal{O}_{m_t}(\\mathbf{c}_{j_t}, \\mathbf{c}_{ctx})
        \\cdot \\Phi(\\mathbf{p}_{constraints}, \\mathbf{c}_{limitations})
    \\right)

where :math:`\\Pi` is the projection to solution space, and each step
:math:`t` is recorded in the proof trace with the operation type, operands,
and intermediate result.

Verification
------------
The ``verify()`` method checks the solution against the Problem's formal
specification and verification code, producing a binary pass/fail plus a
detailed violation report.
"""

from __future__ import annotations

import torch
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
import json
import time


# ────────────────────────────────────────────────────────────────────
# Algebraic operation names (canonical vocabulary)
# ────────────────────────────────────────────────────────────────────
VALID_OPERATIONS = frozenset({
    "compose",       # ⊕  — ConceptAlgebra.compose
    "bind",          # ⊗  — ConceptAlgebra.bind
    "difference",    # ⊖  — ConceptAlgebra.difference
    "project",       # Π  — ConceptAlgebra.project_to_solution
    "analogy",       # Analogical reasoning
    "identity",      # No-op / passthrough
    "mask",          # Constraint masking via Φ
    "refine",        # Iterative refinement step
})


@dataclass
class ProofStep:
    """A single step in the formal proof trace.

    Records what algebraic operation was applied, which operands were used,
    and the intermediate result norm (as a lightweight fingerprint).

    Attributes
    ----------
    step_index : int
        Zero-based position in the proof sequence.
    operation : str
        Name of the algebraic operation (from ``VALID_OPERATIONS``).
    operand_indices : list of int
        Indices of the Concepts / tensors involved.
    result_norm : float
        L2 norm of the intermediate result (fingerprint, not full tensor).
    constraint_violation : float
        Φ mask violation score at this step (0.0 = fully valid).
    timestamp : float
        Wall-clock time when this step was computed.
    metadata : dict
        Any additional info (e.g., convergence delta, attention weights).
    """

    step_index: int
    operation: str
    operand_indices: List[int]
    result_norm: float
    constraint_violation: float = 0.0
    timestamp: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation not in VALID_OPERATIONS:
            raise ValueError(
                f"Unknown operation '{self.operation}'. "
                f"Valid: {sorted(VALID_OPERATIONS)}"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dictionary."""
        return {
            "step_index": self.step_index,
            "operation": self.operation,
            "operand_indices": self.operand_indices,
            "result_norm": self.result_norm,
            "constraint_violation": self.constraint_violation,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProofStep":
        """Deserialize from a plain dictionary."""
        return cls(**data)


@dataclass
class SolutionTensor:
    """A formalized algebraic solution with proof trace.

    Attributes
    ----------
    resolution_steps : list of torch.Tensor
        Ordered sequence of intermediate latent states, one per algebraic
        reasoning step. Each tensor has shape ``(10, d)`` or ``(d,)``
        depending on the operation.
    applied_concepts : list of int
        Indices of the Concepts from the input set that were used.
    applied_operations : list of str
        Names of algebraic operations applied in order.
    result_tensor : torch.Tensor
        The final solution in latent space, shape ``(10, d)`` or ``(d_out,)``.
    confidence : float
        Solution confidence in ``[0, 1]``. Computed from convergence
        behaviour and constraint satisfaction.
    verification_passed : bool or None
        ``True`` if formal verification succeeded, ``False`` if it failed,
        ``None`` if verification has not been run yet.
    proof_trace : list of ProofStep
        Full audit trail of algebraic steps for interpretability.
    metadata : dict
        Additional info (timing, model version, etc.).

    Examples
    --------
    >>> s = SolutionTensor.empty(d=128)
    >>> s.confidence
    0.0
    >>> s.is_verified
    False
    """

    resolution_steps: List[torch.Tensor]
    applied_concepts: List[int]
    applied_operations: List[str]
    result_tensor: torch.Tensor
    confidence: float = 0.0
    verification_passed: Optional[bool] = None
    proof_trace: List[ProofStep] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── Validation ────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"Confidence must be in [0, 1], got {self.confidence}"
            )
        for op in self.applied_operations:
            if op not in VALID_OPERATIONS:
                raise ValueError(
                    f"Unknown operation '{op}'. Valid: {sorted(VALID_OPERATIONS)}"
                )
        if len(self.applied_operations) != len(self.resolution_steps):
            raise ValueError(
                f"Number of operations ({len(self.applied_operations)}) must "
                f"match number of resolution steps ({len(self.resolution_steps)})"
            )

    # ── Properties ────────────────────────────────────────────────

    @property
    def num_steps(self) -> int:
        """Number of algebraic reasoning steps."""
        return len(self.resolution_steps)

    @property
    def is_verified(self) -> bool:
        """Whether formal verification has been run and passed."""
        return self.verification_passed is True

    @property
    def device(self) -> torch.device:
        return self.result_tensor.device

    @property
    def dtype(self) -> torch.dtype:
        return self.result_tensor.dtype

    # ── Verification ──────────────────────────────────────────────

    def verify(self, problem: "ProblemTensor") -> bool:
        """Run formal verification against a ProblemTensor.

        This method checks the solution's result tensor against the
        problem's formal specification and verification code embeddings.
        The verification is geometric: we measure how well the solution
        aligns with the specification and how much it violates the
        constraints.

        Verification criteria:

        1. **Specification alignment**: cosine similarity between
           ``result_tensor`` (flattened) and ``problem.formal_specification``
           must exceed a threshold.
        2. **Constraint satisfaction**: the constraint violation across
           all proof steps must be below threshold.
        3. **Convergence**: the solution must have converged (last two
           steps differ by < ε in L2 norm).

        Parameters
        ----------
        problem : ProblemTensor
            The problem this solution was computed for.

        Returns
        -------
        bool
            ``True`` if all verification criteria pass.
        """
        from acre.core.problem_tensor import ProblemTensor  # avoid circular

        # Criterion 1: Specification alignment
        spec_vec = problem.get_formal_specification()
        result_flat = self.result_tensor.reshape(-1)
        # Project result to spec dimension if needed
        if result_flat.shape[0] != spec_vec.shape[0]:
            # Use first d elements as alignment proxy
            d = spec_vec.shape[0]
            result_proj = result_flat[:d]
        else:
            result_proj = result_flat

        spec_sim = torch.nn.functional.cosine_similarity(
            result_proj.unsqueeze(0),
            spec_vec.unsqueeze(0),
            dim=-1,
        ).item()

        # Criterion 2: Constraint violation
        max_violation = 0.0
        if self.proof_trace:
            max_violation = max(step.constraint_violation for step in self.proof_trace)

        # Criterion 3: Convergence (if multiple steps exist)
        converged = True
        if len(self.resolution_steps) >= 2:
            last = self.resolution_steps[-1].reshape(-1)
            prev = self.resolution_steps[-2].reshape(-1)
            delta = (last - prev).norm().item()
            converged = delta < 1e-2  # Convergence threshold

        # Combined verdict
        passed = (
            spec_sim > 0.1          # Positive alignment with spec
            and max_violation < 0.5  # Constraint violations bounded
            and converged            # Solution converged
        )

        self.verification_passed = passed
        self.metadata["verification_details"] = {
            "spec_similarity": spec_sim,
            "max_constraint_violation": max_violation,
            "converged": converged,
            "timestamp": time.time(),
        }
        return passed

    # ── Human-Readable Output ─────────────────────────────────────

    def to_human_readable(self) -> str:
        """Decode the solution into a human-readable summary.

        Returns a structured text report containing the proof steps,
        applied concepts, confidence, and verification status.

        Returns
        -------
        str
            Multi-line human-readable solution summary.
        """
        lines = [
            "═" * 60,
            "  ACRE Solution Report",
            "═" * 60,
            f"  Steps:        {self.num_steps}",
            f"  Confidence:   {self.confidence:.4f}",
            f"  Verified:     {self.verification_passed}",
            f"  Concepts used: {self.applied_concepts}",
            f"  Result norm:  {self.result_tensor.norm().item():.4f}",
            "─" * 60,
            "  Proof Trace:",
        ]
        for step in self.proof_trace:
            lines.append(
                f"    [{step.step_index}] {step.operation}"
                f"  operands={step.operand_indices}"
                f"  |result|={step.result_norm:.4f}"
                f"  violation={step.constraint_violation:.4f}"
            )
        lines.append("═" * 60)
        return "\n".join(lines)

    def get_proof_summary(self) -> str:
        """Generate a concise summary of the proof trace.

        Returns
        -------
        str
            One-line-per-step summary for quick inspection.
        """
        if not self.proof_trace:
            return "No proof trace recorded."

        parts = []
        for step in self.proof_trace:
            parts.append(
                f"Step {step.step_index}: "
                f"{step.operation}({step.operand_indices}) "
                f"→ |r|={step.result_norm:.3f}"
            )
        return "\n".join(parts)

    # ── Recording Steps ───────────────────────────────────────────

    def record_step(
        self,
        operation: str,
        operand_indices: List[int],
        intermediate_result: torch.Tensor,
        constraint_violation: float = 0.0,
        **extra_metadata: Any,
    ) -> None:
        """Append a new step to the proof trace.

        Parameters
        ----------
        operation : str
            Name of the algebraic operation.
        operand_indices : list of int
            Indices of operands involved.
        intermediate_result : torch.Tensor
            The intermediate result tensor.
        constraint_violation : float
            Φ mask violation score (0.0 = fully valid).
        **extra_metadata
            Additional info to store in the step.
        """
        step = ProofStep(
            step_index=len(self.proof_trace),
            operation=operation,
            operand_indices=operand_indices,
            result_norm=intermediate_result.norm().item(),
            constraint_violation=constraint_violation,
            timestamp=time.time(),
            metadata=dict(extra_metadata),
        )
        self.proof_trace.append(step)
        self.resolution_steps.append(intermediate_result.detach().clone())
        self.applied_operations.append(operation)

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary (tensors become detached clones)."""
        return {
            "resolution_steps": [s.detach().clone() for s in self.resolution_steps],
            "applied_concepts": list(self.applied_concepts),
            "applied_operations": list(self.applied_operations),
            "result_tensor": self.result_tensor.detach().clone(),
            "confidence": self.confidence,
            "verification_passed": self.verification_passed,
            "proof_trace": [s.to_dict() for s in self.proof_trace],
            "metadata": self.metadata,
        }

    # ── Factories ─────────────────────────────────────────────────

    @classmethod
    def empty(
        cls,
        d: int = 128,
        device: Optional[torch.device] = None,
        dtype: torch.dtype = torch.float32,
    ) -> "SolutionTensor":
        """Create an empty solution (no steps, zero result).

        Parameters
        ----------
        d : int
            Embedding dimension.
        device : torch.device, optional
        dtype : torch.dtype
        """
        return cls(
            resolution_steps=[],
            applied_concepts=[],
            applied_operations=[],
            result_tensor=torch.zeros(10, d, device=device, dtype=dtype),
            confidence=0.0,
            verification_passed=None,
            proof_trace=[],
        )

    @classmethod
    def from_result(
        cls,
        result: torch.Tensor,
        confidence: float,
        applied_concepts: Optional[List[int]] = None,
    ) -> "SolutionTensor":
        """Quick-create a solution from a final result tensor.

        Parameters
        ----------
        result : torch.Tensor
            The final solution tensor.
        confidence : float
            Confidence score in [0, 1].
        applied_concepts : list of int, optional
            Concept indices used.
        """
        return cls(
            resolution_steps=[],
            applied_concepts=applied_concepts or [],
            applied_operations=[],
            result_tensor=result,
            confidence=confidence,
        )

    # ── Utility ───────────────────────────────────────────────────

    def to(
        self,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> "SolutionTensor":
        """Move all tensors to the specified device / dtype."""
        kwargs = {}
        if device is not None:
            kwargs["device"] = device
        if dtype is not None:
            kwargs["dtype"] = dtype
        return SolutionTensor(
            resolution_steps=[s.to(**kwargs) for s in self.resolution_steps],
            applied_concepts=list(self.applied_concepts),
            applied_operations=list(self.applied_operations),
            result_tensor=self.result_tensor.to(**kwargs),
            confidence=self.confidence,
            verification_passed=self.verification_passed,
            proof_trace=list(self.proof_trace),
            metadata=dict(self.metadata),
        )

    def __repr__(self) -> str:
        return (
            f"SolutionTensor(steps={self.num_steps}, "
            f"confidence={self.confidence:.3f}, "
            f"verified={self.verification_passed})"
        )
