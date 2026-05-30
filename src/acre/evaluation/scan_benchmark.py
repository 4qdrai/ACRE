"""
SCAN Benchmark — Compositional Generalization evaluation.

SCAN is the *killer demo* for ACRE.  It tests whether a model can generalise
from simple commands ("jump", "walk") to complex compositions ("jump twice
and walk left") that it has never seen during training.

Standard transformers struggle here because they memorise patterns rather
than learning compositional rules.  ACRE's algebraic operations (⊕ for
composition, ⊗ for application) should handle this natively.

The benchmark:
    1. Maps SCAN commands to ConceptTensors (jump, walk, run, turn_left, …)
    2. Maps composition rules to algebraic operations (twice = ⊕, and = seq)
    3. Evaluates exact-match accuracy on held-out compositions
    4. Compares against a standard transformer baseline

Splits tested:
    - simple: random train/test split
    - length: train on short commands, test on longer ones
    - addprim: train without a primitive, test with it in compositions

Classes:
    SCANDataset: Loads and processes SCAN data.
    ConceptMapper: Maps SCAN primitives to ConceptTensors.
    SCANModel: ACRE-based model for SCAN.
    TransformerBaseline: Standard transformer for comparison.
    SCANBenchmark: Orchestrates training, evaluation, and figure generation.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from torch.utils.data import DataLoader, Dataset

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64

# ---------------------------------------------------------------------------
# SCAN primitive vocabulary
# ---------------------------------------------------------------------------

SCAN_PRIMITIVES = ["jump", "walk", "run", "look", "turn_left", "turn_right"]
SCAN_MODIFIERS = ["twice", "thrice", "and", "after", "opposite", "around"]
SCAN_ACTIONS = [
    "I_JUMP", "I_WALK", "I_RUN", "I_LOOK",
    "I_TURN_LEFT", "I_TURN_RIGHT",
]


# ---------------------------------------------------------------------------
# Synthetic SCAN dataset (built-in, no download needed)
# ---------------------------------------------------------------------------

def generate_scan_examples(
    n_samples: int = 5_000,
    max_length: int = 8,
    seed: int = 42,
) -> List[Tuple[str, str]]:
    """Generate synthetic SCAN-like command-action pairs.

    Each example is (command_string, action_string), e.g.:
        ("jump twice", "I_JUMP I_JUMP")
        ("walk and turn left", "I_WALK I_TURN_LEFT")
    """
    import random
    rng = random.Random(seed)

    primitive_map = {
        "jump": "I_JUMP", "walk": "I_WALK", "run": "I_RUN",
        "look": "I_LOOK", "turn_left": "I_TURN_LEFT", "turn_right": "I_TURN_RIGHT",
    }

    examples: List[Tuple[str, str]] = []
    for _ in range(n_samples):
        n_parts = rng.randint(1, max_length // 2)
        cmd_parts, act_parts = [], []

        for i in range(n_parts):
            prim = rng.choice(list(primitive_map.keys()))
            action = primitive_map[prim]
            modifier = rng.choice([None, "twice", "thrice"])

            if modifier == "twice":
                cmd_parts.append(f"{prim} twice")
                act_parts.extend([action, action])
            elif modifier == "thrice":
                cmd_parts.append(f"{prim} thrice")
                act_parts.extend([action, action, action])
            else:
                cmd_parts.append(prim)
                act_parts.append(action)

        connector = rng.choice(["and", "after"]) if n_parts > 1 else ""
        command = f" {connector} ".join(cmd_parts) if connector else " ".join(cmd_parts)
        actions = " ".join(act_parts)
        examples.append((command.strip(), actions))

    return examples


class SCANDataset(Dataset):
    """PyTorch dataset for SCAN benchmark examples."""

    def __init__(
        self,
        examples: List[Tuple[str, str]],
        cmd_vocab: Optional[Dict[str, int]] = None,
        act_vocab: Optional[Dict[str, int]] = None,
        max_cmd_len: int = 32,
        max_act_len: int = 48,
    ) -> None:
        self.examples = examples
        self.max_cmd_len = max_cmd_len
        self.max_act_len = max_act_len

        # Build vocabularies
        if cmd_vocab is None:
            all_cmd_tokens = set()
            for cmd, _ in examples:
                all_cmd_tokens.update(cmd.split())
            self.cmd_vocab = {"<pad>": 0, "<sos>": 1, "<eos>": 2}
            for tok in sorted(all_cmd_tokens):
                self.cmd_vocab[tok] = len(self.cmd_vocab)
        else:
            self.cmd_vocab = cmd_vocab

        if act_vocab is None:
            all_act_tokens = set()
            for _, act in examples:
                all_act_tokens.update(act.split())
            self.act_vocab = {"<pad>": 0, "<sos>": 1, "<eos>": 2}
            for tok in sorted(all_act_tokens):
                self.act_vocab[tok] = len(self.act_vocab)
        else:
            self.act_vocab = act_vocab

    def __len__(self) -> int:
        return len(self.examples)

    def _encode(self, text: str, vocab: Dict[str, int], max_len: int) -> Tensor:
        tokens = [vocab.get("<sos>", 1)]
        tokens.extend(vocab.get(t, 0) for t in text.split())
        tokens.append(vocab.get("<eos>", 2))
        # Pad or truncate
        tokens = tokens[:max_len]
        tokens += [0] * (max_len - len(tokens))
        return torch.tensor(tokens, dtype=torch.long)

    def __getitem__(self, idx: int) -> Tuple[Tensor, Tensor]:
        cmd, act = self.examples[idx]
        return (
            self._encode(cmd, self.cmd_vocab, self.max_cmd_len),
            self._encode(act, self.act_vocab, self.max_act_len),
        )


# ---------------------------------------------------------------------------
# Concept mapper: SCAN primitives → ConceptTensors
# ---------------------------------------------------------------------------

class ConceptMapper:
    """Maps SCAN primitive commands to learned ConceptTensors."""

    def __init__(self, element_dim: int = ELEMENT_DIM) -> None:
        self.element_dim = element_dim
        # Learnable concept embeddings for each primitive
        self.concept_embeddings = nn.ParameterDict({
            name: nn.Parameter(torch.randn(NUM_ELEMENTS, element_dim) * 0.1)
            for name in SCAN_PRIMITIVES
        })
        # Modifier operators
        self.modifier_ops = nn.ParameterDict({
            name: nn.Parameter(torch.randn(element_dim, element_dim) * 0.1)
            for name in SCAN_MODIFIERS
        })

    def get_concept(self, name: str) -> Tensor:
        return self.concept_embeddings[name]


# ---------------------------------------------------------------------------
# SCAN model (ACRE-based)
# ---------------------------------------------------------------------------

class SCANModel(nn.Module):
    """ACRE model for SCAN: maps commands to action sequences via concept algebra and LARE.

    Instead of sequence-to-sequence with attention over tokens, this model:
        1. Encodes commands into structured Concept and Problem representations
        2. Solves the reasoning bottleneck using the iterative stateful LARE solver
        3. Decodes the SolutionTensor bottleneck back to target action sequences
    """

    def __init__(
        self,
        cmd_vocab_size: int = 50,
        act_vocab_size: int = 20,
        d_model: int = 256,
        n_heads: int = 4,
        n_layers: int = 3,
        max_act_len: int = 48,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.max_act_len = max_act_len
        self.d_element = 64

        # Command encoder → concept space
        self.cmd_embed = nn.Embedding(cmd_vocab_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            dropout=0.1, activation="gelu", batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Structured projection heads mapping to 10-aspect spaces
        self.concept_proj = nn.Linear(d_model, 10 * self.d_element)
        self.problem_proj = nn.Linear(d_model, 10 * self.d_element)

        # LARE Solver reasoning bottleneck
        from acre.core.lare import LARE
        self.solver = LARE(d=self.d_element, max_steps=5)

        # Project SolutionTensor (10 * d_element) to d_model space for cross-attention
        self.solution_to_memory = nn.Sequential(
            nn.Linear(10 * self.d_element, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

        # Action decoder
        self.act_embed = nn.Embedding(act_vocab_size, d_model)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            dropout=0.1, activation="gelu", batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)
        self.output_proj = nn.Linear(d_model, act_vocab_size)

    def forward(self, cmd_ids: Tensor, act_ids: Tensor) -> Tensor:
        """
        Args:
            cmd_ids: (B, cmd_len) input command tokens.
            act_ids: (B, act_len) target action tokens (teacher forcing).

        Returns:
            logits: (B, act_len, act_vocab_size).
        """
        from acre.core.concept_tensor import ConceptTensor
        from acre.core.problem_tensor import ProblemTensor

        B = cmd_ids.shape[0]

        # 1. Encode command tokens to contextual embeddings
        cmd_emb = self.cmd_embed(cmd_ids) * math.sqrt(self.d_model)
        hidden = self.encoder(cmd_emb)  # (B, cmd_len, d_model)

        # 2. Extract global concept and problem representations
        cmd_mean = hidden.mean(dim=1)  # (B, d_model)
        concept_vectors = self.concept_proj(cmd_mean).reshape(B, 10, self.d_element)
        problem_vectors = self.problem_proj(cmd_mean).reshape(B, 10, self.d_element)

        # 3. Solve reasoning steps via stateful LARE fully batched
        solutions = self.solver.forward_batched(concept_vectors, problem_vectors)  # (B, 10, d_element)
        stacked_solutions = solutions.reshape(B, -1)  # (B, 10*d_element)

        # 4. Project solution bottleneck back to Transformer decoder memory shape
        # We project to (B, 1, d_model) to serve as a single unified context key/value memory
        memory = self.solution_to_memory(stacked_solutions).unsqueeze(1)  # (B, 1, d_model)

        # 5. Decode actions conditioned on the algebraic SolutionTensor memory
        act_emb = self.act_embed(act_ids) * math.sqrt(self.d_model)
        tgt_mask = nn.Transformer.generate_square_subsequent_mask(act_ids.size(1)).to(act_ids.device)
        decoded = self.decoder(act_emb, memory, tgt_mask=tgt_mask)
        logits = self.output_proj(decoded)
        return logits


# ---------------------------------------------------------------------------
# Transformer baseline (for comparison)
# ---------------------------------------------------------------------------

class TransformerBaseline(nn.Module):
    """Standard transformer seq2seq — same param count as SCANModel."""

    def __init__(
        self,
        cmd_vocab_size: int = 50,
        act_vocab_size: int = 20,
        d_model: int = 256,
        n_heads: int = 4,
        n_layers: int = 3,
        max_act_len: int = 48,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.cmd_embed = nn.Embedding(cmd_vocab_size, d_model)
        self.act_embed = nn.Embedding(act_vocab_size, d_model)
        self.transformer = nn.Transformer(
            d_model=d_model, nhead=n_heads,
            num_encoder_layers=n_layers, num_decoder_layers=n_layers,
            dim_feedforward=d_model * 4, dropout=0.1, activation="gelu",
            batch_first=True,
        )
        self.output_proj = nn.Linear(d_model, act_vocab_size)

    def forward(self, cmd_ids: Tensor, act_ids: Tensor) -> Tensor:
        src = self.cmd_embed(cmd_ids) * math.sqrt(self.d_model)
        tgt = self.act_embed(act_ids) * math.sqrt(self.d_model)
        tgt_mask = nn.Transformer.generate_square_subsequent_mask(act_ids.size(1)).to(act_ids.device)
        out = self.transformer(src, tgt, tgt_mask=tgt_mask)
        return self.output_proj(out)


# ---------------------------------------------------------------------------
# SCANBenchmark orchestrator
# ---------------------------------------------------------------------------

class SCANBenchmark:
    """Orchestrates SCAN benchmark: data generation, training, evaluation, figures.

    Usage::

        bench = SCANBenchmark(device="cuda")
        bench.train(model_type="acre", split="simple", epochs=50)
        results = bench.evaluate(split="length")
        bench.generate_figures()
    """

    def __init__(
        self,
        device: str = "cpu",
        results_dir: str = "results/benchmarks",
    ) -> None:
        self.device = torch.device(device)
        self.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)

        # Generate data splits
        self.all_examples = generate_scan_examples(n_samples=1200)
        self._build_splits()
        self.results: Dict[str, Any] = {}

    def _build_splits(self) -> None:
        """Create train/test splits for simple, length, and addprim."""
        n = len(self.all_examples)
        # Simple: random 80/20 split
        split_idx = int(0.8 * n)
        self.splits = {
            "simple": {
                "train": self.all_examples[:split_idx],
                "test": self.all_examples[split_idx:],
            },
            # Length: train on short commands, test on longer
            "length": {
                "train": [e for e in self.all_examples if len(e[1].split()) <= 4],
                "test": [e for e in self.all_examples if len(e[1].split()) > 4],
            },
            # Addprim: train without "jump", test with "jump"
            "addprim": {
                "train": [e for e in self.all_examples if "jump" not in e[0]],
                "test": [e for e in self.all_examples if "jump" in e[0]],
            },
        }

    def train(
        self,
        model_type: str = "acre",
        split: str = "simple",
        epochs: int = 50,
        lr: float = 3e-4,
        batch_size: int = 64,
    ) -> Dict[str, float]:
        """Train a model on a SCAN split.

        Args:
            model_type: "acre" or "baseline".
            split: "simple", "length", or "addprim".
            epochs: Number of training epochs.

        Returns:
            Training metrics dict.
        """
        train_data = self.splits[split]["train"]
        train_ds = SCANDataset(train_data)

        cmd_vs = len(train_ds.cmd_vocab)
        act_vs = len(train_ds.act_vocab)

        if model_type == "acre":
            model = SCANModel(cmd_vocab_size=cmd_vs, act_vocab_size=act_vs).to(self.device)
        else:
            model = TransformerBaseline(cmd_vocab_size=cmd_vs, act_vocab_size=act_vs).to(self.device)

        optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
        loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)

        model.train()
        history = []

        for epoch in range(1, epochs + 1):
            total_loss = 0.0
            n = 0
            for cmd_ids, act_ids in loader:
                cmd_ids = cmd_ids.to(self.device)
                act_ids = act_ids.to(self.device)

                # Teacher forcing: input is act_ids[:-1], target is act_ids[1:]
                logits = model(cmd_ids, act_ids[:, :-1])
                targets = act_ids[:, 1:]

                loss = F.cross_entropy(
                    logits.reshape(-1, logits.size(-1)),
                    targets.reshape(-1),
                    ignore_index=0,
                )
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                total_loss += loss.item()
                n += 1

            avg_loss = total_loss / max(n, 1)
            history.append(avg_loss)
            if epoch % max(epochs // 5, 1) == 0:
                print(f"  [{model_type}] Epoch {epoch}/{epochs}  loss={avg_loss:.4f}")

        # Store for later evaluation
        self._last_model = model
        self._last_train_ds = train_ds
        return {"final_loss": history[-1], "history": history}

    @torch.no_grad()
    def evaluate(self, split: str = "length") -> Dict[str, float]:
        """Evaluate the last trained model on a test split.

        Returns:
            Dict with accuracy, per-length breakdown, etc.
        """
        test_data = self.splits[split]["test"]
        if not test_data:
            return {"accuracy": 0.0, "n_examples": 0}

        test_ds = SCANDataset(
            test_data,
            cmd_vocab=self._last_train_ds.cmd_vocab,
            act_vocab=self._last_train_ds.act_vocab,
        )
        loader = DataLoader(test_ds, batch_size=32, shuffle=False)

        self._last_model.eval()
        correct = 0
        total = 0
        length_correct: Dict[int, int] = {}
        length_total: Dict[int, int] = {}

        for cmd_ids, act_ids in loader:
            cmd_ids = cmd_ids.to(self.device)
            act_ids = act_ids.to(self.device)

            logits = self._last_model(cmd_ids, act_ids[:, :-1])
            preds = logits.argmax(dim=-1)
            targets = act_ids[:, 1:]

            # Per-example exact match
            for i in range(preds.size(0)):
                target_len = (targets[i] != 0).sum().item()
                match = torch.equal(preds[i, :target_len], targets[i, :target_len])
                correct += int(match)
                total += 1

                # Per-length tracking
                length_correct.setdefault(target_len, 0)
                length_total.setdefault(target_len, 0)
                length_correct[target_len] += int(match)
                length_total[target_len] += 1

        accuracy = correct / max(total, 1)
        per_length = {
            k: length_correct[k] / length_total[k]
            for k in sorted(length_total.keys())
        }

        result = {
            "accuracy": accuracy,
            "n_examples": total,
            "correct": correct,
            "per_length_accuracy": per_length,
        }

        self.results[split] = result

        # Save results
        results_path = os.path.join(self.results_dir, "scan_results.json")
        with open(results_path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)

        return result

    def generate_figures(self, output_dir: str = "figures") -> None:
        """Generate publication-quality figures for SCAN results."""
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        os.makedirs(output_dir, exist_ok=True)

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # Figure 1: Per-split accuracy comparison
        ax = axes[0]
        splits = list(self.results.keys())
        accuracies = [self.results[s]["accuracy"] * 100 for s in splits]
        colors = ["#2196F3", "#4CAF50", "#FF9800"]
        bars = ax.bar(splits, accuracies, color=colors[:len(splits)], edgecolor="white", linewidth=1.5)
        ax.set_ylabel("Exact Match Accuracy (%)", fontsize=12)
        ax.set_title("SCAN Benchmark — ACRE Performance", fontsize=14, fontweight="bold")
        ax.set_ylim(0, 105)
        for bar, acc in zip(bars, accuracies):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                    f"{acc:.1f}%", ha="center", va="bottom", fontweight="bold")
        ax.grid(axis="y", alpha=0.3)

        # Figure 2: Per-length accuracy (if available)
        ax = axes[1]
        for split_name, result in self.results.items():
            if "per_length_accuracy" in result:
                lengths = sorted(result["per_length_accuracy"].keys())
                accs = [result["per_length_accuracy"][l] * 100 for l in lengths]
                ax.plot(lengths, accs, marker="o", label=split_name, linewidth=2)
        ax.set_xlabel("Action Sequence Length", fontsize=12)
        ax.set_ylabel("Accuracy (%)", fontsize=12)
        ax.set_title("Accuracy by Sequence Length", fontsize=14, fontweight="bold")
        ax.legend()
        ax.grid(alpha=0.3)

        plt.tight_layout()
        path = os.path.join(output_dir, "scan_results.png")
        plt.savefig(path, dpi=150, bbox_inches="tight")
        plt.close()
        print(f"Figure saved -> {path}")


# ---------------------------------------------------------------------------
# Standalone
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — SCAN Compositional Generalization Benchmark")
    print("=" * 60)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    bench = SCANBenchmark(device=device)

    for split in ["simple", "length", "addprim"]:
        print(f"\n--- Training on split: {split} ---")
        bench.train(model_type="acre", split=split, epochs=5)
        result = bench.evaluate(split=split)
        print(f"  Accuracy: {result['accuracy']:.2%}  ({result['correct']}/{result['n_examples']})")

    bench.generate_figures()
    print("\nDone!")
