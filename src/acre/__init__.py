"""
ACRE — Algebraic Concept Reasoning Engine
==========================================

A novel AI architecture that decouples language from logic using formalized
10-element structures for Concepts, Problems, and Solutions. Reasoning is
performed via differentiable algebra in a structured latent space, replacing
standard autoregressive next-token prediction with operator-operand bilinear
mechanisms bounded by formal constraint masks.

Architecture Overview
---------------------
The ACRE pipeline consists of:

1. **Translational Semantic Encoder** — compresses unstructured text into
   dense 10-element Concept Tensors and Problem (GPF) Tensors.
2. **Latent Algebraic Reasoning Engine (LARE)** — performs multi-step
   algebraic reasoning via constrained operator-operand bilinear operations.
3. **Constraint Mask Φ** — geometrically gates invalid axiomatic states by
   orthogonalizing GPF Constraints against Concept Limitations.
4. **Translational Decoder** — maps the algebraic solution back to
   human-readable output (text, code, formal specs).
5. **Latent RAG** — self-learning store of concept-problem-solution triples.

Mathematical Foundation
-----------------------
- Concept Tensor:  :math:`\\mathbf{c}_j \\in \\mathbb{R}^{10 \\times d}`
- Problem Tensor:  :math:`\\mathbf{p}_i \\in \\mathbb{R}^{10 \\times d}`
- Algebra:  :math:`\\oplus` (compose), :math:`\\otimes` (bind),
  :math:`\\ominus` (difference), :math:`\\Pi` (projection)
- State update:

  .. math::

      c_{out}^{(t)} = \\sum_i \\sum_j \\alpha_{ij}
      \\left( \\sum_m \\sigma(W_m p_{formal\\_reqs})
      \\mathcal{O}_m(c_j, c_{ctx}) \\right)
      \\cdot \\Phi(p_{constraints}, c_{limitations})

License
-------
Copyright (c) 2025 ACRE Contributors. All rights reserved.

References
----------
- ACRE: Formalized Latent Concept Architecture (internal)
- SPRIND Next Frontier AI Challenge
"""

__version__ = "0.1.0"
__author__ = "ACRE Contributors"
__license__ = "Apache-2.0"

# Lazy imports to avoid circular dependencies and keep startup fast
from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.algebra import ConceptAlgebra
from acre.core.constraint_mask import ConstraintMask
from acre.core.lare import LARE
from acre.core.concept_encoder import ConceptEncoder, ProblemEncoder
from acre.core.decoder import SolutionDecoder
from acre.core.flow_matching_decoder import FlowMatchingDecoder
from acre.core.concept_embedding import ConceptEmbeddingModel, ConceptReranker
from acre.core.latent_rag import LatentRAG

__all__ = [
    # Version
    "__version__",
    # Core tensor types
    "ConceptTensor",
    "ProblemTensor",
    "SolutionTensor",
    # Algebra
    "ConceptAlgebra",
    # Reasoning engine
    "LARE",
    "ConstraintMask",
    # Encoder / Decoder
    "ConceptEncoder",
    "ProblemEncoder",
    "SolutionDecoder",
    "FlowMatchingDecoder",
    # Embedding & retrieval
    "ConceptEmbeddingModel",
    "ConceptReranker",
    "LatentRAG",
]
