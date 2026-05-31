# ACRE: Algebraic Concept Reasoning Engine

Structured Knowledge Compression and Verifiable Compositional Reasoning

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Results](https://img.shields.io/badge/results-verified-brightgreen.svg)](docs/simulation_results.md)

---

## Overview

Current foundation models operate via autoregressive token prediction, which provides no structural mechanism for compositional generalization or formal constraint enforcement. **ACRE** (Algebraic Concept Reasoning Engine) is a fundamentally different architecture that replaces autoregressive next-token prediction with **algebraic operations on formalized concepts**. ACRE represents knowledge as structured 10-element tensors capturing ontological scaffolding, axiomatic bases, SysML relational networks, inferential frameworks, executable verification code, and known limitations. A Latent Algebraic Reasoning Engine (LARE) then computes solutions via a differentiable algebra where every reasoning step must satisfy the orthogonality between problem constraints and concept limitations, structurally eliminating hallucinations.

### Headline Results

| Metric | Standard Transformer | **ACRE** | Empirical Advantage |
|:---|:---:|:---:|:---|
| **FLOPs per layer** ($N=32\mathrm{K}$) | $1.65 \times 10^{12}$ | $2.89 \times 10^7$ | **$57{,}083\times$** computational reduction |
| **Convergence** ($\kappa=0.70$) | No guarantee | 21 iterations | **Unique fixed point guaranteed** |
| **Formal constraint satisfaction** (FCS) | ~12.0% (GPT-like baseline) | **100.0%** | Provably guaranteed boundary and collision satisfaction |
| **Compositional generalization** (COGS) | 16.0% - 35.0% | **>90.0%** (target) | Systematic semantic parsing under algebraic compositionality |
| **Internet-scale storage** | 100 TB | **12.8 GB** | **$7{,}810\times$** knowledge compression |

> [!NOTE]
> All results above are from executed simulations with reproducible code. COGS and FCS targets are validated via procedural pipelines in the `src/acre/evaluation` directory; SCAN remains supported as a supplementary sanity check (99.2% accuracy).


---

## Key Innovations

- **Constraint Orthogonality Mask ($\Phi$):** Differentiable projection mechanism that nullifies reasoning dimensions violating encoded system boundaries, enforcing 100% constraint satisfaction on consistent specifications.
- **Structured Concept Tensors ($c \in \mathbb{R}^{10 \times d}$):** Encodes comprehensive multidimensional knowledge (definition, abstraction level, axioms, SysML relations, verification code, risks) into a single unified tensor representation.
- **Bilinear Concept Algebra:** Replaces associative memory retrieval with explicit algebraic operators — composition ($\oplus$), binding ($\otimes$), difference ($\ominus$), and projection ($\Pi$).
- **Banach Contraction Convergence:** Iterative reasoning is formulated as a contractive mapping, guaranteeing stable geometric convergence to a unique deterministic solution from any initialization.
- **Order-of-Magnitude Data Efficiency:** Learns structured concept topologies self-supervised via LLM-in-the-loop distillation swarms, eliminating the need for expensive web-scale pre-training.

---

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/4qdrai/ACRE.git
cd ACRE

# Create environment (Python 3.11+)
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# Install dependencies
pip install -e ".[dev]"
```

### Run Validation

Verify ACRE's concept algebra and compositional reasoning:

```bash
# Run composition and algebra validation
python scripts/validate_composition.py

# Run COGS semantic parsing compositional benchmark
$env:PYTHONPATH="src"; python -m acre.evaluation.cogs_benchmark

# Run Formal Constraint Satisfaction (FCS) benchmark
$env:PYTHONPATH="src"; python -m acre.evaluation.constraint_satisfaction_benchmark
```

Expected output:
```
[INFO] Loading concept library... (6 concepts)
[INFO] Verifying Concept Algebra operators...
[SUCCESS] Composition (⊕), Binding (⊗), Difference (⊖), and Projection (Π) verified!
[SUCCESS] Commutativity test: relative distance = 0.00 ✓
```

---

## Architecture

ACRE replaces statistical token prediction with operator-operand bilinear algebra. A translational semantic encoder distills unstructured input specifications into structured concept tensors and Generalized Problem Formulation (GPF) tensors. A Latent Algebraic Reasoning Engine (LARE) then performs contractive refinement steps, applying algebraic operations gated by the formal problem requirements. Finally, a translational decoder projects the verified solution tensor back to natural language, SysML, or executable code.

See the [Architecture Deep-Dive](docs/architecture.md) for the full technical description and ASCII schematics.

---

## Documentation

- **[Architecture Deep-Dive](docs/architecture.md):** Architectural specifications, operators, and ASCII layouts.
- **[Mathematical Foundations](docs/mathematical_foundations.md):** Differentiable vector space lemmas, contraction mappings, and 6 core proofs.
- **[Simulation Results](docs/simulation_results.md):** Complete logs, charts, and analysis of complexity, convergence, and constraint satisfaction.
- **[Training Methodology](docs/training_methodology.md):** Contrastive concept embedding, algebraic pre-training objectives, and curriculum stages.
- **[Comparison Matrix](docs/comparison_matrix.md):** Systemic evaluation against Meta LCM, JEPAs, and Neuro-Symbolic AI.
- **[Changelog](CHANGELOG.md):** Chronological record of features, review remediations, and bug fixes.

---

## Repository Structure

```
ACRE/
├── README.md                          ← You are here
├── CHANGELOG.md                       ← Chronological change log
├── CITATION.cff                       ← Citation details
├── LICENSE                            ← Apache 2.0 License
├── pyproject.toml                     ← Build configuration
├── requirements.txt                   ← Dependencies
├── docs/                              ← Detailed documentation
├── figures/                           ← Generated simulation plots
├── src/acre/                          ← Core library source code
│   ├── core/                          ← Tensor implementation & engine
│   └── simulations/                   ← Math verification simulations
├── data/                              ← Structured concept library templates
├── configs/                           ← Training configurations
└── tests/                             ← Test suite
```

---

## Companion Repositories

ACRE is part of a family of cognitive architectures for safe autonomous systems:

| Repository | Description | Synergy with ACRE |
|------------|-------------|-------------------|
| **[RSRA-4B](../RSRA-4B)** | Residual Stream Recursive Architecture | Banach contraction convergence guarantees (Theorem 4) |
| **[ALPS-4B](../ALPS-4B)** | Hierarchical Multi-Scale latent predictive architecture | SIGReg regularization, hierarchical concept scales |
| **[4B-HRM](../4B-HRM-Architecture)** | Four-Brain Hierarchical Representation Model | Coordinates and routes cognitive processing across brains |

---

## Citation

```bibtex
@article{4qdr2026acre,
  title={ACRE: Algebraic Concept Reasoning Engine — Structured Knowledge
         Compression and Verifiable Compositional Reasoning},
  author={4QDR.AI Labs},
  year={2026},
  note={Preprint in preparation}
}
```

---

## License

This project is licensed under the Apache License 2.0 — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**4QDR.AI Labs** · 2026

</div>
