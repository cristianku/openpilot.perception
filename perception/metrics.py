"""Distance errors, kept apart by where the label comes from.

Every eligible label counts (an associated pair with a finite, positive label): a missing or non-finite
prediction is a failure, it is never dropped, so a model cannot look better by giving up on the hard cases.
The shares within 1/2/5 m use all the eligible labels as denominator; MAE, MedAE, RMSE and AbsRel are over
the predicted ones, always reported with `eligible`, `predicted` and `missing`.

The headline numbers (`measured()`, `scalars()`) use genuine radar measurements only; synthetic and test
values are reported under their own name and never mixed in.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from perception.provenance import Provenance

# Disjoint distance bands of the label: [2, 10), [10, 30), [30, 60), [60, 80]; outside them, out of range.
BANDS = (('2-10 m', 2.0, 10.0), ('10-30 m', 10.0, 30.0), ('30-60 m', 30.0, 60.0), ('60-80 m', 60.0, 80.0))
WITHIN_M = (1.0, 2.0, 5.0)


def band_of(label_m: float) -> str:
    for name, low, high in BANDS:
        if low <= label_m < high or (high == BANDS[-1][2] and label_m == high):
            return name
    return 'out of range'


def summarize(labels: list[float], estimates: list[float | None]) -> dict[str, float | int | None]:
    """The errors of one group of eligible labels; an estimate of None is a missing prediction."""
    predicted = [(label, estimate) for label, estimate in zip(labels, estimates, strict=True) if estimate is not None]
    result: dict[str, float | int | None] = {'eligible': len(labels), 'predicted': len(predicted),
                                             'missing': len(labels) - len(predicted)}
    errors = np.asarray([abs(estimate - label) for label, estimate in predicted])
    relative = np.asarray([abs(estimate - label) / label for label, estimate in predicted])
    result['mae_m'] = float(errors.mean()) if errors.size else None
    result['medae_m'] = float(np.median(errors)) if errors.size else None
    result['rmse_m'] = float(np.sqrt((errors ** 2).mean())) if errors.size else None
    result['absrel'] = float(relative.mean()) if relative.size else None
    for limit in WITHIN_M:
        hits = int((errors <= limit).sum()) if errors.size else 0
        result[f'within_{limit:g}m'] = hits / len(labels) if labels else None
    return result


class DistanceErrors:
    def __init__(self):
        self._labels: dict[Provenance, list[float]] = defaultdict(list)
        self._estimates: dict[Provenance, list[float | None]] = defaultdict(list)

    def add(self, estimated_m: float | None, label_m: float, provenance: Provenance, associated: bool) -> bool:
        """Record one eligible label and the model's estimate for it (None or not finite = missing).
        False, nothing recorded, when the pair is not associated or the label is not a finite distance."""
        if not associated or not math.isfinite(label_m) or label_m <= 0:
            return False
        estimate = estimated_m if estimated_m is not None and math.isfinite(estimated_m) else None
        self._labels[provenance].append(label_m)
        self._estimates[provenance].append(estimate)
        return True

    def summary(self) -> dict[str, dict]:
        """By provenance: the overall errors, then the same by distance band."""
        result = {}
        for provenance, labels in self._labels.items():
            estimates = self._estimates[provenance]
            overall = summarize(labels, estimates)
            bands = {}
            for name in [band[0] for band in BANDS] + ['out of range']:
                chosen = [i for i, label in enumerate(labels) if band_of(label) == name]
                if chosen:
                    bands[name] = summarize([labels[i] for i in chosen], [estimates[i] for i in chosen])
            overall['bands'] = bands
            result[str(provenance)] = overall
        return result

    def measured(self) -> dict | None:
        """Errors against genuine radar measurements only; None without any."""
        return self.summary().get(str(Provenance.OEM_RADAR))

    def scalars(self, prefix: str = 'distance') -> dict[str, float]:
        """Flat values for a training log (W&B): genuine measurements only, overall and by band."""
        measured = self.measured()
        if not measured:
            return {}
        flat = {f'{prefix}/{name}': value for name, value in measured.items() if name != 'bands' and value is not None}
        for band, values in measured['bands'].items():
            for name, value in values.items():
                if value is not None:
                    flat[f'{prefix}/{band}/{name}'] = value
        return flat
