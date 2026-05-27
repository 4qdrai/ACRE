"""
Unit Tests for Continuous-Time Flow Matching Decoder
===================================================

Tests the instantiation, loss computation, ODE integration (Euler sampling),
and batched processing of the FlowMatchingDecoder.
"""

from __future__ import annotations

import pytest
import torch

from acre.core.solution_tensor import SolutionTensor
from acre.core.flow_matching_decoder import FlowMatchingDecoder, SinusoidalTimeEmbedding


def test_sinusoidal_time_embedding() -> None:
    """Verify that SinusoidalTimeEmbedding maps time steps to vectors of the correct shape."""
    dim = 64
    embedder = SinusoidalTimeEmbedding(dim=dim)
    
    # Test batch representation
    t = torch.tensor([0.0, 0.5, 1.0])
    embeddings = embedder(t)
    assert embeddings.shape == (3, dim)
    assert not torch.isnan(embeddings).any()
    
    # Test odd dimension padding
    embedder_odd = SinusoidalTimeEmbedding(dim=dim + 1)
    embeddings_odd = embedder_odd(t)
    assert embeddings_odd.shape == (3, dim + 1)
    assert (embeddings_odd[:, -1] == 0).all()  # Padding should be zeros


def test_flow_matching_decoder_instantiation() -> None:
    """Verify FlowMatchingDecoder can be instantiated with custom hyperparameters."""
    decoder = FlowMatchingDecoder(
        d_solution=640,
        d_model=128,
        vocab_size=1000,
        num_layers=2,
        num_heads=2,
        dim_feedforward=256,
        max_output_len=100,
        parameterization="target",
    )
    assert decoder.d_solution == 640
    assert decoder.d_model == 128
    assert decoder.vocab_size == 1000
    assert decoder.parameterization == "target"

    # Test invalid parameterization
    with pytest.raises(ValueError):
        FlowMatchingDecoder(parameterization="invalid_param")


@pytest.mark.parametrize("parameterization", ["velocity", "target"])
def test_flow_matching_loss(parameterization: str) -> None:
    """Verify that flow matching loss computes a scalar and supports backpropagation."""
    d_model = 64
    d_sol = 10 * d_model  # 640
    vocab_size = 500
    
    decoder = FlowMatchingDecoder(
        d_solution=d_sol,
        d_model=d_model,
        vocab_size=vocab_size,
        num_layers=1,
        num_heads=2,
        dim_feedforward=128,
        max_output_len=50,
        parameterization=parameterization,
    )
    
    # Create single solution tensor and targets
    sol = SolutionTensor.empty(d=d_model)
    targets = torch.randint(0, vocab_size, (15,))  # sequence length 15
    
    loss = decoder.compute_loss(sol, targets)
    
    assert loss.dim() == 0  # scalar
    assert not torch.isnan(loss)
    assert loss.item() >= 0.0
    
    # Verify backward pass works
    loss.backward()
    
    # Verify gradient exists on parameters
    for name, param in decoder.named_parameters():
        if param.requires_grad:
            assert param.grad is not None, f"Parameter {name} missing gradient"


def test_flow_matching_decoding_unbatched() -> None:
    """Verify unbatched decoding outputs correct shapes and types."""
    d_model = 64
    d_sol = 10 * d_model
    vocab_size = 100
    
    decoder = FlowMatchingDecoder(
        d_solution=d_sol,
        d_model=d_model,
        vocab_size=vocab_size,
        num_layers=1,
        num_heads=2,
        dim_feedforward=128,
        max_output_len=50,
        parameterization="target",
    )
    
    sol = SolutionTensor.empty(d=d_model)
    
    # Decode logits
    logits = decoder.decode(sol, max_length=20, num_steps=5)
    assert logits.shape == (20, vocab_size)
    assert not torch.isnan(logits).any()
    
    # Decode to tokens (argmax)
    tokens_argmax = decoder.decode_to_tokens(sol, max_length=20, num_steps=5, temperature=0.0)
    assert tokens_argmax.shape == (20,)
    assert tokens_argmax.dtype == torch.long
    assert ((tokens_argmax >= 0) & (tokens_argmax < vocab_size)).all()
    
    # Decode to tokens (sampled)
    tokens_sampled = decoder.decode_to_tokens(sol, max_length=20, num_steps=5, temperature=1.0)
    assert tokens_sampled.shape == (20,)
    assert tokens_sampled.dtype == torch.long


def test_flow_matching_decoding_batched() -> None:
    """Verify batched decoding outputs correct shapes."""
    d_model = 64
    d_sol = 10 * d_model
    vocab_size = 100
    batch_size = 4
    
    decoder = FlowMatchingDecoder(
        d_solution=d_sol,
        d_model=d_model,
        vocab_size=vocab_size,
        num_layers=1,
        num_heads=2,
        dim_feedforward=128,
        max_output_len=50,
        parameterization="velocity",
    )
    
    # Create batched solution tensor
    result_tensor = torch.randn(batch_size, 10, d_model)
    sol = SolutionTensor(
        resolution_steps=[],
        applied_concepts=[],
        applied_operations=[],
        result_tensor=result_tensor,
    )
    
    # Loss computation with batched targets
    targets = torch.randint(0, vocab_size, (batch_size, 15))
    loss = decoder.compute_loss(sol, targets)
    assert loss.dim() == 0
    
    # Logits decoding
    logits = decoder.decode(sol, max_length=20, num_steps=5)
    assert logits.shape == (batch_size, 20, vocab_size)
    
    # Tokens decoding
    tokens = decoder.decode_to_tokens(sol, max_length=20, num_steps=5, temperature=0.0)
    assert tokens.shape == (batch_size, 20)
    assert tokens.dtype == torch.long
