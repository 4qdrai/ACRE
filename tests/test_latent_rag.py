"""
Tests for LatentRAG — concept knowledge storage and retrieval.
"""

import pytest
import torch
from acre.core.concept_tensor import ConceptTensor
from acre.core.problem_tensor import ProblemTensor
from acre.core.solution_tensor import SolutionTensor
from acre.core.latent_rag import LatentRAG

D_EMBED = 64
D = 32

@pytest.fixture
def rag():
    return LatentRAG(d_embed=D_EMBED, use_faiss=False)

@pytest.fixture
def concept():
    return ConceptTensor.random(d=D)

@pytest.fixture
def problem():
    return ProblemTensor.random(d=D)

@pytest.fixture
def solution():
    return SolutionTensor.empty(d=D)


class TestStoreAndRetrieve:
    """Tests for basic store/retrieve operations in LatentRAG."""

    def test_store_and_retrieve(self, rag, concept, problem, solution):
        """Stored triple should be findable via retrieval."""
        emb = torch.randn(D_EMBED)
        idx = rag.store(concept, problem, solution, embedding=emb)
        assert idx == 0
        assert len(rag) == 1

        # Retrieve
        results = rag.retrieve(emb, top_k=1)
        assert len(results) == 1
        ret_concept, ret_problem, ret_solution, sim = results[0]
        assert torch.allclose(ret_concept.to_tensor(), concept.to_tensor())
        assert torch.allclose(ret_problem.to_tensor(), problem.to_tensor())
        assert torch.allclose(ret_solution.result_tensor, solution.result_tensor)
        assert sim > 0.99  # Query is identical to key

    def test_store_without_embedding(self, rag, concept, problem, solution):
        """Storing without explicit embedding should generate one automatically."""
        idx = rag.store(concept, problem, solution)
        assert idx == 0
        assert len(rag) == 1
        
        # We can retrieve using a query constructed the same way
        c_flat = concept.to_tensor().reshape(-1)
        p_flat = problem.to_tensor().reshape(-1)
        combined = torch.cat([c_flat, p_flat])
        if combined.shape[0] >= D_EMBED:
            query = combined[:D_EMBED]
        else:
            query = torch.cat([combined, torch.zeros(D_EMBED - combined.shape[0])])
        
        results = rag.retrieve(query, top_k=1)
        assert len(results) == 1
        assert results[0][3] > 0.99

    def test_size_tracking(self, rag, concept, problem, solution):
        """Size should reflect the number of stored concepts."""
        assert len(rag) == 0
        rag.store(concept, problem, solution)
        assert len(rag) == 1
        rag.store(ConceptTensor.random(d=D), problem, solution)
        assert len(rag) == 2


class TestEmptyRetrieve:
    """Tests for retrieval from an empty store."""

    def test_empty_retrieve(self, rag):
        """Retrieving from an empty store should return an empty list."""
        query = torch.randn(D_EMBED)
        results = rag.retrieve(query, top_k=5)
        assert results == []


class TestTopK:
    """Tests for top_k result limiting."""

    def test_top_k_respected(self, rag, problem, solution):
        """Should return at most top_k results."""
        for i in range(20):
            rag.store(ConceptTensor.random(d=D), problem, solution, embedding=torch.randn(D_EMBED))

        query = torch.randn(D_EMBED)
        results = rag.retrieve(query, top_k=5)
        assert len(results) == 5

    def test_top_k_when_fewer_concepts(self, rag, concept, problem, solution):
        """If fewer concepts than top_k, return all available."""
        rag.store(concept, problem, solution, embedding=torch.randn(D_EMBED))
        query = torch.randn(D_EMBED)
        results = rag.retrieve(query, top_k=10)
        assert len(results) == 1

    def test_top_k_sorted_by_score(self, rag, problem, solution):
        """Results should be sorted by similarity score, highest first."""
        for i in range(10):
            rag.store(ConceptTensor.random(d=D), problem, solution, embedding=torch.randn(D_EMBED))

        query = torch.randn(D_EMBED)
        results = rag.retrieve(query, top_k=5)
        scores = [sim for _, _, _, sim in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1]


class TestForget:
    """Tests for forgetting (removing) concepts."""

    def test_forget_removes_concept(self, rag, concept, problem, solution):
        """After forget, the concept should not appear in retrieval."""
        emb = torch.randn(D_EMBED)
        rag.store(concept, problem, solution, embedding=emb)
        assert len(rag) == 1

        deleted = rag.forget(emb, threshold=0.95)
        assert deleted == 1
        assert len(rag) == 0

        results = rag.retrieve(emb, top_k=5)
        assert results == []


class TestConsolidate:
    """Tests for concept consolidation (identifying high-use entries)."""

    def test_consolidate_identifies_frequent_entries(self, rag, concept, problem, solution):
        """Entries retrieved above threshold should be candidates for consolidation."""
        emb = torch.randn(D_EMBED)
        rag.store(concept, problem, solution, embedding=emb)

        # Retrieve it 3 times
        for _ in range(3):
            rag.retrieve(emb, top_k=1)

        candidates = rag.consolidate(threshold=2)
        assert len(candidates) == 1
        assert candidates[0] == 0

        # Retrieve the consolidation data
        data = rag.get_consolidation_data(candidates)
        assert len(data) == 1
        c_data, p_data, s_data = data[0]
        assert torch.allclose(c_data.to_tensor(), concept.to_tensor())


class TestStatistics:
    """Tests for retrieval statistics tracking."""

    def test_statistics_tracked(self, rag, concept, problem, solution):
        """Retrieval stats should be updated after each retrieval."""
        emb = torch.randn(D_EMBED)
        rag.store(concept, problem, solution, embedding=emb)

        # Perform retrievals
        rag.retrieve(emb, top_k=1)
        rag.retrieve(torch.randn(D_EMBED), top_k=1)

        stats = rag.get_statistics()
        assert stats["total_stores"] == 1
        assert stats["total_retrievals"] == 2
        assert stats["total_hits"] == 2
        assert stats["num_entries"] == 1
