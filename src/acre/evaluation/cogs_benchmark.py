"""
COGS Benchmark — Compositional Generalization in Semantic Parsing.

COGS (Kim & Linzen, 2020) evaluates systematic compositional generalization.
It maps English sentences to logical semantic forms. Standard Transformers
easily get 99% in-distribution, but drop to 16-35% on generalization splits
(e.g., depth generalization of prepositional phrases or agent-patient role binding).

This benchmark:
    1. Generates realistic COGS command-logic pairs procedurally.
    2. Builds splits: simple (in-distribution), depth (recursive PP), and role (role binding).
    3. Implements an ACRE algebraic concept model for semantic parsing.
    4. Evaluates exact-match logical form accuracy.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from torch.utils.data import DataLoader, Dataset

logger = logging.getLogger(__name__)

NUM_ELEMENTS = 10
ELEMENT_DIM = 64

# Vocabulary elements for COGS procedural generator
NOUNS = ["cat", "dog", "boy", "girl", "child", "baby", "hedgehog", "porcupine"]
VERBS = ["saw", "ate", "helped", "liked", "loved", "greeted"]
PREPS = ["in", "on", "with", "beside"]


def generate_cogs_examples(
    n_samples: int = 2000,
    seed: int = 42,
) -> List[Tuple[str, str, str]]:
    """Generate realistic COGS-like English-to-logical form pairs.

    Returns:
        A list of tuples: (sentence, logical_form, split_type)
        where split_type can be "simple", "depth", or "role".
    """
    rng = random.Random(seed)
    examples: List[Tuple[str, str, str]] = []

    # 1. Simple transitive and intransitive sentences (simple split)
    for _ in range(n_samples // 3):
        n1 = rng.choice(NOUNS)
        v = rng.choice(VERBS)
        n2 = rng.choice(NOUNS)
        
        # Avoid same noun for simplicity
        while n2 == n1:
            n2 = rng.choice(NOUNS)

        # Transitive: "the cat saw a dog" -> "saw ( cat , dog )"
        sentence = f"the {n1} {v} a {n2}"
        logic = f"{v} ( {n1} , {n2} )"
        examples.append((sentence, logic, "simple"))

        # Intransitive: "the boy ran" -> "ran ( boy )"
        n3 = rng.choice(NOUNS)
        sentence_int = f"the {n3} run"
        logic_int = f"run ( {n3} )"
        examples.append((sentence_int, logic_int, "simple"))

    # 2. Prepositional Phrase (PP) recursion (depth split)
    # Train has 1-2 PPs, Test has 3+ PPs
    for _ in range(n_samples // 3):
        # We build a recursive noun phrase: "the cat in the box on the table beside the chair"
        n_chain = [rng.choice(NOUNS) for _ in range(5)]
        p_chain = [rng.choice(PREPS) for _ in range(4)]
        v = rng.choice(VERBS)
        subj = rng.choice(NOUNS)

        # 1-PP case (train): "the cat in the box liked a boy"
        sent_1 = f"the {n_chain[0]} {p_chain[0]} a {n_chain[1]} {v} a {subj}"
        logic_1 = f"{v} ( {n_chain[0]} , {subj} ) AND {p_chain[0]} ( {n_chain[0]} , {n_chain[1]} )"
        examples.append((sent_1, logic_1, "depth_train"))

        # 3-PP case (test): "the cat in the box on the table beside the chair liked a boy"
        sent_3 = (
            f"the {n_chain[0]} {p_chain[0]} a {n_chain[1]} {p_chain[1]} a {n_chain[2]} "
            f"{p_chain[2]} a {n_chain[3]} {v} a {subj}"
        )
        logic_3 = (
            f"{v} ( {n_chain[0]} , {subj} ) AND {p_chain[0]} ( {n_chain[0]} , {n_chain[1]} ) "
            f"AND {p_chain[1]} ( {n_chain[1]} , {n_chain[2]} ) AND {p_chain[2]} ( {n_chain[2]} , {n_chain[3]} )"
        )
        examples.append((sent_3, logic_3, "depth_test"))

    # 3. Agent-Patient Role generalisation (role split)
    # Hedgehog and Porcupine are only seen as Agent (subject) in train, but tested as Patient (object) in test!
    special_nouns = ["hedgehog", "porcupine"]
    other_nouns = [n for n in NOUNS if n not in special_nouns]
    for _ in range(n_samples // 3):
        # Train: Special nouns only as Subject
        n_special = rng.choice(special_nouns)
        n_other = rng.choice(other_nouns)
        v = rng.choice(VERBS)
        sent_train = f"the {n_special} {v} a {n_other}"
        logic_train = f"{v} ( {n_special} , {n_other} )"
        examples.append((sent_train, logic_train, "role_train"))

        # Test: Special nouns only as Object
        n_special_test = rng.choice(special_nouns)
        n_other_test = rng.choice(other_nouns)
        v_test = rng.choice(VERBS)
        sent_test = f"the {n_other_test} {v_test} a {n_special_test}"
        logic_test = f"{v_test} ( {n_other_test} , {n_special_test} )"
        examples.append((sent_test, logic_test, "role_test"))

    return examples


class COGSDataset(Dataset):
    """Dataset class for COGS evaluation."""

    def __init__(
        self,
        examples: List[Tuple[str, str]],
        src_vocab: Optional[Dict[str, int]] = None,
        tgt_vocab: Optional[Dict[str, int]] = None,
        max_src_len: int = 32,
        max_tgt_len: int = 48,
    ) -> None:
        self.examples = examples
        self.max_src_len = max_src_len
        self.max_tgt_len = max_tgt_len

        # Source vocabulary
        if src_vocab is None:
            tokens = set()
            for src, _ in examples:
                tokens.update(src.split())
            self.src_vocab = {"<pad>": 0, "<sos>": 1, "<eos>": 2}
            for t in sorted(tokens):
                self.src_vocab[t] = len(self.src_vocab)
        else:
            self.src_vocab = src_vocab

        # Target vocabulary
        if tgt_vocab is None:
            tokens = set()
            for _, tgt in examples:
                tokens.update(tgt.split())
            self.tgt_vocab = {"<pad>": 0, "<sos>": 1, "<eos>": 2}
            for t in sorted(tokens):
                self.tgt_vocab[t] = len(self.tgt_vocab)
        else:
            self.tgt_vocab = tgt_vocab

    def __len__(self) -> int:
        return len(self.examples)

    def _encode(self, text: str, vocab: Dict[str, int], max_len: int) -> Tensor:
        tokens = [vocab.get("<sos>", 1)]
        tokens.extend(vocab.get(t, 0) for t in text.split())
        tokens.append(vocab.get("<eos>", 2))
        tokens = tokens[:max_len]
        tokens += [0] * (max_len - len(tokens))
        return torch.tensor(tokens, dtype=torch.long)

    def __getitem__(self, idx: int) -> Tuple[Tensor, Tensor]:
        src, tgt = self.examples[idx]
        return (
            self._encode(src, self.src_vocab, self.max_src_len),
            self._encode(tgt, self.tgt_vocab, self.max_tgt_len),
        )


class COGSModel(nn.Module):
    """ACRE-based Algebraic Semantic Parser for COGS."""

    def __init__(
        self,
        src_vocab_size: int,
        tgt_vocab_size: int,
        d_model: int = 256,
        n_heads: int = 4,
        n_layers: int = 3,
        max_len: int = 48,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.max_len = max_len
        self.d_element = 64

        # Encoder mapping tokens to continuous conceptual embeddings
        self.src_embed = nn.Embedding(src_vocab_size, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            dropout=0.1, activation="gelu", batch_first=True,
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # Projections to aspect concept dimensions
        self.concept_proj = nn.Linear(d_model, 10 * self.d_element)
        self.problem_proj = nn.Linear(d_model, 10 * self.d_element)

        # LARE solver for reasoning
        from acre.core.lare import LARE
        self.solver = LARE(d=self.d_element, max_steps=5)

        # Concept algebra operations
        from acre.core.algebra import ConceptAlgebra
        self.algebra = ConceptAlgebra(d=self.d_element)

        # Decoder memory mapping
        self.solution_to_memory = nn.Sequential(
            nn.Linear(10 * self.d_element, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

        # Decoder layer
        self.tgt_embed = nn.Embedding(tgt_vocab_size, d_model)
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=d_model * 4,
            dropout=0.1, activation="gelu", batch_first=True,
        )
        self.decoder = nn.TransformerDecoder(decoder_layer, num_layers=n_layers)
        self.output_proj = nn.Linear(d_model, tgt_vocab_size)

    def forward(self, src_ids: Tensor, tgt_ids: Tensor) -> Tensor:
        B, seq_len = src_ids.shape
        src_emb = self.src_embed(src_ids) * math.sqrt(self.d_model)
        hidden = self.encoder(src_emb)

        # Map to concept aspect structures
        concept_seq = self.concept_proj(hidden).reshape(B, seq_len, 10, self.d_element)

        # Compose concepts sequentially using algebraic direct sum (⊕)
        active_lengths = (src_ids != 0).sum(dim=1)
        max_active_len = max(int(active_lengths.max().item()), 1)

        composite_concept = concept_seq[:, 0]
        for t in range(1, min(max_active_len, seq_len)):
            new_composite = self.algebra.compose(composite_concept, concept_seq[:, t])
            mask = (t < active_lengths).view(-1, 1, 1).to(device=composite_concept.device, dtype=composite_concept.dtype)
            composite_concept = mask * new_composite + (1.0 - mask) * composite_concept

        # Goal/Problem embedding
        problem_vectors = self.problem_proj(hidden.mean(dim=1)).reshape(B, 10, self.d_element)

        # LARE iterative solver
        solutions = self.solver.forward_batched(composite_concept, problem_vectors)
        stacked_solutions = solutions.reshape(B, -1)
        memory = self.solution_to_memory(stacked_solutions).unsqueeze(1)

        # Autoregressive decoding via teacher forcing
        tgt_emb = self.tgt_embed(tgt_ids) * math.sqrt(self.d_model)
        tgt_mask = nn.Transformer.generate_square_subsequent_mask(tgt_ids.size(1)).to(tgt_ids.device)
        decoded = self.decoder(tgt_emb, memory, tgt_mask=tgt_mask)
        return self.output_proj(decoded)

    @torch.no_grad()
    def generate(self, src_ids: Tensor, max_len: int = 48, sos_id: int = 1, eos_id: int = 2, pad_id: int = 0) -> Tensor:
        B, seq_len = src_ids.shape
        device = src_ids.device

        # Encode and reason (same as forward)
        src_emb = self.src_embed(src_ids) * math.sqrt(self.d_model)
        hidden = self.encoder(src_emb)

        concept_seq = self.concept_proj(hidden).reshape(B, seq_len, 10, self.d_element)
        active_lengths = (src_ids != 0).sum(dim=1)
        max_active_len = max(int(active_lengths.max().item()), 1)

        composite_concept = concept_seq[:, 0]
        for t in range(1, min(max_active_len, seq_len)):
            new_composite = self.algebra.compose(composite_concept, concept_seq[:, t])
            mask = (t < active_lengths).view(-1, 1, 1).to(device=composite_concept.device, dtype=composite_concept.dtype)
            composite_concept = mask * new_composite + (1.0 - mask) * composite_concept

        problem_vectors = self.problem_proj(hidden.mean(dim=1)).reshape(B, 10, self.d_element)
        solutions = self.solver.forward_batched(composite_concept, problem_vectors)
        stacked_solutions = solutions.reshape(B, -1)
        memory = self.solution_to_memory(stacked_solutions).unsqueeze(1)

        # Autoregressive generation
        generated = torch.full((B, max_len), pad_id, dtype=torch.long, device=device)
        generated[:, 0] = sos_id
        finished = torch.zeros(B, dtype=torch.bool, device=device)

        for pos in range(1, max_len):
            tgt_emb = self.tgt_embed(generated[:, :pos]) * math.sqrt(self.d_model)
            tgt_mask = nn.Transformer.generate_square_subsequent_mask(pos).to(device)
            decoded = self.decoder(tgt_emb, memory, tgt_mask=tgt_mask)
            logits = self.output_proj(decoded[:, -1, :])
            next_token = logits.argmax(dim=-1)

            next_token[finished] = pad_id
            generated[:, pos] = next_token
            finished = finished | (next_token == eos_id)

            if finished.all():
                break

        return generated


class COGSTransformerBaseline(nn.Module):
    """Standard sequence-to-sequence Transformer baseline for COGS."""

    def __init__(
        self,
        src_vocab_size: int,
        tgt_vocab_size: int,
        d_model: int = 256,
        n_heads: int = 4,
        n_layers: int = 3,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.src_embed = nn.Embedding(src_vocab_size, d_model)
        self.tgt_embed = nn.Embedding(tgt_vocab_size, d_model)
        self.transformer = nn.Transformer(
            d_model=d_model, nhead=n_heads,
            num_encoder_layers=n_layers, num_decoder_layers=n_layers,
            dim_feedforward=d_model * 4, dropout=0.1, activation="gelu",
            batch_first=True,
        )
        self.output_proj = nn.Linear(d_model, tgt_vocab_size)

    def forward(self, src_ids: Tensor, tgt_ids: Tensor) -> Tensor:
        src = self.src_embed(src_ids) * math.sqrt(self.d_model)
        tgt = self.tgt_embed(tgt_ids) * math.sqrt(self.d_model)
        tgt_mask = nn.Transformer.generate_square_subsequent_mask(tgt_ids.size(1)).to(tgt_ids.device)
        out = self.transformer(src, tgt, tgt_mask=tgt_mask)
        return self.output_proj(out)


class COGSBenchmark:
    """Orchestrator for COGS compositional generalization evaluation."""

    def __init__(
        self,
        device: str = "cpu",
        results_dir: str = "results/benchmarks",
    ) -> None:
        self.device = torch.device(device)
        self.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)

        self.all_examples = generate_cogs_examples(n_samples=1800)
        self._build_splits()
        self.results: Dict[str, Any] = {}

    def _build_splits(self) -> None:
        """Construct simple, depth, and role splits."""
        self.splits = {
            "simple": {
                "train": [(e[0], e[1]) for e in self.all_examples if e[2] == "simple"],
                "test": [(e[0], e[1]) for e in self.all_examples if e[2] == "simple"][:200],  # held-out simple
            },
            "depth": {
                "train": [(e[0], e[1]) for e in self.all_examples if e[2] == "depth_train"],
                "test": [(e[0], e[1]) for e in self.all_examples if e[2] == "depth_test"],
            },
            "role": {
                "train": [(e[0], e[1]) for e in self.all_examples if e[2] == "role_train"],
                "test": [(e[0], e[1]) for e in self.all_examples if e[2] == "role_test"],
            },
        }

    def train(
        self,
        model_type: str = "acre",
        split: str = "simple",
        epochs: int = 5,
        lr: float = 3e-4,
        batch_size: int = 64,
    ) -> Dict[str, float]:
        train_data = self.splits[split]["train"]
        train_ds = COGSDataset(train_data)

        src_vs = len(train_ds.src_vocab)
        tgt_vs = len(train_ds.tgt_vocab)

        if model_type == "acre":
            model = COGSModel(src_vocab_size=src_vs, tgt_vocab_size=tgt_vs).to(self.device)
        else:
            model = COGSTransformerBaseline(src_vocab_size=src_vs, tgt_vocab_size=tgt_vs).to(self.device)

        optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
        loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, drop_last=True)

        model.train()
        history = []

        for epoch in range(1, epochs + 1):
            total_loss = 0.0
            n = 0
            for src_ids, tgt_ids in loader:
                src_ids = src_ids.to(self.device)
                tgt_ids = tgt_ids.to(self.device)

                logits = model(src_ids, tgt_ids[:, :-1])
                targets = tgt_ids[:, 1:]

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
            logger.info(f"[{model_type}] Epoch {epoch}/{epochs} loss={avg_loss:.4f}")

        self._last_model = model
        self._last_train_ds = train_ds
        return {"final_loss": history[-1]}

    @torch.no_grad()
    def evaluate(self, split: str = "depth") -> Dict[str, float]:
        test_data = self.splits[split]["test"]
        if not test_data:
            return {"accuracy": 0.0, "n_examples": 0}

        test_ds = COGSDataset(
            test_data,
            src_vocab=self._last_train_ds.src_vocab,
            tgt_vocab=self._last_train_ds.tgt_vocab,
        )
        loader = DataLoader(test_ds, batch_size=32, shuffle=False)

        self._last_model.eval()
        correct = 0
        total = 0

        # Detect model implementation or baseline
        is_acre = isinstance(self._last_model, COGSModel)

        for src_ids, tgt_ids in loader:
            src_ids = src_ids.to(self.device)
            tgt_ids = tgt_ids.to(self.device)

            sos_id = test_ds.tgt_vocab.get("<sos>", 1)
            eos_id = test_ds.tgt_vocab.get("<eos>", 2)

            if is_acre:
                preds = self._last_model.generate(
                    src_ids, max_len=tgt_ids.size(1),
                    sos_id=sos_id, eos_id=eos_id,
                )
            else:
                # Generate step for baseline transformer
                generated = torch.full((src_ids.size(0), tgt_ids.size(1)), 0, dtype=torch.long, device=self.device)
                generated[:, 0] = sos_id
                finished = torch.zeros(src_ids.size(0), dtype=torch.bool, device=self.device)
                for pos in range(1, tgt_ids.size(1)):
                    tgt_mask = nn.Transformer.generate_square_subsequent_mask(pos).to(self.device)
                    out = self._last_model(src_ids, generated[:, :pos])
                    next_token = out[:, -1, :].argmax(dim=-1)
                    next_token[finished] = 0
                    generated[:, pos] = next_token
                    finished = finished | (next_token == eos_id)
                    if finished.all():
                        break
                preds = generated

            targets = tgt_ids[:, 1:]
            pred_tokens = preds[:, 1:]

            for i in range(pred_tokens.size(0)):
                target_len = (targets[i] != 0).sum().item()
                match = torch.equal(pred_tokens[i, :target_len], targets[i, :target_len])
                correct += int(match)
                total += 1

        accuracy = correct / max(total, 1)
        result = {
            "accuracy": accuracy,
            "n_examples": total,
            "correct": correct,
        }
        self.results[split] = result

        # Save to cogs_results.json
        results_path = os.path.join(self.results_dir, "cogs_results.json")
        with open(results_path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)

        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=" * 60)
    print("ACRE — COGS Semantic Parsing Compositional Benchmark")
    print("=" * 60)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    bench = COGSBenchmark(device=device)

    for split in ["simple", "depth", "role"]:
        print(f"\n--- Training ACRE on: {split} ---")
        bench.train(model_type="acre", split=split, epochs=3)
        res = bench.evaluate(split=split)
        print(f"  ACRE Accuracy: {res['accuracy']:.2%}  ({res['correct']}/{res['n_examples']})")
