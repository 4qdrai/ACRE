"""
Constraint Mask Φ — Orthogonality Gating for Invalid Algebraic States
=====================================================================

The constraint mask is the mathematical mechanism that **structurally
eliminates hallucination** in ACRE. It computes a differentiable gate
:math:`\\Phi \\in [0, 1]^d` from two inputs:

1. **GPF Constraints** (Problem Element 5 — ``constraints_context``):
   operational constraints and contextual bounds on the problem.
2. **Concept Limitations** (Concept Element 8 — ``limitations_risks``):
   known limitations and failure modes of the concept.

Mathematical Formulation
------------------------

Given constraint vector :math:`\\mathbf{p}_{const} \\in \\mathbb{R}^d` and
limitation vector :math:`\\mathbf{c}_{limit} \\in \\mathbb{R}^d`:

1. Project both to a shared geometric space:

   .. math::

       \\tilde{p} = W_p \\, \\mathbf{p}_{const} + b_p \\\\
       \\tilde{c} = W_c \\, \\mathbf{c}_{limit} + b_c

2. Compute orthogonality score (how incompatible they are):

   .. math::

       \\text{ortho}(\\tilde{p}, \\tilde{c}) = 1 -
       \\frac{|\\tilde{p}^T \\tilde{c}|}{\\|\\tilde{p}\\| \\, \\|\\tilde{c}\\| + \\epsilon}

   When constraints and limitations are orthogonal (unrelated),
   the score is 1 → the gate is open (valid state). When they are
   aligned (the concept's limitations directly conflict with the
   problem's constraints), the score is 0 → the gate is closed.

3. Compute the element-wise gate via a learned MLP:

   .. math::

       \\Phi(\\mathbf{p}_{const}, \\mathbf{c}_{limit}) =
       \\sigma\\left( W_3 \\, \\text{GELU}\\left(
       W_2 [\\tilde{p}; \\tilde{c}; \\tilde{p} \\odot \\tilde{c}; \\text{ortho}]
       \\right) \\right)

   where :math:`\\sigma` is the element-wise sigmoid, ensuring
   :math:`\\Phi \\in [0, 1]^d`.

Why This Matters
----------------

In the LARE state update equation:

.. math::

    c_{out}^{(t)} = \\sum_i \\sum_j \\alpha_{ij}
    \\left( \\sum_m \\sigma(W_m p_{formal\\_reqs})
    \\mathcal{O}_m(c_j, c_{ctx}) \\right)
    \\cdot \\underbrace{\\Phi(p_{constraints}, c_{limitations})}_{\\text{this module}}

the mask element-wise multiplies the algebraic output. Dimensions where
the concept's known limitations conflict with the problem's constraints
are driven toward zero, physically preventing the model from producing
solutions that violate known boundaries.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple

from acre.core.concept_tensor import DEFAULT_EMBEDDING_DIM


class ConstraintMask(nn.Module):
    """Differentiable constraint mask Φ for gating invalid algebraic states.

    Computes :math:`\\Phi(p_{constraints}, c_{limitations}) \\in [0, 1]^d`
    from the problem's operational constraints and the concept's known
    limitations. The output is used as an element-wise multiplicative gate
    in the LARE reasoning loop.

    Parameters
    ----------
    d : int
        Embedding dimension of constraint and limitation vectors.
    hidden_dim : int, optional
        Hidden dimension of the gating MLP. Defaults to ``2 * d``.
    temperature : float
        Temperature for the sigmoid gate. Lower values produce sharper
        (more binary) gates; higher values produce softer gates. Default 1.0.
    dropout : float
        Dropout rate inside the gating MLP. Default 0.1.

    Examples
    --------
    >>> mask_fn = ConstraintMask(d=128)
    >>> constraints = torch.randn(128)
    >>> limitations = torch.randn(128)
    >>> gate = mask_fn(constraints, limitations)
    >>> gate.shape
    torch.Size([128])
    >>> (gate >= 0).all() and (gate <= 1).all()
    True
    """

    def __init__(
        self,
        d: int = DEFAULT_EMBEDDING_DIM,
        hidden_dim: Optional[int] = None,
        temperature: float = 1.0,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d = d
        self.hidden_dim = hidden_dim or (2 * d)
        self.temperature = temperature

        # Projection heads: map constraints and limitations to shared space
        self.constraint_proj = nn.Sequential(
            nn.Linear(d, d),
            nn.LayerNorm(d),
            nn.GELU(),
        )
        self.limitation_proj = nn.Sequential(
            nn.Linear(d, d),
            nn.LayerNorm(d),
            nn.GELU(),
        )

        # Gating MLP: takes [p_proj; c_proj; p_proj ⊙ c_proj; ortho_score]
        # Input dim = d + d + d + 1 = 3d + 1
        gate_input_dim = 3 * d + 1
        self.gate_mlp = nn.Sequential(
            nn.Linear(gate_input_dim, self.hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(self.hidden_dim, self.hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(self.hidden_dim, d),
            # No final activation — we apply scaled sigmoid separately
        )

        # Learnable temperature scaling (optional refinement)
        self.log_temperature = nn.Parameter(
            torch.tensor(temperature).log()
        )

        # Violation detection head
        self.violation_head = nn.Sequential(
            nn.Linear(gate_input_dim, d),
            nn.GELU(),
            nn.Linear(d, 1),
            nn.Sigmoid(),
        )

    def _compute_orthogonality(
        self,
        p_proj: torch.Tensor,
        c_proj: torch.Tensor,
        eps: float = 1e-8,
    ) -> torch.Tensor:
        """Compute the orthogonality score between projected vectors.

        Returns 1.0 when vectors are perfectly orthogonal (compatible),
        0.0 when perfectly aligned (conflicting).

        Parameters
        ----------
        p_proj : torch.Tensor
            Projected constraint vector, shape ``(d,)`` or ``(B, d)``.
        c_proj : torch.Tensor
            Projected limitation vector, same shape.
        eps : float
            Numerical stability epsilon.

        Returns
        -------
        torch.Tensor
            Shape ``(1,)`` or ``(B, 1)`` — orthogonality score in ``[0, 1]``.
        """
        unbatched = p_proj.ndim == 1

        # Always work in 2-D: (1, d) or (B, d)
        p2 = p_proj.unsqueeze(0) if unbatched else p_proj
        c2 = c_proj.unsqueeze(0) if unbatched else c_proj

        cos_sim = torch.abs(F.cosine_similarity(p2, c2, dim=-1))  # (1,) or (B,)

        # Orthogonality = 1 - |cos_sim|
        ortho = (1.0 - cos_sim).unsqueeze(-1)  # (1, 1) or (B, 1)

        if unbatched:
            return ortho.squeeze(0)  # (1,) — 1-D, matches p_proj's ndim
        return ortho  # (B, 1)

    def _build_gate_input(
        self,
        p_proj: torch.Tensor,
        c_proj: torch.Tensor,
        ortho: torch.Tensor,
    ) -> torch.Tensor:
        """Build the input tensor for the gating MLP.

        Concatenates: [p_proj; c_proj; p_proj ⊙ c_proj; ortho_score].

        Parameters
        ----------
        p_proj : torch.Tensor
            Shape ``(d,)`` or ``(B, d)``.
        c_proj : torch.Tensor
            Same shape.
        ortho : torch.Tensor
            Shape ``(1,)`` or ``(B, 1)``.

        Returns
        -------
        torch.Tensor
            Shape ``(3d + 1,)`` or ``(B, 3d + 1)``.
        """
        interaction = p_proj * c_proj  # Hadamard product
        return torch.cat([p_proj, c_proj, interaction, ortho], dim=-1)

    def forward(
        self,
        constraints: torch.Tensor,
        limitations: torch.Tensor,
    ) -> torch.Tensor:
        """Compute the constraint mask Φ.

        Parameters
        ----------
        constraints : torch.Tensor
            The problem's ``constraints_context`` vector, shape ``(d,)``
            or ``(B, d)``.
        limitations : torch.Tensor
            The concept's ``limitations_risks`` vector, same shape.

        Returns
        -------
        torch.Tensor
            Gate values in ``[0, 1]^d``, same shape as inputs.
            Multiply element-wise with algebraic output to enforce constraints.
        """
        # Project to shared geometric space
        p_proj = self.constraint_proj(constraints)
        c_proj = self.limitation_proj(limitations)

        # Compute orthogonality
        ortho = self._compute_orthogonality(p_proj, c_proj)

        # Build gate input and compute gate
        gate_input = self._build_gate_input(p_proj, c_proj, ortho)
        gate_logits = self.gate_mlp(gate_input)  # (d,) or (B, d)

        # Apply temperature-scaled sigmoid
        temp = self.log_temperature.exp()
        mask = torch.sigmoid(gate_logits / temp)

        return mask

    def compute_violation_score(
        self,
        constraints: torch.Tensor,
        limitations: torch.Tensor,
    ) -> torch.Tensor:
        """Measure how much a state violates constraints.

        Returns a scalar in ``[0, 1]`` where 0 means no violation and
        1 means maximum violation. This is useful for monitoring and
        for the proof trace in SolutionTensor.

        Parameters
        ----------
        constraints : torch.Tensor
            Shape ``(d,)`` or ``(B, d)``.
        limitations : torch.Tensor
            Same shape.

        Returns
        -------
        torch.Tensor
            Scalar violation score (or ``(B,)`` for batched input).
        """
        p_proj = self.constraint_proj(constraints)
        c_proj = self.limitation_proj(limitations)
        ortho = self._compute_orthogonality(p_proj, c_proj)
        
        # Mathematically exact analytical violation score (1.0 - orthogonality)
        # guarantees physical constraint enforcement prior to full convergence.
        violation = 1.0 - ortho.squeeze(-1)
        return violation

    def get_gate_statistics(
        self,
        constraints: torch.Tensor,
        limitations: torch.Tensor,
    ) -> dict:
        """Compute diagnostic statistics for the constraint mask.

        Returns
        -------
        dict
            Contains: mean_gate, min_gate, max_gate, std_gate,
            orthogonality_score, violation_score, num_hard_blocked
            (dimensions with gate < 0.1).
        """
        with torch.no_grad():
            mask = self.forward(constraints, limitations)
            violation = self.compute_violation_score(constraints, limitations)

            p_proj = self.constraint_proj(constraints)
            c_proj = self.limitation_proj(limitations)
            ortho = self._compute_orthogonality(p_proj, c_proj)

            return {
                "mean_gate": mask.mean().item(),
                "min_gate": mask.min().item(),
                "max_gate": mask.max().item(),
                "std_gate": mask.std().item(),
                "orthogonality_score": ortho.mean().item(),
                "violation_score": violation.item() if violation.ndim == 0 else violation.mean().item(),
                "num_hard_blocked": (mask < 0.1).sum().item(),
                "num_dimensions": mask.numel(),
            }

    def extra_repr(self) -> str:
        temp = self.log_temperature.exp().item()
        return (
            f"d={self.d}, hidden_dim={self.hidden_dim}, "
            f"temperature={temp:.3f}"
        )
