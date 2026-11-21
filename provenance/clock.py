"""Offline trusted-clock contract. No system-clock auto-trust."""
from dataclasses import dataclass
from datetime import datetime,timedelta
from typing import Protocol

@dataclass(frozen=True)
class ClockSample:
    utc: datetime
    uncertainty_us: int
    sampled_monotonic_ns: int
    valid_until_monotonic_ns: int
    source_id: str

class TrustedClockProvider(Protocol):
    def sample(self)->ClockSample | None: ...

def check_clock(sample, current_monotonic_ns, approved_source, max_uncertainty_us):
    if (type(sample) is not ClockSample or type(current_monotonic_ns) is not int
            or type(max_uncertainty_us) is not int or max_uncertainty_us < 0
            or type(approved_source) is not str or not approved_source):
        return None
    if (not isinstance(sample.utc,datetime) or sample.utc.tzinfo is None
            or sample.utc.utcoffset()!=timedelta(0)
            or type(sample.uncertainty_us) is not int or sample.uncertainty_us<0
            or type(sample.sampled_monotonic_ns) is not int
            or type(sample.valid_until_monotonic_ns) is not int
            or sample.sampled_monotonic_ns<0
            or not sample.sampled_monotonic_ns<=current_monotonic_ns<=sample.valid_until_monotonic_ns
            or sample.source_id!=approved_source):
        return None
    elapsed_ns=current_monotonic_ns-sample.sampled_monotonic_ns
    # Conservative worst-case clock drift of 1000 ppm for offline contract.
    drift_us=(elapsed_ns+999999)//1000000
    uncertainty=sample.uncertainty_us+drift_us
    if uncertainty>max_uncertainty_us:
        return None
    return sample.utc+timedelta(microseconds=elapsed_ns//1000),uncertainty
