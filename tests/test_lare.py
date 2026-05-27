"""
Tests for LARE — the Latent Algebraic Reasoning Engine.
"""

import pytest
import torch
import torch.nn as nn
from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.lare import LARE

D = 32

@pytest.fixture
def lare():
    return LARE(d=D, num_operators=2, max_steps=10, epsilon=1e-4)

@pytest.fixture
def concepts():
    return [ConceptTensor.random(d=D) for _ in range(3)]

@pytest.fixture
def problem():
    return ProblemTensor.random(d=D)


class TestLAREOutput:
    """Tests for LARE forward pass output."""

    def test_forward_output_type(self, lare, concepts, problem):
        """forward() should return a SolutionTensor."""
        result = lare(concepts, problem)
        assert isinstance(result, SolutionTensor)

    def test_forward_output_shape(self, lare, concepts, problem):
        """Solution tensor should have shape (10, d)."""
        result = lare(concepts, problem)
        assert result.result_tensor.shape == (10, D)

    def test_forward_output_finite(self, lare, concepts, problem):
        """Output should contain only finite values."""
        result = lare(concepts, problem)
        assert torch.isfinite(result.result_tensor).all()

    def test_forward_reports_steps(self, lare, concepts, problem):
        """Solution should report how many refinement steps were taken."""
        result = lare(concepts, problem)
        assert "num_steps" in result.metadata
        assert result.metadata["num_steps"] >= 1


class TestLAREConvergence:
    """Tests for convergence behavior."""

    def test_convergence_state_difference_trend(self, concepts, problem):
        """State differences should generally contract (decrease) over iterations."""
        # Use more steps to see contraction clearly
        lare = LARE(d=D, num_operators=2, max_steps=20, epsilon=1e-20)
        result = lare(concepts, problem)
        history = result.metadata["convergence_history"]
        
        # In a contraction mapping, later deltas should be smaller than initial deltas
        assert len(history) >= 2
        assert history[-1] < history[0]

    def test_max_iterations_respected(self, concepts, problem):
        """LARE should never exceed max_steps, even if not converged."""
        K_max = 4
        lare = LARE(d=D, num_operators=2, max_steps=K_max, epsilon=1e-20)  # Tiny epsilon = never converge
        result = lare(concepts, problem)
        assert result.metadata["num_steps"] <= K_max

    def test_early_stopping(self, concepts, problem):
        """When diff < epsilon, LARE should stop early and report convergence."""
        # Use a very large epsilon so it converges immediately after first step
        lare = LARE(d=D, num_operators=2, max_steps=100, epsilon=1e10)
        result = lare(concepts, problem)
        assert result.metadata["converged"] is True
        assert result.metadata["num_steps"] == 1


class TestLAREConstraintMask:
    """Tests for the Φ constraint mask inside LARE."""

    def test_constraint_mask_applied(self, lare, concepts, problem):
        """Verify that the constraint mask computes valid gate statistics."""
        result = lare(concepts, problem)
        assert len(result.proof_trace) > 0
        for step in result.proof_trace:
            # Violation score must be bounded in [0, 1]
            assert 0.0 <= step.constraint_violation <= 1.0


class TestLAREGradients:
    """Tests for gradient flow through LARE."""

    def test_gradient_flow(self, lare, concepts, problem):
        """Gradients should propagate from the solution back to LARE parameters."""
        # Make concepts require gradients
        concepts_grad = []
        leaf_tensors = []
        for c in concepts:
            t = c.to_tensor().detach().requires_grad_(True)
            leaf_tensors.append(t)
            concepts_grad.append(ConceptTensor.from_tensor(t))

        result = lare(concepts_grad, problem)
        loss = (result.result_tensor * torch.randn_like(result.result_tensor)).sum()
        loss.backward()

        # Check gradients flow to inputs
        for t in leaf_tensors:
            assert t.grad is not None
            assert not torch.all(t.grad == 0)

        # Check gradients flow to LARE parameters
        has_grad = False
        for p in lare.parameters():
            if p.grad is not None and p.grad.abs().sum() > 0:
                has_grad = True
                break
        assert has_grad, "No gradients found in LARE parameters"

    def test_spectral_norm_bound(self, lare):
        """Refinement operator heads and refiner should respect spectral norm constraints (Lipschitz ≤ 1)."""
        # Check operator bilinear weights
        for op in lare.operators:
            # Check W_concept
            weight = op.W_concept.weight
            _, s, _ = torch.linalg.svd(weight)
            assert s[0].item() <= 1.0 + 0.3

            # Check W_context
            weight = op.W_context.weight
            _, s, _ = torch.linalg.svd(weight)
            assert s[0].item() <= 1.0 + 0.3

        # Check state refiner linear weights
        # state_refiner has layers: Linear, GELU, LayerNorm, Linear
        for layer in lare.state_refiner:
            if isinstance(layer, nn.Linear):
                weight = layer.weight
                _, s, _ = torch.linalg.svd(weight)
                assert s[0].item() <= 1.0 + 0.3
