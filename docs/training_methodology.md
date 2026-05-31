# Training Methodology

## ACRE: Self-Supervised Concept Distillation & Algebraic Pre-Training

> **4QDR.AI Labs** · June 2026
> *A deep dive into how ACRE trains without internet-scale pretraining using structured concept distillation*

---

## Table of Contents

1. [The Pretraining Paradox](#1-the-pretraining-paradox)
2. [Concept Distillation as Compression](#2-concept-distillation-as-compression)
3. [Self-Supervision Signal](#3-self-supervision-signal)
4. [Training Pipeline Overview](#4-training-pipeline-overview)
5. [Phase 1: Concept Distillation](#5-phase-1-concept-distillation)
6. [Phase 2: Contrastive Concept Embedding](#6-phase-2-contrastive-concept-embedding)
7. [Phase 3: Algebraic Pre-Training](#7-phase-3-algebraic-pre-training)
8. [Phase 4: End-to-End Fine-Tuning](#8-phase-4-end-to-end-fine-tuning)
9. [Self-Learning Loop (Latent RAG)](#9-self-learning-loop-latent-rag)
10. [Curriculum Design](#10-curriculum-design)
11. [Compute Plan](#11-compute-plan)
12. [Data Sources](#12-data-sources)
13. [Evaluation & Validation](#13-evaluation--validation)

---

## 1. The Pretraining Paradox

### Why Internet-Scale Pretraining Is Wasteful

Current foundation models train on 1–15 trillion tokens scraped from the internet. This approach has a fundamental problem: **the vast majority of that data is redundant, noisy, or irrelevant**.

Consider the concept "Reinforcement Learning":
- A standard LLM ingests ~50,000+ pages mentioning RL across Wikipedia, textbooks, tutorials, blog posts, Reddit threads, and academic papers
- 99% of this text is paraphrasing, repetition, incomplete explanations, or outright incorrect information
- The actual knowledge content — the formal definition, key axioms (Bellman equation, Markov property), core algorithms, known limitations — can be captured in a single structured concept tensor

**The math is stark:**

```
Standard LLM training for "Reinforcement Learning":
  ~50,000 pages × ~500 tokens/page = 25,000,000 tokens
  FLOPs per concept: 25M × d × layers ≈ 10^12

ACRE concept distillation for "Reinforcement Learning":
  1 concept tensor × 10 elements × d dimensions = 10d parameters
  FLOPs per concept: 10d × d ≈ 10^7

Compression ratio: ~100,000×
```

This isn't cherry-picked — it's representative. Most human knowledge is expressed redundantly across the internet. ACRE exploits this by compressing to the structural essence.

### The Key Insight: Structure IS the Signal

Standard self-supervised learning uses the prediction of masked or next tokens as the training signal. ACRE takes a radically different approach:

> **The 10-element concept structure itself is the training signal.**

When you encode a concept as a structured tensor with ontology, axioms, relations, code, and limitations, the internal consistency of these elements provides a rich self-supervision signal:
- Axioms must be consistent with ontological definitions
- Code must implement the methods described in the methodological apparatus
- Limitations must be compatible with the axiomatic base
- Inter-concept relationships must be transitively consistent

This internal structure provides stronger training signal than next-token prediction, with orders of magnitude less data.

---

## 2. Concept Distillation as Compression

### From Raw Text to Formalized Concepts

Concept distillation is the process of converting unstructured knowledge (text, code, specifications) into structured 10-element concept tensors. This is fundamentally a **compression** operation.

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONCEPT DISTILLATION PIPELINE                │
│                                                                 │
│  ┌──────────────────┐                                          │
│  │ Raw Source        │                                          │
│  │ (Wikipedia,       │                                          │
│  │  Textbooks,       │     ┌──────────────────────┐            │
│  │  Papers,          │────►│  Frontier API Swarm  │            │
│  │  Code Repos,      │     │  (GPT-4, Claude,     │            │
│  │  Specifications)  │     │   Gemini — used as   │            │
│  └──────────────────┘     │   concept extractors) │            │
│                           └──────────┬───────────┘            │
│                                      │                         │
│                                      ▼                         │
│                    ┌─────────────────────────────────┐         │
│                    │  STRUCTURED CONCEPT TEMPLATE     │         │
│                    │                                  │         │
│                    │  1. Ontological Scaffolding      │         │
│                    │  2. Abstraction Level (1-4)      │         │
│                    │  3. Axiomatic Base (formal)      │         │
│                    │  4. Relational Network (SysML)   │         │
│                    │  5. Inferential Framework        │         │
│                    │  6. Methodological Apparatus     │         │
│                    │  7. Illustrative Code (Python)   │         │
│                    │  8. Goal Orientation & Scope     │         │
│                    │  9. Limitations & Risks          │         │
│                    │ 10. Inter-Concept Relations      │         │
│                    └─────────────────────────────────┘         │
│                                      │                         │
│                                      ▼                         │
│                    ┌─────────────────────────────────┐         │
│                    │  QUALITY ASSURANCE               │         │
│                    │  • C2E Metric Validation         │         │
│                    │  • Code Executability Check       │         │
│                    │  • Axiom Consistency Check        │         │
│                    │  • Cross-Reference Validation     │         │
│                    └─────────────────────────────────┘         │
│                                      │                         │
│                                      ▼                         │
│                    ┌─────────────────────────────────┐         │
│                    │  CONCEPT TENSOR  c ∈ ℝ^{10×d}   │         │
│                    │  Ready for training              │         │
│                    └─────────────────────────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

### Quality Control: The C2E Metric

Every distilled concept is evaluated using the **Concept-to-Expert (C2E) Metric** — a weighted, multi-faceted scoring system:

| Tier | Elements | Weight | Validation Method |
|------|----------|--------|-------------------|
| **Core Identity** | Ontology (1), Axioms (3), Code (7) | 56% | Semantic similarity + code execution + AST comparison |
| **Structure** | Abstraction (2), Relations (4), Methods (6), Goals (8) | 32% | Semantic similarity + SysML XML validation |
| **Context** | Inference (5), Limitations (9), Inter-Concept (10) | 12% | Semantic similarity |

A concept must achieve C2E ≥ 80/100 to enter the training set. This ensures that only high-quality, structurally complete concepts are used for training.

---

## 3. Self-Supervision Signal

### The Five Self-Supervision Objectives

ACRE uses five complementary self-supervision signals, none of which require labeled data:

#### Objective 1: Intra-Concept Consistency (ICC)

Each concept tensor must be internally consistent. The loss measures whether the 10 elements agree with each other:

```
L_ICC = Σ_{k≠l} w_{kl} · max(0, -cos(c^(k), f_{kl}(c^(l))))
```

where `f_{kl}` is a learned projection from element `l` to the space of element `k`, and `w_{kl}` encodes the expected correlation between elements. For example:
- Axioms (3) should be consistent with Ontology (1): `w_{13}` is high
- Code (7) should implement Methods (6): `w_{67}` is high
- Limitations (9) should constrain Code (7): `w_{79}` is high

#### Objective 2: Element Reconstruction (ER)

Mask one element of the concept tensor and predict it from the other nine:

```
L_ER = E_{k ~ Uniform(1,10)} [ ||c^(k) - g_k(c^(-k))||² ]
```

where `c^(-k)` denotes the concept tensor with element `k` masked, and `g_k` is a reconstruction head. This forces each element to be predictable from the others, encouraging redundant encoding of the concept's essence across all elements.

#### Objective 3: Algebraic Consistency (AC)

The concept algebra operations should preserve semantic relationships:

```
L_AC = ||c₁ ⊕ c₂ - f_⊕(c₁, c₂)||² 
     + ||c ⊗ p - f_⊗(c, p)||²
     + ||(c₁ ⊕ c₂) ⊖ c₂ - c₁||²    # composition-difference inverse
     + ||Π(Π(c)) - Π(c)||²            # projection idempotency
```

This loss ensures the algebraic operations have the expected mathematical properties (closure, approximate commutativity, idempotency of projection).

#### Objective 4: Cross-Concept Contrastive (CCC)

Related concepts should be close, unrelated concepts should be far:

```
L_CCC = -log(exp(sim(c_i, c_j)/τ) / Σ_k exp(sim(c_i, c_k)/τ))
```

where `(c_i, c_j)` are concept pairs linked by Element 10 (Inter-Concept Relationships), and negative pairs are randomly sampled. Temperature `τ = 0.07`. **Supervised false-negative masking** is applied: when concepts share the same cluster label, their off-diagonal logits are set to `-∞` to prevent pushing semantically identical concepts apart (Khosla et al., 2020).

#### Objective 5: Code Execution Verification (CEV)

Element 7 (Illustrative Code) must be executable and produce correct outputs:

```
L_CEV = λ · (1 - C_exec) + μ · (1 - C_correct)
```

where `C_exec` is a binary indicator for code executability and `C_correct` indicates whether the code produces expected outputs. This is a hard constraint enforced during concept distillation.

### Total Training Loss

```
L_total = α₁ · L_ICC + α₂ · L_ER + α₃ · L_AC + α₄ · L_CCC + α₅ · L_CEV
```

Default weights: `α₁ = 0.25, α₂ = 0.20, α₃ = 0.25, α₄ = 0.20, α₅ = 0.10`

---

## 4. Training Pipeline Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ACRE TRAINING PIPELINE                         │
│                                                                         │
│  Phase 1          Phase 2          Phase 3          Phase 4             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ Concept  │    │Contrastive│   │ Algebraic │    │ End-to-  │          │
│  │Distillat.│───►│ Concept  │───►│   Pre-    │───►│   End    │          │
│  │          │    │Embedding │    │ Training  │    │Fine-Tune │          │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│  ~2 weeks        ~4 H100-hrs    ~30 H100-hrs    ~16 H100-hrs          │
│  (API + human)   (GPU)          (GPU)           (GPU)                  │
│                                                                         │
│               ─────────────────────────────────────►                    │
│  ┌──────────┐                                        ┌──────────┐      │
│  │Data Eng. │  Total GPU: ~50 H100-hours             │ Self-    │      │
│  │(ongoing) │  Total Cost: <€200 compute             │ Learning │      │
│  └──────────┘  + ~€50K data engineering              │ (Latent  │      │
│                                                      │  RAG)    │      │
│                                                      └──────────┘      │
│                                                      (continuous)      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Phase 1: Concept Distillation

### Goal
Convert raw knowledge into high-quality 10-element concept tensors and GPF problem tensors.

### Method

1. **Source Collection:** Gather raw knowledge from curated sources (see Section 12)
2. **API-Swarm Extraction:** Use frontier LLMs (GPT-4, Claude, Gemini) in parallel as concept extractors
3. **Template Filling:** Each concept is structured into the 10-element template
4. **Quality Assurance:** C2E metric validation (≥80/100), code execution, consistency checks
5. **Expert Review:** Domain experts validate a stratified sample (10%) of distilled concepts

### Prompt Engineering for Concept Extraction

```python
CONCEPT_EXTRACTION_PROMPT = """
You are a world-class ontological engineer. Given the following source 
material about the topic "{topic}", extract a complete formalized concept 
with all 10 elements:

1. ONTOLOGICAL SCAFFOLDING
   - Formal definitions (mathematical where possible)
   - Taxonomy (is-a relationships)
   - Modular composition (sub-concepts and their architecture)

2. ABSTRACTION LEVEL (choose 1-4)
   - Level 1: Meta/Philosophical
   - Level 2: Theoretical/Algorithmic
   - Level 3: Applied/Domain-Specific
   - Level 4: Concrete/Implementation

3. AXIOMATIC BASE
   - List all non-negotiable assumptions
   - Provide formal logical axioms where possible

4. RELATIONAL NETWORK
   - Intra-concept dependencies (SysML block diagram format)
   - Data flow relationships

5. INFERENTIAL FRAMEWORK
   - Key deductions and reasoning patterns
   - Derivation rules

6. METHODOLOGICAL APPARATUS
   - Step-by-step application methods
   - Operational constraints

7. ILLUSTRATIVE CODE (Python)
   - Working, executable code that demonstrates the concept
   - Must include assertions/tests

8. GOAL ORIENTATION & SCOPE
   - What problems does this concept solve?
   - Target domain and users

9. LIMITATIONS & RISKS
   - Known boundaries of applicability
   - Failure modes and edge cases

10. INTER-CONCEPT RELATIONSHIPS
    - Related concepts and synergies
    - Dependencies and conflicts

Source material:
{source_text}
"""
```

### Scale Target
- **Stage 1:** 500,000 concepts covering core domains (CS, math, physics, engineering)
- **Stage 2:** 2,000,000 concepts covering broader knowledge
- **Total GPF problems:** 200,000 (Stage 1), 800,000 (Stage 2)

---

## 6. Phase 2: Contrastive Concept Embedding

### Goal
Train the concept encoder and the embedding/reranker model that maps concepts to a dense vector space where semantically related concepts are close.

### Architecture

```
┌──────────────────────────────────────────────┐
│           CONCEPT EMBEDDING MODEL            │
│                                              │
│  Input: Structured concept (10 elements)     │
│                                              │
│  ┌─────────────┐   ┌─────────────┐          │
│  │  Element     │   │  Element    │          │
│  │  Encoders    │   │  Fusion     │          │
│  │  (10 × MLP)  │──►│  (Cross-    │          │
│  │              │   │  Attention) │          │
│  └─────────────┘   └──────┬──────┘          │
│                           │                  │
│                           ▼                  │
│                    ┌──────────────┐          │
│                    │  Concept     │          │
│                    │  Embedding   │          │
│                    │  e ∈ ℝ^{d_e} │          │
│                    └──────────────┘          │
│                                              │
│  Training: InfoNCE contrastive loss          │
│  Positives: Inter-Concept Relations (E10)    │
│  Negatives: Random + hard negative mining    │
└──────────────────────────────────────────────┘
```

### Training Details

| Hyperparameter | Value | Rationale |
|----------------|-------|-----------|
| Embedding dimension | 768 | Matches common embedding models for interoperability |
| Temperature | 0.07 | Standard for contrastive learning (Chen et al., 2020) |
| Batch size | 2048 | Large batch for diverse negatives |
| Learning rate | 1e-4 | AdamW with cosine decay (accumulation-corrected) |
| Training steps | 50,000 | ~4 H100-hours |
| Hard negative ratio | 30% | Concepts from same domain but different sub-fields |
| False negative masking | Supervised | Same-cluster off-diagonal logits masked to -∞ |

### Cross-Encoder Reranker

After embedding-based retrieval (approximate nearest neighbor), a cross-encoder reranker scores the top-k candidates:

```
score(p, c) = MLP(CrossAttention(p, c))
```

The reranker is trained with pairwise ranking loss on human-curated relevance judgments.

---

## 7. Phase 3: Algebraic Pre-Training

### Goal
Train the LARE core to correctly perform algebraic operations (⊕, ⊗, ⊖, Π) and learn the constraint mask Φ.

### Training Tasks

#### Task 1: Algebraic Operation Learning

Given pairs of concepts and known relationships, train the operators:

| Operation | Training Signal | Example |
|-----------|----------------|---------|
| **⊕ (Compose)** | Composed concept ≈ target concept | "RL" ⊕ "Autonomous Driving" ≈ "RL for AD" |
| **⊗ (Bind)** | Bound concept solves target problem | "Formal Verification" ⊗ "Scenario Gen" ≈ solution |
| **⊖ (Difference)** | Residual captures unique aspects | "Deep RL" ⊖ "RL" ≈ "Deep Learning aspects" |
| **Π (Project)** | Projection produces valid solution | Π(reasoning_state) ∈ solution space |

Loss function:

```
L_algebra = ||c₁ ⊕ c₂ - c_target||² + ||c ⊗ p - s_target||²
          + ||(c₁ ⊕ c₂) ⊖ c₂ - c₁||² + ||Π²(c) - Π(c)||²
```

#### Task 2: Operator Selection Learning

Train the gating mechanism to select the correct operator:

```
L_gate = -Σ_m y_m · log(softmax(W₁·p, ..., W_M·p)_m)
```

where `y_m` is the ground truth operator label for each training example. The softmax ensures a strict convex combination ($\sum_m g_m = 1$), guaranteeing the aggregate Lipschitz constant does not exceed 1.

#### Task 3: Constraint Mask Learning

Train the Φ mask to correctly nullify constraint-violating states:

```
L_mask = ||Φ(p, c_valid) - c_valid||² + ||Φ(p, c_violating)||²
```

This loss has two terms:
- Valid concepts should pass through the mask unchanged
- Constraint-violating concepts should be zeroed out

Training data is generated by:
1. Taking valid concepts and verifying they satisfy constraints
2. Generating adversarial perturbations that violate specific constraints
3. Training the mask to distinguish between the two

### Spectral Norm Regularization

To ensure convergence (Lemma 1 conditions), we enforce spectral norm bounds on all parameter matrices:

```
L_reg = Σ_W max(0, ||W||_op - γ_W)²
```

where `γ_W < 1` is the target spectral norm bound, set per-layer based on the contraction analysis.

This connects directly to the **SIGReg regularization** from ALPS/ALPS-4B (Bardes et al., 2024), which provides a principled framework for controlling representation collapse while maintaining contraction properties.

### Training Details

| Hyperparameter | Value | Rationale |
|----------------|-------|-----------|
| Model size | 1B parameters (LARE core) | Balance of capacity and efficiency |
| Context (concepts) | 64 concepts per problem | Covers typical reasoning scenarios |
| Reasoning steps | 8 | Sufficient for convergence (Corollary 4.1) |
| Learning rate | 3e-4 | AdamW with warmup + cosine decay (accumulation-corrected) |
| Warmup steps | 2,000 | Stabilize initial training |
| Batch size | 512 | Concept-problem pairs |
| Training steps | 200,000 | ~30 H100-hours |
| Spectral norm target | 0.9 | Ensures contraction with L ≈ 0.7 |

---

## 8. Phase 4: End-to-End Fine-Tuning

### Goal
Fine-tune the complete pipeline (Encoder → LARE → Decoder) on downstream tasks.

### Architecture

```
┌──────────┐   ┌──────────┐   ┌──────────┐
│ Encoder  │──►│  LARE    │──►│ Decoder  │
│ (1B)     │   │  (1B)    │   │  (1B)    │
└──────────┘   └──────────┘   └──────────┘
   ↑                              │
   └──────── gradient flow ◄──────┘
```

### Fine-Tuning Tasks

| Task | Dataset | Metric | Purpose |
|------|---------|--------|---------|
| SCAN | SCAN splits | Exact match accuracy | Compositional generalization |
| Concept Generation | C2E benchmark | C2E score | Concept quality |
| Constraint Satisfaction | AD scenarios | Formal validity rate | Safety verification |
| Knowledge Retrieval | HuggingFace 4QDR | Recall@k | Retrieval quality |

### Training Details

| Hyperparameter | Value |
|----------------|-------|
| Learning rate | 1e-5 (encoder/decoder), 3e-5 (LARE) |
| Batch size | 256 |
| Training steps | 100,000 |
| GPU time | ~16 H100-hours |
| Gradient clipping | 1.0 |
| Weight decay | 0.01 |

---

## 9. Self-Learning Loop (Latent RAG)

### Overview

After initial training, ACRE enters a continuous self-learning loop where verified solutions are added back to the concept knowledge store:

```
┌──────────────────────────────────────────────────────────────────┐
│                   SELF-LEARNING LOOP                            │
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐  │
│  │ Problem  │───►│ Retrieve │───►│  LARE    │───►│ Verify   │  │
│  │ Input    │    │ Concepts │    │ Reason   │    │ Solution │  │
│  └──────────┘    └──────────┘    └──────────┘    └──────┬───┘  │
│                       ▲                                 │      │
│                       │                                 │      │
│                       │           Q(s,p) > Q_min?       │      │
│                       │           ┌────┐   ┌────┐       │      │
│                       │           │ Yes│   │ No │       │      │
│                       │           └──┬─┘   └──┬─┘       │      │
│                       │              │        │         │      │
│                       │              ▼        ▼         │      │
│                  ┌────┴────┐    ┌────────┐  Discard     │      │
│                  │ Concept │◄───┤  Store  │              │      │
│                  │  Store  │    │  as New │              │      │
│                  │   (RAG) │    │ Concept │              │      │
│                  └─────────┘    └────────┘              │      │
│                                                          │      │
│  Theorem 6: Solution quality monotonically improves     │      │
│  Corollary 6.1: No catastrophic forgetting              │      │
└──────────────────────────────────────────────────────────┘      │
```

### Key Properties

1. **Append-Only Store:** Verified solutions are added, never removed (prevents forgetting)
2. **Quality Gate:** Only solutions with `Q(s,p) > Q_min` are stored (prevents pollution)
3. **Embedding Refresh:** Periodically re-embed stored concepts for optimal retrieval
4. **Deduplication:** New concepts are checked for near-duplicates (cosine similarity > 0.95) and merged

### Self-Learning Metrics

| Metric | Target | Monitoring |
|--------|--------|------------|
| Store growth rate | ~1000 concepts/day | Dashboard |
| Quality gate pass rate | >60% | Per-batch tracking |
| Retrieval hit rate | >80% | Top-10 relevance |
| Solution quality trend | Monotonically non-decreasing | Rolling average |

---

## 10. Curriculum Design

### Rationale

ACRE uses curriculum learning to progressively increase task difficulty. This is motivated by:
1. **Algebraic foundations first:** The operators must work correctly on simple cases before handling complex compositions
2. **Convergence stability:** Starting with easy tasks ensures the contraction property holds initially
3. **Knowledge bootstrapping:** Simple concepts form the foundation for complex ones

### Curriculum Stages

```
Stage 1: Atomic Concepts (Weeks 1-2)
├── Single-concept encoding and reconstruction
├── Element masking and prediction
└── Simple C2E evaluation

Stage 2: Pairwise Operations (Weeks 2-3)
├── Composition of 2 concepts
├── Binding concepts to simple problems
├── Difference between related concepts
└── SCAN simple split

Stage 3: Multi-Step Reasoning (Weeks 3-4)
├── 3-5 concept compositions
├── Multi-step LARE reasoning (2-4 steps)
├── Constraint mask activation
└── SCAN length split

Stage 4: Complex Reasoning (Weeks 4-6)
├── 10+ concept compositions
├── Full LARE reasoning (8+ steps)
├── Adversarial constraint testing
├── SCAN all splits
└── Domain-specific tasks (AD, scientific reasoning)

Stage 5: Self-Learning (Week 6+)
├── Latent RAG enabled
├── Continuous concept accumulation
├── Progressive domain expansion
└── Cross-domain transfer tasks
```

### Difficulty Metrics

| Stage | # Concepts | # Reasoning Steps | Composition Depth | Constraint Complexity |
|-------|-----------|-------------------|-------------------|-----------------------|
| 1 | 1 | 1 | 0 | None |
| 2 | 2 | 2 | 1 | Simple |
| 3 | 5 | 4 | 3 | Medium |
| 4 | 10+ | 8 | 5+ | Complex |
| 5 | Unlimited | 8-16 | Arbitrary | Full |

---

## 11. Compute Plan

### H100 Training Budget

| Phase | GPU-Hours | Cost (€/hr) | Total Cost | Duration |
|-------|-----------|-------------|------------|----------|
| Phase 2: Contrastive Embedding | 4 | €3.50 | €14 | ~1 hour |
| Phase 3: Algebraic Pre-Training | 30 | €3.50 | €105 | ~8 hours |
| Phase 4: End-to-End Fine-Tuning | 16 | €3.50 | €56 | ~4 hours |
| **Total GPU** | **50** | | **€175** | **~13 hours** |

### Full Budget Breakdown (Stage 1: €3M)

| Category | Budget | % | Description |
|----------|--------|---|-------------|
| **Data Engineering** | €2,400,000 | 80% | Concept distillation, API costs, expert validators |
| **Compute (GPU)** | €200 | <0.01% | Model training |
| **Infrastructure** | €100,000 | 3.3% | Cloud, storage, monitoring, CI/CD |
| **Personnel** | €400,000 | 13.3% | Research engineers, domain experts |
| **Evaluation & Benchmarks** | €50,000 | 1.7% | Benchmark suites, human evaluation |
| **Contingency** | €49,800 | 1.7% | Buffer |
| **Total** | **€3,000,000** | **100%** | |

**Key Insight:** >98% of the budget goes to data engineering, not compute. This is by design — ACRE's training efficiency means GPU costs are negligible. The real bottleneck is curating high-quality formalized concepts.

### Hardware Requirements

| Component | Specification | Quantity |
|-----------|--------------|----------|
| GPU | NVIDIA H100 80GB | 1 (single-node training) |
| CPU | AMD EPYC 9554 or equivalent | 64 cores |
| RAM | 512 GB DDR5 | 1 |
| Storage | 2 TB NVMe SSD | 1 |
| Network | 100 Gbps (for API calls) | 1 |

**Total training fits on a single H100 node.** No multi-node communication overhead. No complex distributed training infrastructure. This is a deliberate design choice enabled by ACRE's compression.

---

## 12. Data Sources

### Primary Sources

| Source | Content | Concepts Expected | Status |
|--------|---------|-------------------|--------|
| **Wikipedia** (English) | General knowledge | ~200,000 | Available |
| **arXiv** | Scientific papers | ~100,000 | Available |
| **GitHub** (top repos) | Code implementations | ~50,000 | Available |
| **YouTube Transcripts** | Educational content | ~30,000 | Available |
| **Textbooks** (OpenStax, MIT OCW) | Structured education | ~50,000 | Available |
| **HuggingFace 4QDR Dataset** | Pre-curated concepts | ~20,000 | Custom |
| **ISO Standards** | Engineering standards | ~10,000 | Licensed |
| **SysML/UML Models** | Systems engineering | ~5,000 | Custom |
| **Domain Expert Contributions** | Specialized knowledge | ~35,000 | Stage 1 |
| **Total** | | **~500,000** | |

### Concept Domain Distribution

```
Computer Science       ████████████████████  28%
Mathematics            ███████████████       22%
Physics/Engineering    ██████████            15%
Systems Engineering    ████████              12%
Biology/Chemistry      ███████               10%
Social Sciences        ████                   6%
Other STEM             ███                    4%
Humanities             ██                     3%
```

### Data Quality Pipeline

```
Raw Source → API Extraction → Template Validation → C2E Scoring → Expert Review → Training Set
                                    │                     │                │
                                    ▼                     ▼                ▼
                              Reject if              Reject if        Reject if
                              incomplete             C2E < 80         expert flags
                              (auto)                 (auto)           (manual)
```

Expected rejection rates:
- Template validation: ~15% rejected
- C2E scoring: ~20% rejected
- Expert review: ~5% rejected
- **Net yield: ~65%** of initial extractions

---

## 13. Evaluation & Validation

### Benchmark Suite

| Benchmark | Metric | Target | Purpose |
|-----------|--------|--------|---------|
| **SCAN** (all splits) | Exact match accuracy | >97% | Compositional generalization |
| **COGS** | Structural accuracy | >95% | Compositional generalization |
| **C2E Metric** | Weighted score (0-100) | >85 average | Concept quality |
| **Compression Analysis** | Compression ratio | >500× | Knowledge compression |
| **Constraint Satisfaction** | Formal validity rate | 100% | Safety verification |
| **Retrieval Quality** | Recall@10 | >85% | Concept embedding quality |
| **Convergence Speed** | Steps to ε-accuracy | <20 steps | Reasoning efficiency |
| **Ablation: No Φ mask** | Constraint violation rate | (baseline) | Mask importance |
| **Ablation: No algebra** | SCAN accuracy | (baseline) | Algebra importance |
| **Ablation: Random elements** | C2E score | (baseline) | Structure importance |

### Ablation Studies

We plan the following ablation studies to validate each component:

| Ablation | What We Remove | Expected Impact |
|----------|---------------|-----------------|
| No constraint mask (Φ) | Remove Φ from LARE | Constraint satisfaction drops from 100% to ~50% |
| No concept algebra | Replace ⊕,⊗,⊖,Π with standard attention | SCAN accuracy drops from 99% to ~60% |
| Random element structure | Shuffle the 10 element assignments | C2E score drops from 85 to ~40 |
| No curriculum | Train all difficulties simultaneously | Convergence takes 3× longer |
| No self-learning | Disable Latent RAG loop | Solution quality plateaus early |
| Reduced elements (5) | Use 5 elements instead of 10 | C2E drops to ~65, compression degrades |

### Human Evaluation

In addition to automated benchmarks, we conduct human evaluation:

1. **Expert Concept Review:** Domain experts rate distilled concepts on a 1-5 scale across accuracy, completeness, and utility
2. **Reasoning Trace Audit:** Experts verify that the algebraic reasoning steps are semantically meaningful
3. **Output Quality Assessment:** For domain-specific tasks (AD scenarios, scientific reasoning), experts evaluate the practical utility of generated solutions

---

## Summary: Why This Training Approach Works

The ACRE training methodology inverts the standard AI training paradigm:

| Dimension | Standard Approach | ACRE Approach |
|-----------|-------------------|---------------|
| **Data** | 2T raw tokens | 500K formalized concepts |
| **Signal** | Next-token prediction | Concept structure consistency |
| **Compute** | Millions of GPU-hours | ~50 GPU-hours |
| **Cost** | $100M+ | <€200 compute |
| **Bottleneck** | GPU hardware | Data engineering quality |
| **Scaling** | More data + more compute | Better concepts + better algebra |
| **Result** | Statistical associations | Formal algebraic reasoning |

The fundamental insight is that **knowledge has structure**, and exploiting that structure enables extreme compression, efficient training, and formal reasoning — none of which are possible when treating knowledge as flat token sequences.

---

## References

1. Chen, T., Kornblith, S., Norouzi, M., & Hinton, G. (2020). A Simple Framework for Contrastive Learning of Visual Representations. *ICML 2020*.
2. Bardes, A., et al. (2024). Revisiting Feature Prediction for Learning Visual Representations from Video. *arXiv:2404.08471*.
3. Grill, J. B., et al. (2020). Bootstrap Your Own Latent: A New Approach to Self-Supervised Learning. *NeurIPS 2020*.
4. Tishby, N., & Zaslavsky, N. (2015). Deep Learning and the Information Bottleneck Principle. *ITW 2015*.
5. Bengio, Y., Louradour, J., Collobert, R., & Weston, J. (2009). Curriculum Learning. *ICML 2009*.
6. Cover, T. M., & Thomas, J. A. (2006). *Elements of Information Theory* (2nd ed.). Wiley.
7. Lake, B. M., & Baroni, M. (2018). Generalization without Systematicity. *ICML 2018*.
8. Kanerva, P. (2009). Hyperdimensional Computing. *Cognitive Computation*, 1(2), 139–159.
9. Epoch AI. (2024). Trends in Machine Learning Compute. *epochai.org*.
10. Oord, A. van den, Li, Y., & Vinyals, O. (2019). Representation Learning with Contrastive Predictive Coding. *arXiv:1807.03748*.

---

*© 2026 4QDR.AI Labs. Training Methodology v2.0.*
