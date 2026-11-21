from dataclasses import dataclass

@dataclass(frozen=True)
class ProvenancePolicy:
    max_age_ms: int
    source_healthy: bool = False
    max_clock_uncertainty_ms: int = 1000

    def valid(self) -> bool:
        return (type(self.max_age_ms) is int and self.max_age_ms > 0
                and type(self.max_clock_uncertainty_ms) is int
                and self.max_clock_uncertainty_ms >= 0
                and type(self.source_healthy) is bool)
