"""
Concept Algebra — Differentiable Algebraic Operations over Structured Tensors
=============================================================================

The **ConceptAlgebra** implements the four fundamental algebraic operations
that enable structured reasoning in the F-LACA latent space:

.. list-table:: Algebraic Operations
   :header-rows: 1

   * - Symbol
     - Name
     - Signature
     - Description
   * - :math:`\\oplus`
     - Compose
     - :math:`C \\times C \\to C`
     - Bilinear composition of two concepts
   * - :math:`\\otimes`
     - Bind
     - :math:`P \\times C \\to \\mathbb{R}^d`
     - Operator-operand binding of a problem to a concept
   * - :math:`\\ominus`
     - Difference
     - :math:`C \\times C \\to C`
     - Concept subtraction (what c1 has that c2 lacks)
   * - :math:`\\Pi`
     - Project
     - :math:`\\mathbb{R}^d \\times P \\to S`
     - Project a binding result into solution space

Mathematical Details
--------------------

**Compose** (:math:`\\oplus`):

Each element *k* of the composed concept is computed via a learned bilinear map:

.. math::

    (c_1 \\oplus c_2)_k = \\sigma\\left( c_{1,k}^T W_k^{\\oplus} c_{2,k} + b_k \\right)

where :math:`W_k^{\\oplus} \\in \\mathbb{R}^{d \\times d}` is a learnable weight
matrix for element *k*.

**Bind** (:math:`\\otimes`):

The binding operation applies the problem as an *operator* to the concept
as an *operand*:

.. math::

    p \\otimes c = \\sum_{k=1}^{10} \\text{softmax}(W_{gate} p_k) \\cdot
    (W_{bind,k} c_k + U_{bind,k} p_k)

**Difference** (:math:`\\ominus`):

.. math::

    (c_1 \\ominus c_2)_k = W_k^{\\ominus}(c_{1,k} - c_{2,k}) +
    W_k^{int}(c_{1,k} \\odot c_{2,k})

Captures both the linear difference and the interaction term.

**Analogy**: Uses the classic ``c1 - c2 + c3`` pattern, each passed
through learned transformations.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Dict, List, Optional, Tuple

from acre.core.concept_tensor import ConceptTensor, NUM_CONCEPT_ELEMENTS, DEFAULT_EMBEDDING_DIM
from acre.core.problem_tensor import ProblemTensor, NUM_PROBLEM_ELEMENTS
from acre.core.solution_tensor import SolutionTensor, ProofStep

import time


class BilinearElementOp(nn.Module):
    """A learnable bilinear operation applied independently to each of the
    10 concept elements.

    For element *k*, computes:

    .. math::

        y_k = \\sigma(x_{1,k}^T W_k x_{2,k} + b_k) + \\text{skip}(x_{1,k}, x_{2,k})

    Parameters
    ----------
    d : int
        Embedding dimension per element.
    num_elements : int
        Number of elements (default 10).
    activation : str
        Activation function: 'gelu', 'relu', 'tanh', or 'none'.
    """

    def __init__(
        self,
        d: int = DEFAULT_EMBEDDING_DIM,
        num_elements: int = NUM_CONCEPT_ELEMENTS,
        activation: str = "gelu",
    ) -> None:
        super().__init__()
        self.d = d
        self.num_elements = num_elements

        # One bilinear layer per element
        self.bilinears = nn.ModuleList([
            nn.Bilinear(d, d, d, bias=True) for _ in range(num_elements)
        ])

        # Skip connection: linear projection of concatenated inputs
        self.skips = nn.ModuleList([
            nn.Linear(2 * d, d, bias=False) for _ in range(num_elements)
        ])

        # Layer norms for stability
        self.norms = nn.ModuleList([
            nn.LayerNorm(d) for _ in range(num_elements)
        ])

        self._activation_name = activation
        self.activation = {
            "gelu": F.gelu,
            "relu": F.relu,
            "tanh": torch.tanh,
            "none": lambda x: x,
        }[activation]

    def forward(self, x1: torch.Tensor, x2: torch.Tensor) -> torch.Tensor:
        """Apply the bilinear op element-wise.

        Parameters
        ----------
        x1, x2 : torch.Tensor
            Shape ``(10, d)`` or ``(B, 10, d)``.

        Returns
        -------
        torch.Tensor
            Same shape as inputs.
        """
        batched = x1.ndim == 3
        if not batched:
            x1 = x1.unsqueeze(0)
            x2 = x2.unsqueeze(0)

        outputs = []
        for k in range(self.num_elements):
            bilinear_out = self.bilinears[k](x1[:, k, :], x2[:, k, :])
            skip_out = self.skips[k](torch.cat([x1[:, k, :], x2[:, k, :]], dim=-1))
            combined = self.activation(bilinear_out) + skip_out
            outputs.append(self.norms[k](combined))

        result = torch.stack(outputs, dim=1)  # (B, 10, d)
        return result if batched else result.squeeze(0)


class ConceptAlgebra(nn.Module):
    """Differentiable algebra over ConceptTensors, ProblemTensors, and SolutionTensors.

    Implements the four core operations (⊕, ⊗, ⊖, Π) as learnable
    ``nn.Module`` components, enabling end-to-end gradient flow through
    algebraic reasoning.

    Parameters
    ----------
    d : int
        Embedding dimension per element (default 128).
    num_operators : int
        Number of algebraic operator heads for the bind operation (default 4).
        These correspond to the :math:`\\mathcal{O}_m` in the LARE equation.
    solution_dim : int
        Dimension of the projected solution space (default 1280 = 10 * 128).

    Examples
    --------
    >>> algebra = ConceptAlgebra(d=64)
    >>> c1 = ConceptTensor.random(d=64)
    >>> c2 = ConceptTensor.random(d=64)
    >>> c3 = algebra.compose(c1, c2)
    >>> c3.dim
    64
    """

    def __init__(
        self,
        d: int = DEFAULT_EMBEDDING_DIM,
        num_operators: int = 4,
        solution_dim: Optional[int] = None,
    ) -> None:
        super().__init__()
        self.d = d
        self.num_operators = num_operators
        self.solution_dim = solution_dim or (NUM_CONCEPT_ELEMENTS * d)

        # ── ⊕ Compose ────────────────────────────────────────────
        self.compose_op = BilinearElementOp(d, NUM_CONCEPT_ELEMENTS, activation="gelu")

        # ── ⊖ Difference ─────────────────────────────────────────
        self.diff_linear = nn.ModuleList([
            nn.Linear(d, d) for _ in range(NUM_CONCEPT_ELEMENTS)
        ])
        self.diff_interaction = nn.ModuleList([
            nn.Linear(d, d) for _ in range(NUM_CONCEPT_ELEMENTS)
        ])
        self.diff_norms = nn.ModuleList([
            nn.LayerNorm(d) for _ in range(NUM_CONCEPT_ELEMENTS)
        ])

        # ── ⊗ Bind: multiple operator heads ──────────────────────
        # Gate: problem requirements → operator weights
        self.bind_gate = nn.Linear(d, num_operators)

        # Per-operator transforms
        self.bind_concept_projs = nn.ModuleList([
            nn.Linear(NUM_CONCEPT_ELEMENTS * d, d) for _ in range(num_operators)
        ])
        self.bind_problem_projs = nn.ModuleList([
            nn.Linear(NUM_PROBLEM_ELEMENTS * d, d) for _ in range(num_operators)
        ])
        self.bind_combine = nn.ModuleList([
            nn.Linear(2 * d, d) for _ in range(num_operators)
        ])
        self.bind_norm = nn.LayerNorm(d)

        # ── Π Projection to solution space ────────────────────────
        self.project_mlp = nn.Sequential(
            nn.Linear(d + NUM_PROBLEM_ELEMENTS * d, self.solution_dim),
            nn.GELU(),
            nn.LayerNorm(self.solution_dim),
            nn.Linear(self.solution_dim, self.solution_dim),
        )

        # Confidence head: predicts confidence from solution
        self.confidence_head = nn.Sequential(
            nn.Linear(self.solution_dim, d),
            nn.GELU(),
            nn.Linear(d, 1),
            nn.Sigmoid(),
        )

        # ── Analogy transform ────────────────────────────────────
        self.analogy_transform = nn.Linear(d, d)
        self.analogy_norm = nn.LayerNorm(d)

    # ── ⊕ Compose ────────────────────────────────────────────────

    def compose(
        self, c1: ConceptTensor | torch.Tensor, c2: ConceptTensor | torch.Tensor
    ) -> ConceptTensor | torch.Tensor:
        """Compose two concepts: :math:`c_1 \\oplus c_2`.

        Creates a new concept that integrates the knowledge of both
        inputs via element-wise bilinear combination. Supports both
        ConceptTensor objects and raw batched/unbatched tensors.
        """
        t1 = c1.to_tensor() if isinstance(c1, ConceptTensor) else c1
        t2 = c2.to_tensor() if isinstance(c2, ConceptTensor) else c2
        result = self.compose_op(t1, t2)
        if isinstance(c1, ConceptTensor):
            return ConceptTensor.from_tensor(result)
        return result

    # ── ⊗ Bind ───────────────────────────────────────────────────

    def bind(
        self, problem: ProblemTensor | torch.Tensor, concept: ConceptTensor | torch.Tensor
    ) -> torch.Tensor:
        """Bind a problem to a concept: :math:`p \\otimes c`.

        The problem acts as an *operator* that extracts task-relevant
        information from the concept *operand*. Multiple operator heads
        are gated by the problem's formal requirements. Supports both
        ProblemTensor/ConceptTensor objects and raw batched/unbatched tensors.
        """
        p_tensor = problem.to_tensor() if isinstance(problem, ProblemTensor) else problem
        c_tensor = concept.to_tensor() if isinstance(concept, ConceptTensor) else concept

        batched = p_tensor.ndim == 3
        if not batched:
            p_tensor = p_tensor.unsqueeze(0)
            c_tensor = c_tensor.unsqueeze(0)

        B = p_tensor.shape[0]
        p_flat = p_tensor.reshape(B, -1)   # (B, 10*d)
        c_flat = c_tensor.reshape(B, -1)   # (B, 10*d)

        # Get formal requirements (element index 2, 0-indexed)
        if isinstance(problem, ProblemTensor):
            p_reqs = problem.get_formal_requirements()
            if p_reqs.ndim == 1:
                p_reqs = p_reqs.unsqueeze(0)
        else:
            p_reqs = p_tensor[:, 2, :]     # (B, d)

        gate_logits = self.bind_gate(p_reqs)  # (B, num_operators)
        gate_weights = F.softmax(gate_logits, dim=-1)

        # Apply each operator head and combine
        bound = torch.zeros(B, self.d, device=p_tensor.device, dtype=p_tensor.dtype)
        for m in range(self.num_operators):
            c_proj = self.bind_concept_projs[m](c_flat)   # (B, d)
            p_proj = self.bind_problem_projs[m](p_flat)   # (B, d)
            combined = self.bind_combine[m](
                torch.cat([c_proj, p_proj], dim=-1)
            )  # (B, d)
            bound = bound + gate_weights[:, m].unsqueeze(-1) * combined

        bound = self.bind_norm(bound)
        return bound if batched else bound.squeeze(0)

    # ── ⊖ Difference ─────────────────────────────────────────────

    def difference(
        self, c1: ConceptTensor | torch.Tensor, c2: ConceptTensor | torch.Tensor
    ) -> ConceptTensor | torch.Tensor:
        """Concept difference: :math:`c_1 \\ominus c_2`.

        Captures what ``c1`` has that ``c2`` lacks, using both the
        linear difference and a Hadamard interaction term. Supports both
        ConceptTensor objects and raw batched/unbatched tensors.
        """
        t1 = c1.to_tensor() if isinstance(c1, ConceptTensor) else c1
        t2 = c2.to_tensor() if isinstance(c2, ConceptTensor) else c2

        batched = t1.ndim == 3
        if not batched:
            t1 = t1.unsqueeze(0)
            t2 = t2.unsqueeze(0)

        B = t1.shape[0]
        outputs = []
        for k in range(NUM_CONCEPT_ELEMENTS):
            e1 = t1[:, k, :]
            e2 = t2[:, k, :]
            diff_part = self.diff_linear[k](e1 - e2)
            interaction = self.diff_interaction[k](e1 * e2)
            combined = self.diff_norms[k](diff_part + interaction)
            outputs.append(combined)

        result = torch.stack(outputs, dim=1)  # (B, 10, d)
        if isinstance(c1, ConceptTensor):
            return ConceptTensor.from_tensor(result.squeeze(0))
        return result if batched else result.squeeze(0)

    # ── Π Projection to Solution ──────────────────────────────────

    def project_to_solution(
        self,
        binding_result: torch.Tensor,
        problem: ProblemTensor | torch.Tensor,
    ) -> SolutionTensor | torch.Tensor:
        """Project a binding result into solution space: :math:`\\Pi`.

        Takes the output of ``bind()`` and the original problem,
        and produces a SolutionTensor (or raw tensor if batched) with confidence estimation.
        """
        p_tensor = problem.to_tensor() if isinstance(problem, ProblemTensor) else problem

        batched = binding_result.ndim == 2
        if not batched:
            binding_result = binding_result.unsqueeze(0)
            p_tensor = p_tensor.unsqueeze(0)

        B = binding_result.shape[0]
        p_flat = p_tensor.reshape(B, -1)
        proj_input = torch.cat([binding_result, p_flat], dim=-1)
        solution_vec = self.project_mlp(proj_input)  # (B, solution_dim)

        if isinstance(problem, ProblemTensor):
            # Estimate confidence
            confidence = self.confidence_head(solution_vec).squeeze(-1)  # (B,)

            # Reshape to (10, d) if solution_dim == 10*d
            if self.solution_dim == NUM_CONCEPT_ELEMENTS * self.d:
                result = solution_vec[0].reshape(NUM_CONCEPT_ELEMENTS, self.d)
            else:
                result = solution_vec[0]

            return SolutionTensor(
                resolution_steps=[binding_result[0].detach().clone()],
                applied_concepts=[],
                applied_operations=["project"],
                result_tensor=result,
                confidence=confidence[0].item(),
                proof_trace=[
                    ProofStep(
                        step_index=0,
                        operation="project",
                        operand_indices=[],
                        result_norm=result.norm().item(),
                        timestamp=time.time(),
                    )
                ],
            )
        else:
            if self.solution_dim == NUM_CONCEPT_ELEMENTS * self.d:
                return solution_vec.reshape(B, NUM_CONCEPT_ELEMENTS, self.d)
            return solution_vec

    # ── Analogy ──────────────────────────────────────────────────

    def analogy(
        self,
        c1: ConceptTensor,
        c2: ConceptTensor,
        c3: ConceptTensor,
    ) -> ConceptTensor:
        """Analogical reasoning: :math:`c_1 - c_2 + c_3`.

        Implements the classic vector analogy pattern with learned
        transformations for each element. Think of it as: "c1 is to c2
        as c3 is to ?". The answer captures the relational structure
        between c1 and c2, applied to c3.

        Parameters
        ----------
        c1, c2, c3 : ConceptTensor

        Returns
        -------
        ConceptTensor
            The analogical result.
        """
        # c1 - c2 + c3, with learned transform
        t1 = c1.to_tensor()  # (10, d)
        t2 = c2.to_tensor()
        t3 = c3.to_tensor()

        raw_analogy = t1 - t2 + t3  # (10, d)

        # Apply learned transform per-element
        result_elements = {}
        for k, name in enumerate(ConceptTensor.ELEMENT_NAMES):
            transformed = self.analogy_transform(raw_analogy[k])
            result_elements[name] = self.analogy_norm(transformed)

        return ConceptTensor(**result_elements)

    # ── Regularization Losses ────────────────────────────────────

    def algebraic_consistency_loss(
        self,
        c1: ConceptTensor,
        c2: ConceptTensor,
        c3: ConceptTensor,
    ) -> torch.Tensor:
        """Compute algebraic consistency regularization.

        Enforces structural properties of the algebra:

        1. **Commutativity bias**: :math:`\\|c_1 \\oplus c_2 - c_2 \\oplus c_1\\|`
           should be small (soft, not forced).
        2. **Associativity bias**: :math:`\\|(c_1 \\oplus c_2) \\oplus c_3
           - c_1 \\oplus (c_2 \\oplus c_3)\\|` should be small.
        3. **Identity**: :math:`c_1 \\ominus c_1 \\approx \\mathbf{0}`.

        Parameters
        ----------
        c1, c2, c3 : ConceptTensor
            Three concepts for testing algebraic properties.

        Returns
        -------
        torch.Tensor
            Scalar loss value.
        """
        # 1. Commutativity bias
        c12 = self.compose(c1, c2).to_tensor()
        c21 = self.compose(c2, c1).to_tensor()
        comm_loss = (c12 - c21).norm()

        # 2. Associativity bias
        c12_3 = self.compose(
            self.compose(c1, c2), c3
        ).to_tensor()
        c1_23 = self.compose(
            c1, self.compose(c2, c3)
        ).to_tensor()
        assoc_loss = (c12_3 - c1_23).norm()

        # 3. Self-difference ≈ zero
        c_self_diff = self.difference(c1, c1).to_tensor()
        identity_loss = c_self_diff.norm()

        return 0.3 * comm_loss + 0.3 * assoc_loss + 0.4 * identity_loss

    def forward(
        self,
        problem: ProblemTensor,
        concept: ConceptTensor,
    ) -> SolutionTensor:
        """Full forward pass: bind + project.

        Convenience method that runs ``bind()`` followed by
        ``project_to_solution()``.

        Parameters
        ----------
        problem : ProblemTensor
        concept : ConceptTensor

        Returns
        -------
        SolutionTensor
        """
        binding = self.bind(problem, concept)
        return self.project_to_solution(binding, problem)
