"""
Tests for ConceptAlgebra — the algebraic operations on concept tensors.
"""

import pytest
import torch
import torch.nn as nn
from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.algebra import ConceptAlgebra

D = 32  # Test dimension
B = 4   # Batch size for batched tests

@pytest.fixture
def algebra():
    return ConceptAlgebra(d=D)

@pytest.fixture
def concept_a():
    return ConceptTensor.random(d=D)

@pytest.fixture
def concept_b():
    return ConceptTensor.random(d=D)

@pytest.fixture
def concept_c():
    return ConceptTensor.random(d=D)

@pytest.fixture
def problem():
    return ProblemTensor.random(d=D)


class TestComposeOperation:
    """Tests for ⊕ compose."""

    def test_compose_output_type(self, algebra, concept_a, concept_b):
        """compose should return a ConceptTensor."""
        result = algebra.compose(concept_a, concept_b)
        assert isinstance(result, ConceptTensor)

    def test_compose_dimensions(self, algebra, concept_a, concept_b):
        """Output concept should have same shape as inputs."""
        result = algebra.compose(concept_a, concept_b)
        assert result.to_tensor().shape == (10, D)

    def test_compose_differentiable(self, algebra, concept_a, concept_b):
        """Gradients should flow through composition."""
        t1 = concept_a.to_tensor().detach().requires_grad_(True)
        c1 = ConceptTensor.from_tensor(t1)

        result = algebra.compose(c1, concept_b)
        loss = result.to_tensor().sum()
        loss.backward()

        assert t1.grad is not None
        assert not torch.all(t1.grad == 0)


class TestBindOperation:
    """Tests for ⊗ bind."""

    def test_bind_output_shape(self, algebra, concept_a, problem):
        """bind should return a tensor of shape (d,)."""
        result = algebra.bind(problem, concept_a)
        assert isinstance(result, torch.Tensor)
        assert result.shape == (D,)

    def test_bind_differentiable(self, algebra, concept_a, problem):
        """Gradients should flow through binding."""
        t = concept_a.to_tensor().detach().requires_grad_(True)
        c = ConceptTensor.from_tensor(t)
        result = algebra.bind(problem, c)
        loss = (result * torch.randn_like(result)).sum()
        loss.backward()
        assert t.grad is not None
        assert not torch.all(t.grad == 0)


class TestDifferenceOperation:
    """Tests for ⊖ difference."""

    def test_difference_output_type(self, algebra, concept_a, concept_b):
        """difference should return a ConceptTensor."""
        result = algebra.difference(concept_a, concept_b)
        assert isinstance(result, ConceptTensor)

    def test_difference_dimensions(self, algebra, concept_a, concept_b):
        """Output should preserve dimensions."""
        result = algebra.difference(concept_a, concept_b)
        assert result.to_tensor().shape == (10, D)


class TestProjectOperation:
    """Tests for Π project."""

    def test_project_to_solution(self, algebra, concept_a, problem):
        """project_to_solution should return a SolutionTensor."""
        binding = algebra.bind(problem, concept_a)
        result = algebra.project_to_solution(binding, problem)
        assert isinstance(result, SolutionTensor)
        assert result.result_tensor.shape == (10, D)
        assert result.confidence >= 0.0 and result.confidence <= 1.0


class TestAnalogy:
    """Tests for analogy."""

    def test_analogy_returns_concept(self, algebra, concept_a, concept_b, concept_c):
        """analogy(c1, c2, c3) should return a ConceptTensor."""
        result = algebra.analogy(concept_a, concept_b, concept_c)
        assert isinstance(result, ConceptTensor)

    def test_analogy_dimensions(self, algebra, concept_a, concept_b, concept_c):
        """Analogy output should preserve dimensions."""
        result = algebra.analogy(concept_a, concept_b, concept_c)
        assert result.to_tensor().shape == (10, D)


class TestAlgebraicProperties:
    """Tests for algebraic closure, consistency, and structural properties."""

    def test_algebraic_closure_compose(self, algebra, concept_a, concept_b):
        """Composing two valid concepts should always produce a valid concept."""
        result = algebra.compose(concept_a, concept_b)
        tensor = result.to_tensor()
        assert torch.isfinite(tensor).all(), "Compose produced non-finite values"
        assert tensor.shape == (10, D)

    def test_algebraic_closure_difference(self, algebra, concept_a, concept_b):
        """Differencing two valid concepts should always produce a valid concept."""
        result = algebra.difference(concept_a, concept_b)
        tensor = result.to_tensor()
        assert torch.isfinite(tensor).all(), "Difference produced non-finite values"

    def test_algebraic_closure_project(self, algebra, concept_a, problem):
        """Projecting valid concept through valid problem → valid solution."""
        binding = algebra.bind(problem, concept_a)
        result = algebra.project_to_solution(binding, problem)
        tensor = result.result_tensor
        assert torch.isfinite(tensor).all(), "Project produced non-finite values"

    def test_algebraic_consistency_loss(self, algebra, concept_a, concept_b, concept_c):
        """Consistency loss should return a valid differentiable scalar."""
        loss = algebra.algebraic_consistency_loss(concept_a, concept_b, concept_c)
        assert isinstance(loss, torch.Tensor)
        assert loss.ndim == 0
        assert loss.item() >= 0.0

    def test_all_parameters_have_gradients(self, algebra, concept_a, concept_b, problem):
        """All algebra parameters should receive gradients after running all algebraic operations."""
        # 1. Compose
        c_comp = algebra.compose(concept_a, concept_b)
        # 2. Difference
        c_diff = algebra.difference(concept_a, concept_b)
        # 3. Analogy
        c_analog = algebra.analogy(concept_a, concept_b, concept_a)
        # 4. Bind + Project
        binding = algebra.bind(problem, concept_a)
        # Make a batch to pass through MLP and confidence head
        solution_raw = algebra.project_mlp(torch.cat([binding.unsqueeze(0), problem.to_tensor().reshape(1, -1)], dim=-1))
        conf_raw = algebra.confidence_head(solution_raw)

        loss = (
            (c_comp.to_tensor() * torch.randn_like(c_comp.to_tensor())).sum()
            + (c_diff.to_tensor() * torch.randn_like(c_diff.to_tensor())).sum()
            + (c_analog.to_tensor() * torch.randn_like(c_analog.to_tensor())).sum()
            + (solution_raw * torch.randn_like(solution_raw)).sum()
            + conf_raw.sum()
        )
        loss.backward()

        for name, param in algebra.named_parameters():
            assert param.grad is not None, f"Parameter {name} did not receive gradients"


class TestBatchedAlgebra:
    """Tests for batch operations on raw tensors."""

    def test_batched_compose(self, algebra):
        a = torch.randn(B, 10, D)
        b = torch.randn(B, 10, D)
        out = algebra.compose(a, b)
        assert out.shape == (B, 10, D)

    def test_batched_bind(self, algebra):
        p = torch.randn(B, 10, D)
        c = torch.randn(B, 10, D)
        out = algebra.bind(p, c)
        assert out.shape == (B, D)

    def test_batched_difference(self, algebra):
        a = torch.randn(B, 10, D)
        b = torch.randn(B, 10, D)
        out = algebra.difference(a, b)
        assert out.shape == (B, 10, D)

    def test_batched_project_to_solution(self, algebra):
        binding = torch.randn(B, D)
        p = torch.randn(B, 10, D)
        out = algebra.project_to_solution(binding, p)
        assert out.shape == (B, 10, D)
