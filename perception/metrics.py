"""Distance errors, kept apart by where the label comes from.

Only pairs a caller marks as associated are recorded. The headline numbers (`measured()`, `scalars()`)
use genuine radar measurements only; synthetic and test values are reported under their own name and
never mixed in.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from perception.provenance import Provenance


class DistanceErrors:
    def __init__(self):
        self._absolute: dict[Provenance, list[float]] = defaultdict(list)
        self._relative: dict[Provenance, list[float]] = defaultdict(list)

    def add(self, estimated_m: float, label_m: float, provenance: Provenance, associated: bool) -> bool:
        """Record one pair; False (nothing recorded) when not associated or not finite."""
        if not associated or not (math.isfinite(estimated_m) and math.isfinite(label_m)) or label_m <= 0:
            return False
        error = abs(estimated_m - label_m)
        self._absolute[provenance].append(error)
        self._relative[provenance].append(error / label_m)
        return True

    def summary(self) -> dict[str, dict[str, float]]:
        result = {}
        for provenance, errors in self._absolute.items():
            absolute, relative = np.asarray(errors), np.asarray(self._relative[provenance])
            result[str(provenance)] = {
                'count': len(errors),
                'abs_mean_m': float(absolute.mean()),
                'abs_median_m': float(np.median(absolute)),
                'rel_mean': float(relative.mean()),
                'rel_median': float(np.median(relative)),
            }
        return result

    def measured(self) -> dict[str, float] | None:
        """Errors against genuine radar measurements only; None without any."""
        return self.summary().get(str(Provenance.OEM_RADAR))

    def scalars(self, prefix: str = 'distance') -> dict[str, float]:
        """Flat values for a training log (W&B): genuine measurements only."""
        measured = self.measured()
        return {f'{prefix}/{name}': value for name, value in measured.items()} if measured else {}
