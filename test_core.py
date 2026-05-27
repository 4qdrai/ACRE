"""Quick smoke test for all ACRE core modules."""
import sys
sys.path.insert(0, "src")

import torch
print("=" * 60)
print("  ACRE Core Architecture — Smoke Test")
print("=" * 60)

# 1. ConceptTensor
from acre.core.concept_tensor import ConceptTensor
c1 = ConceptTensor.random(d=64)
c2 = ConceptTensor.random(d=64)
assert c1.to_tensor().shape == (10, 64), "ConceptTensor shape mismatch"
c_rt = ConceptTensor.from_tensor(c1.to_tensor())
assert c1 == c_rt, "ConceptTensor roundtrip failed"
batch = ConceptTensor.stack([c1, c2])
assert batch.shape == (2, 10, 64), "ConceptTensor batch shape mismatch"
print("[OK] ConceptTensor")

# 2. ProblemTensor
from acre.core.problem_tensor import ProblemTensor
p = ProblemTensor.random(d=64)
assert p.to_tensor().shape == (10, 64)
assert p.get_constraint_vector().shape == (64,)
assert p.get_formal_requirements().shape == (64,)
print("[OK] ProblemTensor")

# 3. SolutionTensor
from acre.core.solution_tensor import SolutionTensor
s = SolutionTensor.empty(d=64)
assert s.num_steps == 0
assert s.confidence == 0.0
s2 = SolutionTensor.from_result(torch.randn(10, 64), confidence=0.8)
assert s2.confidence == 0.8
print("[OK] SolutionTensor")

# 4. ConceptAlgebra
from acre.core.algebra import ConceptAlgebra
alg = ConceptAlgebra(d=64)
c3 = alg.compose(c1, c2)
assert c3.dim == 64, "Compose output dim mismatch"
bound = alg.bind(p, c1)
assert bound.shape == (64,), f"Bind shape: {bound.shape}"
diff = alg.difference(c1, c2)
assert diff.dim == 64
sol = alg.project_to_solution(bound, p)
assert sol.confidence > 0
analogy = alg.analogy(c1, c2, ConceptTensor.random(d=64))
assert analogy.dim == 64
loss = alg.algebraic_consistency_loss(c1, c2, ConceptTensor.random(d=64))
assert loss.item() >= 0
print("[OK] ConceptAlgebra")

# 5. ConstraintMask
from acre.core.constraint_mask import ConstraintMask
mask = ConstraintMask(d=64)
gate = mask(p.get_constraint_vector(), c1.limitations_risks)
assert gate.shape == (64,), f"Mask shape: {gate.shape}"
assert (gate >= 0).all() and (gate <= 1).all(), "Mask out of [0,1]"
v = mask.compute_violation_score(p.get_constraint_vector(), c1.limitations_risks)
assert 0 <= v.item() <= 1
stats = mask.get_gate_statistics(p.get_constraint_vector(), c1.limitations_risks)
assert "mean_gate" in stats
print("[OK] ConstraintMask")

# 6. LARE
from acre.core.lare import LARE
lare = LARE(d=64, num_operators=2, max_steps=3)
concepts = [ConceptTensor.random(d=64) for _ in range(3)]
solution = lare(concepts, p, max_steps=3)
assert solution.result_tensor.shape == (10, 64)
assert solution.num_steps > 0
assert 0 <= solution.confidence <= 1
print(f"[OK] LARE — {solution.num_steps} steps, confidence={solution.confidence:.3f}")

# 7. ConceptEncoder
from acre.core.concept_encoder import ConceptEncoder, ProblemEncoder
enc = ConceptEncoder(d_model=64, d_concept=32, num_layers=1, num_heads=2, dim_feedforward=128)
tokens = torch.randn(1, 20, 64)
c_enc = enc.encode_concept(tokens)
assert c_enc.dim == 32
print("[OK] ConceptEncoder")

penc = ProblemEncoder(d_model=64, d_problem=32, num_layers=1, num_heads=2, dim_feedforward=128)
p_enc = penc.encode_problem(tokens)
assert p_enc.dim == 32
print("[OK] ProblemEncoder")

# 8. SolutionDecoder
from acre.core.decoder import SolutionDecoder
dec = SolutionDecoder(d_solution=640, d_model=64, vocab_size=100, num_layers=1, num_heads=2, dim_feedforward=128, num_refine_steps=1)
sol_test = SolutionTensor.from_result(torch.randn(10, 64), confidence=0.5)
logits = dec(sol_test, max_length=10)
assert logits.shape == (10, 100), f"Decoder output: {logits.shape}"
print("[OK] SolutionDecoder")

# 9. ConceptEmbeddingModel
from acre.core.concept_embedding import ConceptEmbeddingModel, ConceptReranker
emb_model = ConceptEmbeddingModel(d_input=64, d_embed=128)
c_emb = emb_model.embed_concept(ConceptTensor.random(d=64))
assert c_emb.shape == (128,)
p_emb = emb_model.embed_problem(ProblemTensor.random(d=64))
assert p_emb.shape == (128,)
sim = emb_model.similarity(ConceptTensor.random(d=64), ProblemTensor.random(d=64))
assert -1 <= sim <= 1
results = emb_model.batch_retrieve(ProblemTensor.random(d=64), [ConceptTensor.random(d=64) for _ in range(5)], top_k=3)
assert len(results) == 3
print("[OK] ConceptEmbeddingModel")

# 10. ConceptReranker
reranker = ConceptReranker(d_input=64, d_hidden=128, num_layers=1, num_heads=2)
ranked = reranker.rerank(ProblemTensor.random(d=64), [ConceptTensor.random(d=64) for _ in range(4)])
assert len(ranked) == 4
assert ranked[0][1] >= ranked[-1][1]  # sorted descending
print("[OK] ConceptReranker")

# 11. LatentRAG
from acre.core.latent_rag import LatentRAG
rag = LatentRAG(d_embed=128)
c_store = ConceptTensor.random(d=64)
p_store = ProblemTensor.random(d=64)
s_store = SolutionTensor.from_result(torch.randn(10, 64), confidence=0.9)
idx = rag.store(c_store, p_store, s_store)
assert len(rag) == 1
query = torch.randn(128)
retrieved = rag.retrieve(query, top_k=1)
assert len(retrieved) == 1
stats = rag.get_statistics()
assert stats["total_stores"] == 1
print("[OK] LatentRAG")

print("=" * 60)
print("  ALL TESTS PASSED [OK]")
print("=" * 60)
