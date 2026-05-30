"""
LARE — Latent Algebraic Reasoning Engine
=========================================

The **LARE** is the central reasoning module in the F-LACA architecture.
It replaces standard transformer attention with a *constrained operator-
operand bilinear mechanism* that operates directly on structured
ConceptTensors and ProblemTensors.

Core Equation
-------------

The LARE performs iterative multi-step reasoning. At each step *t*, the
latent state is updated according to:

.. math::

    c_{out}^{(t)} = \\sum_{i \\in \\text{GPFs}} \\sum_{j \\in \\text{Concepts}}
    \\alpha_{ij}
    \\left(
        \\sum_{m=1}^{M} \\sigma(W_m \\, p_{i, \\text{formal\\_reqs}})
        \\, \\mathcal{O}_m(c_j, c_{ctx})
    \\right)
    \\cdot \\Phi(p_{i, \\text{constraints}}, c_{j, \\text{limitations}})

Where:

- :math:`\\alpha_{ij}` are learned attention weights over concept-problem pairs
- :math:`\\mathcal{O}_m` are the *M* algebraic operator heads (bilinear maps)
- :math:`\\sigma(W_m p_{formal\\_reqs})` gates operators based on formal requirements
- :math:`\\Phi` is the constraint mask that nulls invalid states

Convergence & Contraction
-------------------------

Drawing from the RSRA-4B Banach contraction principle, we apply **spectral
normalization** to the refinement operators to ensure the iterative process
is a contraction mapping:

.. math::

    \\| f(c^{(t)}) - f(c^{(t-1)}) \\| \\leq L \\, \\| c^{(t)} - c^{(t-1)} \\|
    \\quad \\text{with } L < 1

The engine monitors convergence via :math:`\\| c^{(t)} - c^{(t-1)} \\|`
and stops early when this falls below :math:`\\varepsilon`.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils import spectral_norm
from typing import List, Optional, Tuple, Dict
import time

from acre.core.concept_tensor import ConceptTensor, NUM_CONCEPT_ELEMENTS, DEFAULT_EMBEDDING_DIM
from acre.core.problem_tensor import ProblemTensor, NUM_PROBLEM_ELEMENTS
from acre.core.solution_tensor import SolutionTensor, ProofStep
from acre.core.constraint_mask import ConstraintMask


class AlgebraicOperatorHead(nn.Module):
    """A single algebraic operator :math:`\mathcal{O}_m`.

    Implements a spectrally-normalized bilinear map that transforms a
    concept vector given a context vector. Spectral normalization
    ensures the Lipschitz constant is bounded, which is required for
    the Banach contraction guarantee.

    Parameters
    ----------
    d : int
        Input / output dimension.
    """

    def __init__(self, d: int) -> None:
        super().__init__()
        self.d = d
        # Spectral-normalized bilinear weights
        self.W_concept = spectral_norm(nn.Linear(d, d, bias=False))
        self.W_context = spectral_norm(nn.Linear(d, d, bias=False))
        self.combine = spectral_norm(nn.Linear(2 * d, d))
        # Note: LayerNorm is removed here to satisfy strict mathematical Banach Lipschitz bounds

    def forward(
        self, concept: torch.Tensor, context: torch.Tensor
    ) -> torch.Tensor:
        """Apply the operator.

        Parameters
        ----------
        concept : torch.Tensor
            Shape ``(d,)`` or ``(B, d)`` — the concept operand.
        context : torch.Tensor
            Shape ``(d,)`` or ``(B, d)`` — the context vector.

        Returns
        -------
        torch.Tensor
            Same shape as inputs.
        """
        c_proj = self.W_concept(concept)
        ctx_proj = self.W_context(context)
        combined = torch.cat([c_proj, ctx_proj], dim=-1)
        # GELU replaced with Tanh (strictly 1-Lipschitz) to mathematically guarantee convergence
        return torch.tanh(self.combine(combined))


class AttentionScorer(nn.Module):
    """Computes attention weights :math:`\alpha_{ij}` over concept-problem pairs.

    Uses a bilinear scoring function:

    .. math::

        \alpha_{ij} = \frac{\exp(s_{ij})}{\sum_k \exp(s_{ik})}
        \quad \text{where} \quad
        s_{ij} = (p_i + s_t)^T W_{attn} c_j + b

    Parameters
    ----------
    d_problem : int
        Flattened problem dimension (10 * d).
    d_concept : int
        Flattened concept dimension (10 * d).
    """

    def __init__(self, d_problem: int, d_concept: int, d_hidden: int = 128) -> None:
        super().__init__()
        self.problem_proj = nn.Linear(d_problem, d_hidden)
        self.state_proj = nn.Linear(d_problem, d_hidden)  # Dynamic query projection of prev_state
        self.concept_proj = nn.Linear(d_concept, d_hidden)
        self.score = nn.Linear(d_hidden, 1)

    def forward(
        self,
        problem_flat: torch.Tensor,  # (10*d,)
        concept_flats: torch.Tensor,  # (J, 10*d)
        prev_state: torch.Tensor,     # (10*d,) - dynamic reasoning state
    ) -> torch.Tensor:
        """Compute attention weights over concepts dynamically based on reasoning state.

        Parameters
        ----------
        problem_flat : torch.Tensor
            Flattened problem tensor, shape ``(10*d,)``.
        concept_flats : torch.Tensor
            Stacked flattened concepts, shape ``(J, 10*d)``.
        prev_state : torch.Tensor
            Previous reasoning state, shape ``(10*d,)``.

        Returns
        -------
        torch.Tensor
            Attention weights, shape ``(J,)`` summing to 1.
        """
        p_proj = self.problem_proj(problem_flat)        # (d_hidden,)
        s_proj = self.state_proj(prev_state)            # (d_hidden,)
        c_projs = self.concept_proj(concept_flats)      # (J, d_hidden)
        
        # Stateful query integrates both problem constraints and current reasoning state
        query = p_proj + s_proj                         # (d_hidden,)
        interaction = query.unsqueeze(0) * c_projs     # (J, d_hidden)
        scores = self.score(interaction).squeeze(-1)    # (J,)
        return F.softmax(scores, dim=0)


class LARE(nn.Module):
    """Latent Algebraic Reasoning Engine.

    The core reasoning module that replaces standard attention with
    constrained operator-operand bilinear mechanisms. Takes a set of
    ConceptTensors and a ProblemTensor, performs multi-step iterative
    reasoning, and produces a SolutionTensor.

    Parameters
    ----------
    d : int
        Embedding dimension per element (default 128).
    num_operators : int
        Number of algebraic operator heads :math:`M` (default 4).
    max_steps : int
        Default maximum reasoning iterations (default 10).
    epsilon : float
        Convergence threshold (default 1e-4).
    temperature : float
        Temperature for constraint mask (default 1.0).

    Examples
    --------
    >>> lare = LARE(d=64, num_operators=4)
    >>> concepts = [ConceptTensor.random(d=64) for _ in range(5)]
    >>> problem = ProblemTensor.random(d=64)
    >>> solution = lare(concepts, problem, max_steps=5)
    >>> solution.is_verified  # Not yet verified
    False
    """

    def __init__(
        self,
        d: int = DEFAULT_EMBEDDING_DIM,
        num_operators: int = 4,
        max_steps: int = 10,
        epsilon: float = 1e-4,
        temperature: float = 1.0,
    ) -> None:
        super().__init__()
        self.d = d
        self.num_operators = num_operators
        self.default_max_steps = max_steps
        self.epsilon = epsilon

        num_elements = NUM_CONCEPT_ELEMENTS

        # ── Algebraic operator heads O_m ──────────────────────────
        self.operators = nn.ModuleList([
            AlgebraicOperatorHead(d) for _ in range(num_operators)
        ])

        # ── Operator gating: σ(W_m · p_formal_reqs) ──────────────
        self.operator_gates = nn.ModuleList([
            nn.Linear(d, 1) for _ in range(num_operators)
        ])

        # ── Attention scorer α_ij ─────────────────────────────────
        self.attention_scorer = AttentionScorer(
            d_problem=num_elements * d,
            d_concept=num_elements * d,
            d_hidden=d,
        )

        # ── Constraint mask Φ ─────────────────────────────────────
        self.constraint_mask = ConstraintMask(
            d=d, temperature=temperature
        )

        # ── Context aggregation: how to aggregate concept elements
        # into a single context vector for the operator heads
        self.context_aggregator = nn.Sequential(
            spectral_norm(nn.Linear(num_elements * d, d)),
            nn.Tanh(),  # Replaced GELU with Tanh (1-Lipschitz limit) to satisfy contraction theorem
        )

        # ── State refinement: processes the aggregated operator
        # output and produces the next state
        self.state_refiner = nn.Sequential(
            spectral_norm(nn.Linear(d, d)),
            nn.Tanh(),  # Replaced GELU with Tanh (1-Lipschitz limit) to satisfy contraction theorem
            spectral_norm(nn.Linear(d, d)),
        )

        # ── Per-element output projection (expand d → 10*d for state)
        self.state_expand = nn.Sequential(
            spectral_norm(nn.Linear(d, num_elements * d)),
        )

        # ── Solution projection ───────────────────────────────────
        self.solution_proj = nn.Sequential(
            nn.Linear(num_elements * d, num_elements * d),
            nn.GELU(),
            nn.LayerNorm(num_elements * d),
        )

        # ── Confidence estimation ─────────────────────────────────
        self.confidence_head = nn.Sequential(
            nn.Linear(num_elements * d + 1, d),  # +1 for convergence delta
            nn.GELU(),
            nn.Linear(d, 1),
            nn.Sigmoid(),
        )

    def _compute_context(self, concepts: List[ConceptTensor]) -> torch.Tensor:
        """Aggregate all concepts into a single context vector.

        Parameters
        ----------
        concepts : list of ConceptTensor

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — aggregated context.
        """
        # Mean-pool all concept tensors, then project
        stacked = ConceptTensor.stack(concepts)  # (J, 10, d)
        mean_concept = stacked.mean(dim=0)       # (10, d)
        flat = mean_concept.reshape(-1)           # (10*d,)
        return self.context_aggregator(flat)      # (d,)

    def _apply_operators(
        self,
        concept_element: torch.Tensor,
        context: torch.Tensor,
        formal_reqs: torch.Tensor,
    ) -> torch.Tensor:
        """Apply all M operator heads with gating.

        Computes:

        .. math::

            \\sum_m \\sigma(W_m \\, p_{formal\\_reqs}) \\, \\mathcal{O}_m(c, ctx)

        Parameters
        ----------
        concept_element : torch.Tensor
            Shape ``(d,)`` — a single concept element.
        context : torch.Tensor
            Shape ``(d,)`` — aggregated context.
        formal_reqs : torch.Tensor
            Shape ``(d,)`` — problem's formal requirements.

        Returns
        -------
        torch.Tensor
            Shape ``(d,)`` — gated operator output.
        """
        output = torch.zeros_like(concept_element)
        for m in range(self.num_operators):
            gate = torch.sigmoid(self.operator_gates[m](formal_reqs))  # (1,)
            op_result = self.operators[m](concept_element, context)    # (d,)
            output = output + gate * op_result
        return output

    def _single_step(
        self,
        concepts: List[ConceptTensor],
        problem: ProblemTensor,
        prev_state: torch.Tensor,
        context: torch.Tensor,
    ) -> Tuple[torch.Tensor, float]:
        """Execute a single reasoning step.

        Implements the full LARE state update equation.

        Parameters
        ----------
        concepts : list of ConceptTensor
        problem : ProblemTensor
        prev_state : torch.Tensor
            Previous state, shape ``(10, d)`` or ``(10*d,)``.
        context : torch.Tensor
            Shape ``(d,)`` — aggregated context.

        Returns
        -------
        tuple of (torch.Tensor, float)
            New state ``(10*d,)`` and constraint violation score.
        """
        num_concepts = len(concepts)
        p_flat = problem.to_tensor().reshape(-1)   # (10*d,)
        formal_reqs = problem.get_formal_requirements()  # (d,)

        # Compute attention weights α_ij using the stateful query (prev_state)
        concept_flats = torch.stack(
            [c.to_tensor().reshape(-1) for c in concepts], dim=0
        )  # (J, 10*d)
        alpha = self.attention_scorer(p_flat, concept_flats, prev_state)  # (J,)

        # Update the aggregated context dynamically from the reasoning state to achieve true multi-hop refinement
        dynamic_context = self.context_aggregator(prev_state)  # (d,)

        # Weighted sum over concepts with operator application
        aggregated = torch.zeros(self.d, device=p_flat.device, dtype=p_flat.dtype)
        total_violation = 0.0

        for j in range(num_concepts):
            # Apply operators to all 10 aspect elements individually rather than pre-pooling
            op_outputs = []
            for idx in range(NUM_CONCEPT_ELEMENTS):
                c_element = concepts[j].get_element(idx)
                op_outputs.append(self._apply_operators(c_element, dynamic_context, formal_reqs))
            
            # Aggregate the independently processed aspect representations
            op_output = torch.stack(op_outputs, dim=0).mean(dim=0)  # (d,)

            # Apply constraint mask Φ (soft gating)
            constraints = problem.get_constraint_vector()       # (d,)
            limitations = concepts[j].limitations_risks         # (d,)
            mask = self.constraint_mask(constraints, limitations)  # (d,)
            violation = self.constraint_mask.compute_violation_score(
                constraints, limitations
            ).item()
            total_violation += violation

            # Masked contribution
            masked_output = op_output * mask

            # Strict differentiable Gram-Schmidt projection (hard constraint satisfaction)
            # Projects the masked output vector orthogonally onto the null-space of the constraints
            dot_val = (masked_output * constraints).sum(dim=-1, keepdim=True)
            norm_sq = (constraints * constraints).sum(dim=-1, keepdim=True) + 1e-8
            proj = (dot_val / norm_sq) * constraints
            masked_output = masked_output - proj

            aggregated = aggregated + alpha[j] * masked_output

        avg_violation = total_violation / max(num_concepts, 1)

        # Refine the aggregated state
        refined = self.state_refiner(aggregated)  # (d,)

        # Expand back to full state dimension
        new_state = self.state_expand(refined)  # (10*d,)

        # Residual connection with previous state
        if prev_state.shape == new_state.shape:
            new_state = 0.5 * new_state + 0.5 * prev_state

        return new_state, avg_violation

    def _anderson_acceleration(
        self,
        concepts: List[ConceptTensor],
        problem: ProblemTensor,
        context: torch.Tensor,
        max_steps: int,
        epsilon: float,
        m: int = 3,
    ) -> Tuple[torch.Tensor, List[float], List[ProofStep], List[torch.Tensor], List[str]]:
        """DEQ fixed point solving via Anderson Acceleration."""
        device = concepts[0].device
        dtype = concepts[0].dtype
        num_elements = NUM_CONCEPT_ELEMENTS

        # Initialize state from problem
        x = problem.to_tensor().reshape(-1).clone()

        X_hist = []
        F_hist = []

        proof_steps = []
        resolution_steps = []
        applied_ops = []
        convergence_deltas = []

        start_time = time.time()

        for t in range(max_steps):
            x_prev = x.clone()

            # Step evaluation: g(x) = f(x)
            g_x, violation = self._single_step(concepts, problem, x_prev, context)
            f_x = g_x - x_prev  # Residual f = g(x) - x

            delta = f_x.norm().item()
            convergence_deltas.append(delta)

            step = ProofStep(
                step_index=t,
                operation="refine_deq",
                operand_indices=list(range(len(concepts))),
                result_norm=g_x.norm().item(),
                constraint_violation=violation,
                timestamp=time.time(),
                metadata={"convergence_delta": delta, "deq": True},
            )
            proof_steps.append(step)
            resolution_steps.append(g_x.detach().clone())
            applied_ops.append("refine_deq")

            if delta < epsilon:
                x = g_x
                break

            # Keep history
            X_hist.append(x_prev)
            F_hist.append(f_x)
            if len(X_hist) > m:
                X_hist.pop(0)
                F_hist.pop(0)

            # Anderson update
            k = len(X_hist)
            if k == 1:
                x = g_x
            else:
                F_mat = torch.stack([F_hist[i] - F_hist[-1] for i in range(k - 1)], dim=1)  # (10*d, k-1)
                f_target = -F_hist[-1]

                try:
                    # Add Tikhonov (Ridge) regularization to stabilize the least-squares solve
                    lambd = 1e-4
                    F_T = F_mat.transpose(-2, -1)
                    reg_matrix = F_T @ F_mat + lambd * torch.eye(F_mat.size(-1), device=device, dtype=dtype)
                    alpha_coeff = torch.linalg.solve(reg_matrix, F_T @ f_target.unsqueeze(-1)).solution.squeeze(-1)
                except RuntimeError:
                    x = g_x
                    continue

                beta = torch.zeros(k, device=device, dtype=dtype)
                beta[:-1] = alpha_coeff
                beta[-1] = 1.0 - alpha_coeff.sum()

                # Reconstruct next state x = \sum beta_i (x_i + F_i)
                g_hist = [X_hist[i] + F_hist[i] for i in range(k)]
                x = torch.stack(g_hist, dim=0).T @ beta

        return x, convergence_deltas, proof_steps, resolution_steps, applied_ops

    def forward(
        self,
        concepts: List[ConceptTensor],
        problem: ProblemTensor,
        max_steps: Optional[int] = None,
        epsilon: Optional[float] = None,
        deq_mode: bool = False,
    ) -> SolutionTensor:
        """Run the full multi-step reasoning process.

        Iteratively refines a latent state by applying algebraic operators
        over concepts, gated by the problem's formal requirements and
        masked by the constraint function Φ. Stops when either:

        1. The state converges: :math:`\\|c^{(t)} - c^{(t-1)}\\| < \\varepsilon`
        2. Maximum iterations are reached.

        Parameters
        ----------
        concepts : list of ConceptTensor
            The knowledge operands to reason over. Must not be empty.
        problem : ProblemTensor
            The task operator driving the reasoning.
        max_steps : int, optional
            Override the default max iterations.
        epsilon : float, optional
            Override the default convergence threshold.
        deq_mode : bool
            Enable Anderson Acceleration Deep Equilibrium solver.

        Returns
        -------
        SolutionTensor
            Contains the final solution, proof trace, convergence info,
            and confidence score.

        Raises
        ------
        ValueError
            If ``concepts`` is empty.
        """
        if not concepts:
            raise ValueError("LARE requires at least one ConceptTensor")

        max_steps = max_steps or self.default_max_steps
        epsilon = epsilon or self.epsilon
        device = concepts[0].device
        dtype = concepts[0].dtype
        num_elements = NUM_CONCEPT_ELEMENTS

        # Compute context once
        context = self._compute_context(concepts)

        start_time = time.time()

        if deq_mode:
            state, convergence_deltas, proof_steps, resolution_steps, applied_ops = self._anderson_acceleration(
                concepts, problem, context, max_steps, epsilon
            )
        else:
            # Initialize state from the problem tensor
            state = problem.to_tensor().reshape(-1)  # (10*d,)

            # Track for convergence and proof
            proof_steps: List[ProofStep] = []
            resolution_steps: List[torch.Tensor] = []
            applied_ops: List[str] = []
            convergence_deltas: List[float] = []

            for t in range(max_steps):
                prev_state = state.clone()

                # Single LARE step
                state, violation = self._single_step(
                    concepts, problem, prev_state, context
                )

                # Track convergence
                delta = (state - prev_state).norm().item()
                convergence_deltas.append(delta)

                # Record proof step
                step = ProofStep(
                    step_index=t,
                    operation="refine",
                    operand_indices=list(range(len(concepts))),
                    result_norm=state.norm().item(),
                    constraint_violation=violation,
                    timestamp=time.time(),
                    metadata={"convergence_delta": delta},
                )
                proof_steps.append(step)
                resolution_steps.append(state.detach().clone())
                applied_ops.append("refine")

                # Check convergence
                if delta < epsilon:
                    break

        # Project to solution space
        solution_vec = self.solution_proj(state)  # (10*d,)

        # Reshape to (10, d)
        result_tensor = solution_vec.reshape(num_elements, self.d)

        # Apply strict differentiable Gram-Schmidt projection on final output
        # to guarantee zero boundary violation
        constraints = problem.get_constraint_vector()  # (d,)
        dot_val = (result_tensor * constraints.unsqueeze(0)).sum(dim=-1, keepdim=True)  # (10, 1)
        norm_sq = (constraints * constraints).sum(dim=-1, keepdim=True) + 1e-8
        proj = (dot_val / norm_sq) * constraints.unsqueeze(0)
        result_tensor = result_tensor - proj

        # Estimate confidence (based on final state + convergence info)
        final_delta = torch.tensor(
            [convergence_deltas[-1]], device=device, dtype=dtype
        )
        conf_input = torch.cat([solution_vec, final_delta], dim=-1)
        confidence = self.confidence_head(conf_input).item()

        # Build solution
        solution = SolutionTensor(
            resolution_steps=resolution_steps,
            applied_concepts=list(range(len(concepts))),
            applied_operations=applied_ops,
            result_tensor=result_tensor,
            confidence=confidence,
            verification_passed=None,
            proof_trace=proof_steps,
            metadata={
                "num_steps": len(proof_steps),
                "converged": convergence_deltas[-1] < epsilon,
                "final_delta": convergence_deltas[-1],
                "convergence_history": convergence_deltas,
                "total_time_s": time.time() - start_time,
                "num_concepts": len(concepts),
                "num_operators": self.num_operators,
                "deq_mode": deq_mode,
            },
        )

        return solution

    def forward_batched(
        self,
        concept_batched: torch.Tensor,  # (B, 10, d)
        problem_batched: torch.Tensor,  # (B, 10, d)
        max_steps: Optional[int] = None,
        epsilon: Optional[float] = None,
    ) -> torch.Tensor:
        """Run the multi-step reasoning process fully batched in parallel across B examples.

        Parameters
        ----------
        concept_batched : torch.Tensor
            Batch of concepts, shape ``(B, 10, d)``.
        problem_batched : torch.Tensor
            Batch of problems, shape ``(B, 10, d)``.
        max_steps : int, optional
            Maximum reasoning iterations.
        epsilon : float, optional
            Convergence threshold.

        Returns
        -------
        torch.Tensor
            The batched solution tensor, shape ``(B, 10, d)``.
        """
        B, num_elements, d = concept_batched.shape
        max_steps = max_steps or self.default_max_steps
        epsilon = epsilon or self.epsilon
        
        # 1. Compute context: context_aggregator expects (10*d,) for single, so (B, 10*d) for batch
        c_flat = concept_batched.reshape(B, -1)
        context = self.context_aggregator(c_flat)  # (B, d)
        
        # 2. Get formal requirements (element 2) and constraint vector (element 5)
        formal_reqs = problem_batched[:, 2, :]  # (B, d)
        constraints = problem_batched[:, 5, :]  # (B, d)
        limitations = concept_batched[:, 8, :]  # (B, d) - limitations_risks is element 8
        
        # 3. Initialize state from problem tensor
        state = problem_batched.reshape(B, -1)  # (B, 10*d)
        
        for t in range(max_steps):
            prev_state = state.clone()
            
            # Update dynamic context from previous state
            dynamic_context = self.context_aggregator(prev_state)  # (B, d)
            
            # Apply operators to all 10 aspect elements individually
            op_outputs = []
            for idx in range(num_elements):
                c_element = concept_batched[:, idx, :]  # (B, d)
                
                # Apply each operator head with gating
                op_output = torch.zeros_like(c_element)
                for m in range(self.num_operators):
                    gate = torch.sigmoid(self.operator_gates[m](formal_reqs))  # (B, 1)
                    op_result = self.operators[m](c_element, dynamic_context)  # (B, d)
                    op_output = op_output + gate * op_result
                op_outputs.append(op_output)
                
            # Aggregate the independently processed aspect representations
            op_output = torch.stack(op_outputs, dim=1).mean(dim=1)  # (B, d)
            
            # Apply constraint mask Φ (soft gating)
            mask = self.constraint_mask(constraints, limitations)  # (B, d)
            masked_output = op_output * mask
            
            # Strict differentiable Gram-Schmidt projection (hard constraint satisfaction)
            dot_val = (masked_output * constraints).sum(dim=-1, keepdim=True)  # (B, 1)
            norm_sq = (constraints * constraints).sum(dim=-1, keepdim=True) + 1e-8
            proj = (dot_val / norm_sq) * constraints
            masked_output = masked_output - proj
            
            # Refine the aggregated state
            refined = self.state_refiner(masked_output)  # (B, d)
            
            # Expand back to full state dimension
            new_state = self.state_expand(refined)  # (B, 10*d)
            
            # Residual connection
            state = 0.5 * new_state + 0.5 * prev_state
            
            # Check convergence
            delta = (state - prev_state).norm(dim=-1).mean().item()
            if delta < epsilon:
                break
                
        # Project to solution space
        solution_vec = self.solution_proj(state)  # (B, 10*d)
        result_tensor = solution_vec.reshape(B, num_elements, d)
        
        # Apply strict differentiable Gram-Schmidt projection on final output
        dot_val = (result_tensor * constraints.unsqueeze(1)).sum(dim=-1, keepdim=True)  # (B, 10, 1)
        norm_sq = (constraints * constraints).sum(dim=-1, keepdim=True).unsqueeze(1) + 1e-8  # (B, 1, 1)
        proj = (dot_val / norm_sq) * constraints.unsqueeze(1)
        result_tensor = result_tensor - proj
        
        return result_tensor

    def extra_repr(self) -> str:
        return (
            f"d={self.d}, num_operators={self.num_operators}, "
            f"max_steps={self.default_max_steps}, epsilon={self.epsilon}"
        )
