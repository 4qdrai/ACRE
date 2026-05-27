"""
Continuous-time Flow Matching Decoder (ELF-style)
=================================================

This module implements the FlowMatchingDecoder for the ACRE (Algebraic Concept
Reasoning Engine) repository. It decodes a SolutionTensor (the algebraic result
from LARE) back to target token sequences (e.g., text, code) using continuous-time
normalizing flows in embedding space.

Key features:
1. **Late Discretization**: Generates paths entirely within the continuous embedding
   space, mapping back to discrete tokens only at the final step (t=1).
2. **Velocity vs. Target Parameterization**: Supports standard velocity prediction
   or direct target projection (which stabilizes trajectory learning).
3. **Euler ODE Integration**: Generates output sequences using numerical integration.
"""

from __future__ import annotations

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple

from acre.core.concept_tensor import NUM_CONCEPT_ELEMENTS, DEFAULT_EMBEDDING_DIM
from acre.core.solution_tensor import SolutionTensor


class SinusoidalTimeEmbedding(nn.Module):
    """Sinusoidal time embedding module to map scalar t to d_model space."""

    def __init__(self, dim: int) -> None:
        super().__init__()
        self.dim = dim

    def forward(self, t: torch.Tensor) -> torch.Tensor:
        """Embed scalar t in [0, 1].

        Parameters
        ----------
        t : torch.Tensor
            Time steps of shape (B,) or (B, 1).

        Returns
        -------
        torch.Tensor
            Embeddings of shape (B, dim).
        """
        # Ensure t is 1D (B,)
        if t.dim() > 1:
            t = t.squeeze(-1)

        half_dim = self.dim // 2
        # Scaling factor for frequencies
        freqs = torch.exp(
            torch.arange(half_dim, device=t.device, dtype=torch.float32)
            * -(math.log(10000.0) / (half_dim - 1))
        )
        args = t.unsqueeze(1) * freqs.unsqueeze(0)  # (B, half_dim)
        embeddings = torch.cat([torch.sin(args), torch.cos(args)], dim=-1)

        # Pad with zero if dim is odd
        if self.dim % 2 == 1:
            padding = torch.zeros(t.shape[0], 1, device=t.device, dtype=t.dtype)
            embeddings = torch.cat([embeddings, padding], dim=-1)

        return embeddings


class FlowMatchingDecoder(nn.Module):
    """Continuous-time Flow Matching Decoder (ELF-style).

    Parameters
    ----------
    d_solution : int
        Dimension of the solution tensor (10 * d by default).
    d_model : int
        Decoder hidden dimension (default 256).
    vocab_size : int
        Output vocabulary size (default 32000).
    num_layers : int
        Number of transformer decoder layers (default 4).
    num_heads : int
        Number of attention heads (default 4).
    dim_feedforward : int
        Feedforward dimension (default 512).
    max_output_len : int
        Maximum output sequence length (default 512).
    parameterization : str
        Flow target parameterization: "velocity" or "target".
    dropout : float
        Dropout rate (default 0.1).
    """

    def __init__(
        self,
        d_solution: int = NUM_CONCEPT_ELEMENTS * DEFAULT_EMBEDDING_DIM,
        d_model: int = 256,
        vocab_size: int = 32000,
        num_layers: int = 4,
        num_heads: int = 4,
        dim_feedforward: int = 512,
        max_output_len: int = 512,
        parameterization: str = "target",
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.d_solution = d_solution
        self.d_model = d_model
        self.vocab_size = vocab_size
        self.max_output_len = max_output_len
        self.parameterization = parameterization.lower()

        if self.parameterization not in {"velocity", "target"}:
            raise ValueError(
                f"Unknown parameterization: {parameterization}. Expected 'velocity' or 'target'."
            )

        # Shared Token Embedding
        self.token_embedding = nn.Embedding(vocab_size, d_model)

        # Solution Projection
        self.solution_proj = nn.Sequential(
            nn.Linear(d_solution, d_model),
            nn.GELU(),
            nn.LayerNorm(d_model),
        )

        # Time and Position Embeddings
        self.time_embed = SinusoidalTimeEmbedding(d_model)
        self.pos_embedding = nn.Embedding(max_output_len, d_model)

        # Transformer Decoder representing the Vector Field
        decoder_layer = nn.TransformerDecoderLayer(
            d_model=d_model,
            nhead=num_heads,
            dim_feedforward=dim_feedforward,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.vector_field_transformer = nn.TransformerDecoder(
            decoder_layer, num_layers=num_layers
        )

        # Output Prediction Head (velocity or target)
        self.flow_head = nn.Linear(d_model, d_model)

        # Length Predictor from Solution
        self.length_predictor = nn.Sequential(
            nn.Linear(d_solution, d_model),
            nn.GELU(),
            nn.Linear(d_model, max_output_len),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        """Xavier uniform initialization for linear layers."""
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)
            elif isinstance(module, nn.Embedding):
                nn.init.normal_(module.weight, mean=0.0, std=0.02)

    def _get_solution_representation(self, solution: SolutionTensor) -> torch.Tensor:
        """Extract and shape/pad the solution tensor representation.

        Supports both batched and unbatched input.

        Returns
        -------
        torch.Tensor
            Batched flat solution representation, shape (B, d_solution).
        """
        res = solution.result_tensor  # (10, d) or (B, 10, d)
        if res.dim() == 2:
            # Unbatched: add batch dim
            flat = res.reshape(1, -1)
        else:
            flat = res.reshape(res.shape[0], -1)

        # Pad or truncate to self.d_solution
        if flat.shape[-1] < self.d_solution:
            padding = torch.zeros(
                flat.shape[0],
                self.d_solution - flat.shape[-1],
                device=flat.device,
                dtype=flat.dtype,
            )
            flat = torch.cat([flat, padding], dim=-1)
        elif flat.shape[-1] > self.d_solution:
            flat = flat[:, : self.d_solution]

        return flat

    def _predict_length(self, solution_flat: torch.Tensor) -> torch.Tensor:
        """Predict the output sequence length for a batch.

        Parameters
        ----------
        solution_flat : torch.Tensor
            Flattened solution representation, shape (B, d_solution).

        Returns
        -------
        torch.Tensor
            Long tensor of shape (B,) with predicted lengths in [1, max_output_len].
        """
        logits = self.length_predictor(solution_flat)  # (B, max_output_len)
        predicted_lens = logits.argmax(dim=-1) + 1  # 1-indexed length
        return torch.clamp(predicted_lens, 1, self.max_output_len)

    def forward(
        self,
        x_t: torch.Tensor,
        t: torch.Tensor,
        solution_flat: torch.Tensor,
    ) -> torch.Tensor:
        """Evaluate the flow field vector field prediction.

        Parameters
        ----------
        x_t : torch.Tensor
            Current position in embedding space, shape (B, L, d_model).
        t : torch.Tensor
            Continuous time steps, shape (B,) or scalar.
        solution_flat : torch.Tensor
            Flattened solution embedding, shape (B, d_solution).

        Returns
        -------
        torch.Tensor
            Predicted output vector field (velocity or target), shape (B, L, d_model).
        """
        B, L, _ = x_t.shape

        # Format time steps to (B,)
        if t.dim() == 0 or (t.dim() == 1 and t.shape[0] == 1):
            t = torch.full((B,), t.item(), device=x_t.device, dtype=x_t.dtype)

        # Embed time and repeat across sequence length
        t_emb = self.time_embed(t).unsqueeze(1)  # (B, 1, d_model)

        # Embed position
        positions = torch.arange(L, device=x_t.device).unsqueeze(0).expand(B, -1)
        pos_emb = self.pos_embedding(positions)  # (B, L, d_model)

        # Combine inputs to transformer decoder target
        # tgt = x_t + time_emb + pos_emb
        tgt = x_t + t_emb + pos_emb

        # Project solution to decoder space to act as cross-attention memory
        memory = self.solution_proj(solution_flat).unsqueeze(1)  # (B, 1, d_model)

        # Refine representation via cross-attention
        refined = self.vector_field_transformer(tgt, memory)  # (B, L, d_model)

        # Project to flow field output
        output = self.flow_head(refined)  # (B, L, d_model)
        return output

    def compute_loss(
        self,
        solution: SolutionTensor,
        targets: torch.Tensor,
    ) -> torch.Tensor:
        """Compute the continuous flow matching loss (MSE).

        Parameters
        ----------
        solution : SolutionTensor
            The conditioning solution.
        targets : torch.Tensor
            Target sequence tokens, shape (B, L) or (L,).

        Returns
        -------
        torch.Tensor
            Scalar loss.
        """
        # Ensure targets is batched (B, L)
        if targets.dim() == 1:
            targets = targets.unsqueeze(0)

        B, L = targets.shape

        # Fetch solution flat representation
        solution_flat = self._get_solution_representation(solution)

        # Length prediction loss
        target_lengths = torch.full((B,), L, dtype=torch.long, device=targets.device)
        logits_len = self.length_predictor(solution_flat)  # (B, max_output_len)
        target_indices = torch.clamp(target_lengths - 1, 0, self.max_output_len - 1)
        loss_len = F.cross_entropy(logits_len, target_indices)

        # Map targets to target embeddings x_1
        x_1 = self.token_embedding(targets)  # (B, L, d_model)

        # Sample random noise x_0 ~ N(0, I)
        x_0 = torch.randn_like(x_1)

        # Sample random continuous time t ~ Uniform(0, 1)
        t = torch.rand(B, device=targets.device)  # (B,)

        # Linear interpolation path x_t = (1 - t)*x_0 + t*x_1
        t_broadcast = t.view(B, 1, 1)
        x_t = (1.0 - t_broadcast) * x_0 + t_broadcast * x_1

        # Predict vector field
        pred = self.forward(x_t, t, solution_flat)

        # Compute loss depending on parameterization
        if self.parameterization == "velocity":
            # Target is the straight line velocity field: u_t = x_1 - x_0
            u_t = x_1 - x_0
            loss_reconstruct = F.mse_loss(pred, u_t)
        else:
            # Target is directly the end state: x_1
            loss_reconstruct = F.mse_loss(pred, x_1)

        # Combine reconstruction loss and length prediction loss
        return loss_reconstruct + 0.1 * loss_len

    @torch.no_grad()
    def decode(
        self,
        solution: SolutionTensor,
        max_length: Optional[int] = None,
        num_steps: int = 10,
    ) -> torch.Tensor:
        """Decode a SolutionTensor to output token logits via Euler ODE integration.

        Parameters
        ----------
        solution : SolutionTensor
            The solution to decode.
        max_length : int, optional
            Output sequence length. If None, predicted from solution.
        num_steps : int
            Number of Euler integration steps (default 10).

        Returns
        -------
        torch.Tensor
            Vocabulary logits, shape (L, vocab_size) or (B, L, vocab_size).
        """
        # Get batch first solution details
        solution_flat = self._get_solution_representation(solution)
        B = solution_flat.shape[0]

        # Determine length
        if max_length is None:
            lengths = self._predict_length(solution_flat)
            # Use max predicted length for padding convenience
            L = int(lengths.max().item())
        else:
            L = min(max_length, self.max_output_len)

        # Start integration at random noise x_0 ~ N(0, I)
        x = torch.randn(B, L, self.d_model, device=solution_flat.device)

        dt = 1.0 / num_steps

        # Euler Integration
        for step in range(num_steps):
            t_val = step * dt
            t = torch.full((B,), t_val, device=solution_flat.device)

            pred = self.forward(x, t, solution_flat)

            if self.parameterization == "velocity":
                # dx/dt = velocity vector
                dx = pred
            else:
                # Direct prediction parameterization:
                # velocity vector u_t = (x_1 - x_t) / (1 - t)
                # We clamp denominator to avoid division by zero near t=1
                denom = max(1.0 - t_val, 1e-4)
                dx = (pred - x) / denom

            x = x + dx * dt

        # Discretize: calculate cosine similarity / dot product against vocabulary embeddings
        # shape: (B, L, vocab_size)
        logits = torch.matmul(x, self.token_embedding.weight.T)

        # Unbatch if input was unbatch first
        res = solution.result_tensor
        if res.dim() == 2:
            logits = logits.squeeze(0)  # (L, vocab_size)

        return logits

    def decode_to_tokens(
        self,
        solution: SolutionTensor,
        max_length: Optional[int] = None,
        num_steps: int = 10,
        temperature: float = 1.0,
    ) -> torch.Tensor:
        """Decode to discrete token indices.

        Parameters
        ----------
        solution : SolutionTensor
        max_length : int, optional
        num_steps : int
        temperature : float
            Sampling temperature. 0.0 = argmax, > 0 = sampling.

        Returns
        -------
        torch.Tensor
            Token indices, shape (L,) or (B, L) with dtype torch.long.
        """
        logits = self.decode(solution, max_length, num_steps)

        if temperature <= 0.0:
            return logits.argmax(dim=-1)
        else:
            probs = F.softmax(logits / temperature, dim=-1)
            # Reshape for multinominal if batched
            if logits.dim() == 3:
                B, L, V = logits.shape
                flat_probs = probs.reshape(-1, V)
                tokens = torch.multinomial(flat_probs, num_samples=1).squeeze(-1)
                return tokens.reshape(B, L)
            else:
                return torch.multinomial(probs, num_samples=1).squeeze(-1)
