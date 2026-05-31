"""
Unit tests for the COGS semantic parsing compositional benchmark.
"""

import pytest
import torch
from acre.evaluation.cogs_benchmark import (
    generate_cogs_examples,
    COGSDataset,
    COGSModel,
    COGSTransformerBaseline,
    COGSBenchmark,
)


def test_generate_cogs_examples():
    """Verify that COGS procedural generator produces expected splits and shapes."""
    examples = generate_cogs_examples(n_samples=60, seed=42)
    assert len(examples) > 0
    for sentence, logic, split_type in examples:
        assert isinstance(sentence, str) and len(sentence) > 0
        assert isinstance(logic, str) and len(logic) > 0
        assert split_type in ["simple", "depth_train", "depth_test", "role_train", "role_test"]


def test_cogs_dataset():
    """Verify that COGSDataset properly encodes and pads command/logic sentences."""
    examples = [
        ("the cat saw a dog", "saw ( cat , dog )"),
        ("the boy run", "run ( boy )"),
    ]
    ds = COGSDataset(examples, max_src_len=10, max_tgt_len=15)
    
    assert len(ds) == 2
    assert ds.src_vocab["cat"] > 0
    assert ds.tgt_vocab["saw"] > 0

    src_ids, tgt_ids = ds[0]
    assert src_ids.shape == (10,)
    assert tgt_ids.shape == (15,)
    assert src_ids[0] == 1  # SOS
    assert tgt_ids[0] == 1  # SOS


def test_cogs_model_forward():
    """Verify that COGSModel forward pass works and computes correct output shapes."""
    src_vocab_size = 20
    tgt_vocab_size = 15
    model = COGSModel(src_vocab_size=src_vocab_size, tgt_vocab_size=tgt_vocab_size, d_model=64)
    
    # Batch size 4, max_src_len 10, max_tgt_len 12
    src_ids = torch.randint(1, src_vocab_size, (4, 10))
    tgt_ids = torch.randint(1, tgt_vocab_size, (4, 12))
    
    # Pad index is 0
    src_ids[:, -2:] = 0
    
    logits = model(src_ids, tgt_ids)
    assert logits.shape == (4, 12, tgt_vocab_size)
    assert torch.isfinite(logits).all()


def test_cogs_model_generate():
    """Verify that COGSModel generate pass executes autoregressive prediction."""
    src_vocab_size = 15
    tgt_vocab_size = 10
    model = COGSModel(src_vocab_size=src_vocab_size, tgt_vocab_size=tgt_vocab_size, d_model=64)
    
    src_ids = torch.randint(1, src_vocab_size, (2, 8))
    preds = model.generate(src_ids, max_len=15, sos_id=1, eos_id=2, pad_id=0)
    
    assert preds.shape == (2, 15)
    assert (preds[:, 0] == 1).all()  # SOS
    assert torch.isfinite(preds.float()).all()


def test_cogs_benchmark_splits():
    """Verify that COGSBenchmark builds all required evaluation splits."""
    bench = COGSBenchmark(device="cpu")
    assert "simple" in bench.splits
    assert "depth" in bench.splits
    assert "role" in bench.splits

    for name, split in bench.splits.items():
        assert "train" in split
        assert "test" in split
        assert len(split["train"]) > 0
        assert len(split["test"]) > 0
