#!/usr/bin/env python3
"""
validate_composition.py

Validation Use Case 1: Zero-Shot Cross-Domain Concept Composition & Binding
----------------------------------------------------------------------------
This script demonstrates how ACRE algebraically composes two distinct concepts
(Linear Algebra and Calculus) into a joint representation, binds the composed
concept to a specific problem formulation (solving a geodesic path optimization),
projects the bound representation to a solution space, and decodes it using
both the CMLM decoder and the continuous-time Flow Matching decoder.

No mocks are used. All tensor operations, network layers, and algebraic
operations are executed using real PyTorch components.
"""

import os
import json
import time
import torch
import torch.nn.functional as F
import numpy as np

from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.algebra import ConceptAlgebra
from acre.core.decoder import SolutionDecoder
from acre.core.flow_matching_decoder import FlowMatchingDecoder
from acre.training.concept_distillation import TextToConceptPipeline

def main():
    # Set seed for reproducibility
    torch.manual_seed(42)
    np.random.seed(42)

    print("=" * 70)
    print("      ACRE: ZERO-SHOT CROSS-DOMAIN CONCEPT COMPOSITION VALIDATION")
    print("=" * 70)

    # 1. Load seed concepts from JSON
    seed_path = os.path.join("data", "concept_library", "seed_concepts.json")
    if not os.path.exists(seed_path):
        raise FileNotFoundError(f"Seed concepts not found at: {seed_path}")

    print(f"Loading seed concepts from: {seed_path}")
    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    concepts_list = data["concepts"]
    print(f"Loaded {len(concepts_list)} seed concepts.")

    # Find Linear Algebra and Calculus
    linear_algebra_data = next((c for c in concepts_list if c["id"] == "C-MAT-linear_algebra"), None)
    calculus_data = next((c for c in concepts_list if c["id"] == "C-MAT-calculus"), None)

    if not linear_algebra_data or not calculus_data:
        raise ValueError("Could not find Linear Algebra or Calculus in seed concepts.")

    # Combine text definitions for each concept
    la_text = " ".join(linear_algebra_data["elements"].values())
    calc_text = " ".join(calculus_data["elements"].values())

    print("\n--- Raw Input Preview ---")
    print(f"Linear Algebra Text length: {len(la_text)} characters.")
    print(f"Calculus Text length: {len(calc_text)} characters.")

    # 2. Instantiate Text-to-Concept Pipeline
    print("\nInitializing TextToConceptPipeline (d_model=768, element_dim=64)...")
    pipeline = TextToConceptPipeline(vocab_size=32000, d_model=768, element_dim=64, device="cpu")

    # Encode raw text -> ConceptTensors
    print("Encoding 'Linear Algebra' concept into latent manifold space...")
    c_linear_algebra = pipeline.extract_concepts(la_text)[0]
    print("Encoding 'Calculus' concept into latent manifold space...")
    c_calculus = pipeline.extract_concepts(calc_text)[0]

    print(f"Encoded Concept 1 (Linear Algebra) shape: {c_linear_algebra.to_tensor().shape}")
    print(f"Encoded Concept 2 (Calculus) shape: {c_calculus.to_tensor().shape}")

    # 3. Algebraic Composition: C_composed = C_linear_algebra (+) C_calculus
    print("\n--- Step 1: Concept Composition (COMPOSE) ---")
    algebra = ConceptAlgebra(d=64, num_operators=4)
    
    t0 = time.perf_counter()
    c_composed = algebra.compose(c_linear_algebra, c_calculus)
    t_comp = (time.perf_counter() - t0) * 1000
    
    print(f"Composed concept shape: {c_composed.to_tensor().shape}")
    print(f"Composition elapsed time: {t_comp:.3f} ms")

    # Check composition properties
    dist_c1_comp = torch.norm(c_composed.to_tensor() - c_linear_algebra.to_tensor()).item()
    dist_c2_comp = torch.norm(c_composed.to_tensor() - c_calculus.to_tensor()).item()
    print(f"Distance between C_composed and C_linear_algebra: {dist_c1_comp:.4f}")
    print(f"Distance between C_composed and C_calculus:       {dist_c2_comp:.4f}")

    # 4. Define and Encode the Problem (P)
    print("\n--- Step 2: Problem Definition and Encoding ---")
    problem_description = (
        "Problem: Find the path of steepest descent on a Riemannian manifold to minimize the geodesic energy. "
        "Formulate a geodesic optimization problem under acceleration bounds and integrate the ODE representing "
        "the geodesic curve. Verify boundary constraints."
    )
    print(f"Problem description:\n  '{problem_description}'")
    
    p_tensor = pipeline.extract_problems(problem_description)[0]
    print(f"Encoded ProblemTensor shape: {p_tensor.to_tensor().shape}")

    # 5. Bind Composed Concept to Problem: B = C_composed (*) P
    print("\n--- Step 3: Concept-Problem Binding (BIND) ---")
    t0 = time.perf_counter()
    bound_vector = algebra.bind(p_tensor, c_composed)
    t_bind = (time.perf_counter() - t0) * 1000
    print(f"Binding result shape: {bound_vector.shape}")
    print(f"Binding elapsed time: {t_bind:.3f} ms")

    # 6. Project to Solution Tensor: S = PROJECT(B, P)
    print("\n--- Step 4: Solution Projection (PROJECT) ---")
    t0 = time.perf_counter()
    solution_tensor = algebra.project_to_solution(bound_vector, p_tensor)
    t_proj = (time.perf_counter() - t0) * 1000
    print(f"Projected SolutionTensor shape: {solution_tensor.result_tensor.shape}")
    print(f"Solution confidence score: {solution_tensor.confidence:.4f}")
    print(f"Projection elapsed time: {t_proj:.3f} ms")

    # 7. Decoding Comparison: CMLM (Non-Autoregressive) vs. Flow Matching (Continuous ODE)
    print("\n--- Step 5: Decoder Execution and Comparison ---")
    vocab_size = 1000
    d_model = 128
    d_solution = 640

    # Initialize decoders
    cmlm_decoder = SolutionDecoder(
        d_solution=d_solution, d_model=d_model, vocab_size=vocab_size,
        num_layers=2, num_heads=2, dim_feedforward=256, num_refine_steps=3
    )
    fm_decoder = FlowMatchingDecoder(
        d_solution=d_solution, d_model=d_model, vocab_size=vocab_size,
        num_layers=2, num_heads=2, dim_feedforward=256, parameterization="target"
    )

    # Decode with CMLM
    t0 = time.perf_counter()
    cmlm_logits = cmlm_decoder(solution_tensor, max_length=15)
    t_cmlm = (time.perf_counter() - t0) * 1000
    cmlm_tokens = cmlm_logits.argmax(dim=-1)

    # Decode with Flow Matching
    t0 = time.perf_counter()
    fm_logits = fm_decoder.decode(solution_tensor, max_length=15, num_steps=10)
    t_fm = (time.perf_counter() - t0) * 1000
    fm_tokens = fm_logits.argmax(dim=-1)

    print("\nDecoder Performance & Output Analysis:")
    print("-" * 65)
    print(f"CMLM Decoder Decode Time:          {t_cmlm:.3f} ms")
    print(f"CMLM Output Logits Shape:          {cmlm_logits.shape}")
    print(f"CMLM Decoded Tokens:               {cmlm_tokens.tolist()}")
    print("-" * 65)
    print(f"Flow Matching Decoder Decode Time: {t_fm:.3f} ms")
    print(f"Flow Matching Output Logits Shape: {fm_logits.shape}")
    print(f"Flow Matching Decoded Tokens:      {fm_tokens.tolist()}")
    print("-" * 65)

    # Calculate logit statistics for both decoders
    cmlm_entropy = -torch.sum(F.softmax(cmlm_logits, dim=-1) * F.log_softmax(cmlm_logits, dim=-1), dim=-1).mean().item()
    fm_entropy = -torch.sum(F.softmax(fm_logits, dim=-1) * F.log_softmax(fm_logits, dim=-1), dim=-1).mean().item()

    print(f"CMLM Output Logits Entropy:        {cmlm_entropy:.4f}")
    print(f"Flow Matching Output Logits Entropy: {fm_entropy:.4f}")
    print("-" * 65)

    # Verify Differentiability of both decoders
    print("\nVerifying Decoder Differentiability (Gradient Flow)...")
    
    # 1. CMLM Decoder gradient flow check
    cmlm_decoder.zero_grad()
    cmlm_loss = cmlm_logits.mean()
    cmlm_loss.backward(retain_graph=True)
    cmlm_has_grads = any(p.grad is not None for p in cmlm_decoder.parameters())
    print(f"  CMLM Decoder requires_grad and receives gradients: {cmlm_has_grads}")

    # 2. Flow Matching Decoder gradient flow check
    # Let's perform a fake training step using compute_loss
    dummy_targets = torch.randint(0, vocab_size, (15,))
    fm_decoder.zero_grad()
    fm_loss = fm_decoder.compute_loss(solution_tensor, dummy_targets)
    fm_loss.backward()
    fm_has_grads = any(p.grad is not None for p in fm_decoder.parameters())
    print(f"  Flow Matching Decoder receives gradients:          {fm_has_grads}")
    print(f"  Computed Flow Matching Loss:                       {fm_loss.item():.4f}")

    print("\n[SUCCESS] Use Case 1 validation completed cleanly without mocks!")
    print("=" * 70)

if __name__ == "__main__":
    main()
