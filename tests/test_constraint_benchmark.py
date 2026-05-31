"""
Unit tests for the Formal Constraint Satisfaction (FCS) benchmark.
"""

import pytest
import torch
from acre.evaluation.constraint_satisfaction_benchmark import (
    ConstraintEvaluator,
    ConstraintSatisfactionBenchmark,
)


def test_evaluator_bounds():
    """Verify that ConstraintEvaluator correctly checks bounds constraints."""
    x = torch.tensor([1.0, 2.0, 3.0])
    lower = torch.tensor([0.0, 1.5, 2.0])
    upper = torch.tensor([2.0, 2.5, 4.0])
    
    assert ConstraintEvaluator.verify_bounds(x, lower, upper) is True
    
    # Violation lower
    x_bad_lower = torch.tensor([1.0, 1.0, 3.0])
    assert ConstraintEvaluator.verify_bounds(x_bad_lower, lower, upper) is False

    # Violation upper
    x_bad_upper = torch.tensor([1.0, 2.0, 5.0])
    assert ConstraintEvaluator.verify_bounds(x_bad_upper, lower, upper) is False


def test_evaluator_orthogonality():
    """Verify that ConstraintEvaluator correctly checks orthogonality."""
    x = torch.tensor([1.0, 0.0, 0.0])
    y = torch.tensor([0.0, 1.0, 0.0])
    assert ConstraintEvaluator.verify_orthogonality(x, y) is True

    z = torch.tensor([1.0, 1.0, 0.0])
    assert ConstraintEvaluator.verify_orthogonality(x, z) is False


def test_benchmark_generation():
    """Verify procedural autonomous driving constraint generation."""
    bench = ConstraintSatisfactionBenchmark(d_dim=32)
    constraints = bench.generate_autonomous_driving_scenario(n_obstacles=2)
    
    assert "lower_bound" in constraints
    assert "upper_bound" in constraints
    assert "obstacle_bases" in constraints

    assert constraints["lower_bound"].shape == (32,)
    assert constraints["upper_bound"].shape == (32,)
    assert constraints["obstacle_bases"].shape == (2, 32)


def test_acre_constraint_projection():
    """Verify that ACRE's Gram-Schmidt projection satisfies all constraints."""
    d_dim = 16
    bench = ConstraintSatisfactionBenchmark(d_dim=d_dim)
    constraints = bench.generate_autonomous_driving_scenario(n_obstacles=2)

    # Initial random prediction violating boundaries and obstacles
    raw_pred = torch.randn(d_dim) * 10.0

    # Project onto manifold
    projected = bench.project_acre(raw_pred, constraints)

    # Verify projected satisfies bounds
    assert ConstraintEvaluator.verify_bounds(
        projected, constraints["lower_bound"], constraints["upper_bound"]
    ) is True

    # Verify projected is orthogonal to obstacles
    for base in constraints["obstacle_bases"]:
        assert ConstraintEvaluator.verify_orthogonality(projected, base) is True


def test_benchmark_run():
    """Verify FCS benchmark runs successfully and outputs valid metrics."""
    bench = ConstraintSatisfactionBenchmark(d_dim=16)
    results = bench.run_benchmark(n_runs=10)

    assert "autonomous_driving" in results
    res = results["autonomous_driving"]
    
    assert "acre" in res
    assert "baseline" in res
    
    assert 0.0 <= res["acre"]["satisfaction_rate"] <= 1.0
    assert 0.0 <= res["baseline"]["satisfaction_rate"] <= 1.0
    assert res["acre"]["satisfaction_rate"] == 1.0  # ACRE must be exactly 100%
