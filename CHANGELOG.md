# ACRE Changelog

All notable changes to the Algebraic Concept Reasoning Engine (ACRE) repository are documented here.

---

### v1.6.0 — Round 6 Review Remediation (2026-05-30)

**Training dynamics fixes:**
- **LR scheduler desync** — `OneCycleLR` and `CosineAnnealingLR` `steps_per_epoch`/`T_max` now computed from actual optimizer steps (`algebraic_pretraining.py`, `contrastive_pretraining.py`) instead of raw batch count.
- **Final batch flush** — Gradient accumulation now triggers on `is_last_step` in addition to `is_accum_step`, preventing loss of partial batches at epoch boundaries.

**Evaluation metric fix:**
- **C2E semantic subspace fallacy** — Removed arbitrary dimension slicing in `ElementScorer` (S1, S3, S4, S6). Dense embeddings from `ProjectionHead` are holistic — no disentangling loss forces specific dims to encode specific sub-concepts. All scorers now use full-vector cosine similarity and/or L2 distance.

**Contrastive learning fix:**
- **False negative collisions** — `InfoNCELoss` now accepts optional `labels` tensor to mask same-cluster off-diagonal logits (set to `-inf`). Prevents pushing semantically identical concepts apart when they appear in the same batch (Khosla et al., 2020).

---

### v1.5.0 — Round 5 Review Remediation (2026-05-30)

**Critical mathematical fixes:**
- **Softmax operator gating** — Replaced independent sigmoid gates (allowing aggregate Lipschitz constant up to M=4) with softmax (strict convex combination, Lipschitz ≤ 1), satisfying the Banach contraction theorem (`lare.py`).
- **Krasnoselskii-Mann averaged mapping** — Fixed the LARERefiner iteration from the expansive `x + κ·f(x)` to the proper contraction `(1-κ)·x + κ·f(x)` (`algebraic_pretraining.py`).
- **BF16 numerical stability** — Replaced all `+ 1e-8` epsilon values with `torch.clamp(min=1e-5)` across 10 sites in `lare.py`, `algebra.py` (BFloat16 rounds 1e-8 to 0.0).

**Pipeline fixes:**
- **Weight transfer** — Rewrote checkpoint loading to correctly map `refiner` → `state_refiner` and `phi_mask` → `constraint_mask` (was silently failing via `try/except` on non-existent `loop.solver.algebra`).
- **Pred index alignment** — Fixed second `pred[0]` → `pred[3]` in `consolidate()` for formal specification alignment.
- **Batched decoder** — Length prediction now per-batch-item (was only using `solution_flat[0]`).
- **Positional encoding** — Buffer sized to `max_seq_len + NUM_ELEMENTS` to prevent OOB crash when CLS tokens are prepended.
- **Convergence simulation** — Fixed averaged mapping to use `(1-κ)·c + κ·f(c)` instead of `(1-κ)·0 + κ·f(c)`.

**Data & documentation:**
- Schema alignment: `illustrative_corpus` → `illustrative_code` in `seed_concepts.json` to match `concept_tensor.py`.
- Added missing `\begin{abstract}` in `scientific_paper.tex`.
- Removed hardcoded Windows path from `validate_self_learning.py`.
- Updated `mathematical_foundations.md` — Definition 7 and Lemma 1 now describe softmax gating.

---

### v1.4.0 — Round 4 Review (2026-05-30)
- Fixed autoregressive SCAN evaluation (removed teacher forcing).
- Fixed K-Means ARI evaluation (removed ground-truth label leaking).
- Standardized data schema for self-learning pipeline.

---

### v1.3.0 — Round 3 Review (2026-05-29)
- Integrated Anderson Acceleration for DEQ reasoning.
- Added continuous-time Flow Matching (Euler ODE) decoder.
- Strict Gram-Schmidt orthogonalization in forward pass.

---

### v1.2.0 — Round 2 Review (2026-05-28)
- 1-Lipschitz constraints via Tanh + Spectral Normalization.
- Exact Gram-Schmidt projections for zero-hallucination.
- Deep Equilibrium (DEQ) solving.

---

### v1.1.0 — Round 1 Review (2026-05-27)
- Initial implementation of ACRE architecture.
- Concept Algebra with BilinearElementOp.
- LARE iterative reasoning engine.
- Self-learning loop with Latent RAG.
