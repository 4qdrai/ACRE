"""
Tests for ConceptEmbedding — the dual encoder + cross-encoder reranker.
"""

import pytest
import torch
from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.concept_embedding import ConceptEmbeddingModel, ConceptReranker

D_INPUT = 32
D_EMBED = 64

@pytest.fixture
def embedding():
    model = ConceptEmbeddingModel(d_input=D_INPUT, d_embed=D_EMBED)
    model.eval()
    return model

@pytest.fixture
def concept():
    return ConceptTensor.random(d=D_INPUT)

@pytest.fixture
def problem():
    return ProblemTensor.random(d=D_INPUT)

@pytest.fixture
def concept_library():
    """A small library of 10 random concepts."""
    return [ConceptTensor.random(d=D_INPUT) for _ in range(10)]


class TestEmbeddingShape:
    """Tests for embedding output shapes."""

    def test_embed_concept_shape(self, embedding, concept):
        """Concept embedding should have shape (d_embed,)."""
        emb = embedding.embed_concept(concept)
        assert emb.shape == (D_EMBED,)

    def test_embed_problem_shape(self, embedding, problem):
        """Problem embedding should have shape (d_embed,)."""
        emb = embedding.embed_problem(problem)
        assert emb.shape == (D_EMBED,)

    def test_embed_concept_normalized(self, embedding, concept):
        """Concept embedding should be L2-normalized."""
        emb = embedding.embed_concept(concept)
        norm = torch.norm(emb).item()
        assert abs(norm - 1.0) < 1e-5, f"Embedding norm = {norm}, expected 1.0"

    def test_embed_problem_normalized(self, embedding, problem):
        """Problem embedding should be L2-normalized."""
        emb = embedding.embed_problem(problem)
        norm = torch.norm(emb).item()
        assert abs(norm - 1.0) < 1e-5


class TestSimilarity:
    """Tests for similarity computation."""

    def test_similarity_range(self, embedding, concept, problem):
        """Cosine similarity should be in [-1, 1]."""
        sim = embedding.similarity(concept, problem)
        assert -1.0 <= sim <= 1.0 + 1e-6

    def test_self_similarity_maximum(self, embedding, concept):
        """A concept's similarity with itself should be ~1.0."""
        emb1 = embedding.embed_concept(concept)
        emb2 = embedding.embed_concept(concept)
        self_sim = torch.nn.functional.cosine_similarity(emb1.unsqueeze(0), emb2.unsqueeze(0)).item()
        assert abs(self_sim - 1.0) < 1e-4, f"Self-similarity = {self_sim}"


class TestBatchRetrieval:
    """Tests for batch retrieval."""

    def test_batch_retrieve_returns_top_k(self, embedding, problem, concept_library):
        """batch_retrieve should return at most top_k results."""
        top_k = 3
        results = embedding.batch_retrieve(problem, concept_library, top_k=top_k)
        assert len(results) == top_k

    def test_batch_retrieve_sorted_descending(self, embedding, problem, concept_library):
        """Results should be sorted by score in descending order."""
        results = embedding.batch_retrieve(problem, concept_library, top_k=5)
        scores = [score for _, score in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]

    def test_batch_retrieve_elements_valid(self, embedding, problem, concept_library):
        """All returned concepts should exist in the library."""
        results = embedding.batch_retrieve(problem, concept_library, top_k=5)
        for ret_concept, _ in results:
            assert any(ret_concept == c for c in concept_library)


class TestReranker:
    """Tests for cross-encoder reranker."""

    def test_reranker_ordering(self, problem, concept_library):
        """Reranker should produce a valid ordering (all elements present)."""
        reranker = ConceptReranker(d_input=D_INPUT, d_hidden=32)
        reranker.eval()
        results = reranker.rerank(problem, concept_library)
        assert len(results) == len(concept_library)
        
        returned_concepts = [c for c, _ in results]
        for c in concept_library:
            assert any(c == rc for rc in returned_concepts)

    def test_reranker_sorted(self, problem, concept_library):
        """Reranker results should be sorted by score descending."""
        reranker = ConceptReranker(d_input=D_INPUT, d_hidden=32)
        reranker.eval()
        results = reranker.rerank(problem, concept_library)
        scores = [score for _, score in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]
