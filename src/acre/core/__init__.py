"""
ACRE Core — The algebraic reasoning core of the ACRE architecture.
==================================================================

This package contains the foundational data structures, algebraic operations,
and neural network modules that implement the Formalized Latent Concept
Architecture (F-LACA).

Module Overview
---------------
- **concept_tensor** — 10-element Concept Tensor data structure
- **problem_tensor** — 10-element GPF (Generalized Problem Formulation) Tensor
- **solution_tensor** — Formalized solution space with proof traces
- **algebra** — Differentiable concept/problem/solution algebra (⊕, ⊗, ⊖, Π)
- **lare** — Latent Algebraic Reasoning Engine (replaces standard attention)
- **constraint_mask** — Φ orthogonality mask for constraint enforcement
- **concept_encoder** — Translational Semantic Encoder (text → tensors)
- **decoder** — Translational Decoder (solution tensor → output)
- **concept_embedding** — Dual encoder + cross-encoder reranker
- **latent_rag** — Latent RAG for self-learning concept-problem-solution triples
"""

from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.algebra import ConceptAlgebra
from acre.core.constraint_mask import ConstraintMask
from acre.core.lare import LARE
from acre.core.concept_encoder import ConceptEncoder, ProblemEncoder
from acre.core.decoder import SolutionDecoder
from acre.core.concept_embedding import ConceptEmbeddingModel, ConceptReranker
from acre.core.latent_rag import LatentRAG

__all__ = [
    "ConceptTensor",
    "ProblemTensor",
    "SolutionTensor",
    "ConceptAlgebra",
    "ConstraintMask",
    "LARE",
    "ConceptEncoder",
    "ProblemEncoder",
    "SolutionDecoder",
    "ConceptEmbeddingModel",
    "ConceptReranker",
    "LatentRAG",
]
