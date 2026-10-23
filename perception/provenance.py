"""Where a distance comes from. Every value this package returns carries one, and the kinds are
never mixed: a radar measurement is not a vision estimate, a fused value is neither, and test data
is never reported as a measurement."""
from enum import StrEnum


class Provenance(StrEnum):
    OEM_RADAR = 'oem_radar'        # measured by the car's own radar, as the car port logged it in carState
    VISION = 'vision'              # estimated by the vision model
    FUSED = 'fused'                # radar distance combined with vision (lateral position, class)
    GROUND_TRUTH = 'ground_truth'  # reference values written for a test
    SYNTHETIC = 'synthetic'        # made-up values for tests and demos

    @property
    def measured(self) -> bool:
        """True only for a real measurement of a real drive: the only labels whose errors are logged."""
        return self is Provenance.OEM_RADAR
