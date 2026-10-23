"""Depth loss with a validity mask: only the targets marked valid count, the others give no gradient."""
from __future__ import annotations

import torch
import torch.nn.functional as F


def masked_depth_loss(predicted_m: torch.Tensor, target_m: torch.Tensor, valid: torch.Tensor,
                      kind: str = 'log_l1', eps: float = 1e-3) -> torch.Tensor:
    """Mean loss over the valid targets; 0 (with a gradient path) when there is none.

    kind: 'log_l1' (|log p - log t|, the same weight to 10% at 5 m and at 80 m), 'l1' (metres) or
    'smooth_l1'. Targets that are not finite or not positive are never used, whatever `valid` says.
    """
    valid = valid.bool() & torch.isfinite(target_m) & (target_m > 0)
    if not bool(valid.any()):
        return torch.nan_to_num(predicted_m).sum() * 0.0
    predicted, target = predicted_m[valid], target_m[valid]
    if kind == 'log_l1':
        return (torch.log(predicted.clamp_min(eps)) - torch.log(target)).abs().mean()
    if kind == 'l1':
        return (predicted - target).abs().mean()
    if kind == 'smooth_l1':
        return F.smooth_l1_loss(predicted, target)
    raise ValueError(f'unknown depth loss {kind!r}: log_l1, l1 or smooth_l1')
