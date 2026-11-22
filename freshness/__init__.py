"""Research-only ETH mark freshness diagnostics. No network, trading or production gate."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional

class TransportState(str, Enum):
    UNKNOWN="UNKNOWN"
    FRESH="FRESH"
    DEGRADED="DEGRADED"
    STALE="STALE"
    DISCONNECTED="DISCONNECTED"
    INVALID="INVALID"

class ExchangeState(str, Enum):
    UNVERIFIED="UNVERIFIED"
    VERIFIED_FRESH="VERIFIED_FRESH"
    VERIFIED_STALE="VERIFIED_STALE"
    INVALID_EVIDENCE="INVALID_EVIDENCE"

class Consistency(str, Enum):
    UNKNOWN="UNKNOWN"
    CONSISTENT="CONSISTENT"
    MISMATCH="MISMATCH"
    NOT_COMPARABLE="NOT_COMPARABLE"
    INVALID_INPUT="INVALID_INPUT"
    DISCONNECTED="DISCONNECTED"
    STALE="STALE"

class ReplayState(str, Enum):
    NOT_CHECKED="NOT_CHECKED"
    DUPLICATE="DUPLICATE"
    UNAVAILABLE="UNAVAILABLE"

@dataclass(frozen=True)
class Policy:
    fresh_ms:int=2000
    stale_ms:int=5000
    exchange_max_age_ms:int=5000
    clock_max_uncertainty_ms:int=1000
    comparison_window_ms:int=2000
    tolerance_bps:int=5

    def valid(self):
        return all(type(v) is int for v in vars(self).values()) and (
            0 < self.fresh_ms < self.stale_ms and self.exchange_max_age_ms>0
            and self.clock_max_uncertainty_ms>=0 and self.comparison_window_ms>=0
            and self.tolerance_bps>=0)

@dataclass(frozen=True)
class Observation:
    observation_id:str
    market_id:str
    price_type:str
    source_channel:str
    mark_px:Decimal
    received_monotonic_ns:int
    clock_domain_id:str
    connection_epoch:Optional[str]=None
    request_start_monotonic_ns:Optional[int]=None
    request_end_monotonic_ns:Optional[int]=None
    exchange_timestamp:Optional[datetime]=None
    event_id:Optional[str]=None

@dataclass(frozen=True)
class FreshnessAssessment:
    transport_state:TransportState
    exchange_mark_state:ExchangeState
    rest_ws_consistency:Consistency=Consistency.UNKNOWN
    replay_state:ReplayState=ReplayState.NOT_CHECKED
    transport_age_ms:Optional[int]=None
    exchange_timestamp:Optional[datetime]=None
    exchange_provenance_verified:bool=False
    recommendation_eligible:bool=False
    reason_codes:tuple=()

@dataclass(frozen=True)
class WsSession:
    epoch:str
    connected:bool
    clock_domain_id:str

def _valid_observation(o):
    if type(o) is not Observation:
        return False
    if (o.market_id,o.price_type,o.source_channel)!=('ETH-PERP','mark',o.source_channel):
        return False
    if o.source_channel not in ('REST','WS') or not isinstance(o.observation_id,str) or not o.observation_id:
        return False
    if type(o.mark_px) is not Decimal or not o.mark_px.is_finite() or o.mark_px<=0:
        return False
    if type(o.received_monotonic_ns) is not int or o.received_monotonic_ns<0:
        return False
    if type(o.clock_domain_id) is not str or not o.clock_domain_id:
        return False
    if o.source_channel=='WS':
        if type(o.connection_epoch) is not str or not o.connection_epoch:
            return False
        if o.request_start_monotonic_ns is not None or o.request_end_monotonic_ns is not None:
            return False
    else:
        if o.connection_epoch is not None:
            return False
        if (type(o.request_start_monotonic_ns) is not int
                or type(o.request_end_monotonic_ns) is not int
                or not 0<=o.request_start_monotonic_ns<=o.request_end_monotonic_ns<=o.received_monotonic_ns):
            return False
    if o.exchange_timestamp is not None:
        if (type(o.exchange_timestamp) is not datetime or o.exchange_timestamp.tzinfo is None
                or o.exchange_timestamp.utcoffset()!=timedelta(0)):
            return False
    if o.event_id is not None and (type(o.event_id) is not str or not o.event_id):
        return False
    return True

def assess_transport(observation, now_monotonic_ns, clock_domain_id, policy=Policy(), session=None):
    """Local receipt age only. Does not prove exchange price freshness."""
    reasons=[]
    if type(policy) is not Policy or not policy.valid() or not _valid_observation(observation):
        return FreshnessAssessment(TransportState.INVALID,ExchangeState.UNVERIFIED,reason_codes=('invalid_input',))
    if (type(now_monotonic_ns) is not int or now_monotonic_ns<observation.received_monotonic_ns
            or type(clock_domain_id) is not str or clock_domain_id!=observation.clock_domain_id):
        return FreshnessAssessment(TransportState.INVALID,ExchangeState.UNVERIFIED,reason_codes=('invalid_clock_domain_or_age',))
    if observation.source_channel=='WS':
        if (type(session) is not WsSession or session.clock_domain_id!=clock_domain_id
                or type(session.epoch) is not str or not session.epoch
                or type(session.connected) is not bool):
            return FreshnessAssessment(TransportState.INVALID,ExchangeState.UNVERIFIED,reason_codes=('invalid_ws_session',))
        if not session.connected:
            return FreshnessAssessment(TransportState.DISCONNECTED,ExchangeState.UNVERIFIED,reason_codes=('ws_disconnected',))
        if session.epoch!=observation.connection_epoch:
            return FreshnessAssessment(TransportState.INVALID,ExchangeState.UNVERIFIED,reason_codes=('old_ws_epoch',))
    age_ns=now_monotonic_ns-observation.received_monotonic_ns
    age_ms=age_ns//1000000  # display only; thresholds compare exact nanoseconds
    state=(TransportState.FRESH if age_ns<=policy.fresh_ms*1000000 else
           TransportState.DEGRADED if age_ns<=policy.stale_ms*1000000 else TransportState.STALE)
    if state!=TransportState.FRESH: reasons.append('transport_'+state.value.lower())
    reasons.append('missing_exchange_provenance')
    return FreshnessAssessment(state,ExchangeState.UNVERIFIED,transport_age_ms=age_ms,
                               reason_codes=tuple(reasons))

def compare_rest_ws(rest,ws,policy=Policy(),ws_session=None):
    """Diagnostic comparison only. Agreement is not evidence of exchange timestamp."""
    if (type(policy) is not Policy or not policy.valid() or not _valid_observation(rest)
            or not _valid_observation(ws) or rest.source_channel!='REST'
            or ws.source_channel!='WS' or rest.clock_domain_id!=ws.clock_domain_id):
        return Consistency.INVALID_INPUT
    if (type(ws_session) is not WsSession or type(ws_session.connected) is not bool
            or not ws_session.connected or ws_session.epoch!=ws.connection_epoch
            or ws_session.clock_domain_id!=ws.clock_domain_id):
        return Consistency.INVALID_INPUT
    if abs(rest.received_monotonic_ns-ws.received_monotonic_ns)>policy.comparison_window_ms*1000000:
        return Consistency.NOT_COMPARABLE
    # Symmetric comparison against the larger positive price, no float conversion.
    diff=abs(rest.mark_px-ws.mark_px)*Decimal(10000)/max(rest.mark_px,ws.mark_px)
    return Consistency.CONSISTENT if diff<=policy.tolerance_bps else Consistency.MISMATCH

def assess_exchange(observation, claimed_binding=None, claimed_clock=None, policy=Policy()):
    """No audited exchange proof or trusted clock exists: all claims fail closed."""
    if type(policy) is not Policy or not policy.valid() or not _valid_observation(observation):
        return ExchangeState.INVALID_EVIDENCE
    if claimed_binding is not None or claimed_clock is not None:
        return ExchangeState.INVALID_EVIDENCE
    return ExchangeState.UNVERIFIED

def evaluate(observation,now_monotonic_ns,clock_domain_id,policy=Policy(),session=None,
             counterpart=None,claimed_binding=None,claimed_clock=None,replay_state=ReplayState.NOT_CHECKED):
    """Read-only offline result; recommendation_eligible is always False."""
    transport=assess_transport(observation,now_monotonic_ns,clock_domain_id,policy,session)
    exchange=assess_exchange(observation,claimed_binding,claimed_clock,policy)
    consistency=Consistency.UNKNOWN
    if counterpart is not None:
        if type(observation) is Observation and observation.source_channel=='REST':
            consistency=compare_rest_ws(observation,counterpart,policy,session)
        else:
            consistency=compare_rest_ws(counterpart,observation,policy,session)
    if type(replay_state) is not ReplayState:
        replay_state=ReplayState.UNAVAILABLE
    reasons=list(transport.reason_codes)
    if exchange is ExchangeState.INVALID_EVIDENCE: reasons.append('invalid_exchange_evidence')
    if exchange is ExchangeState.UNVERIFIED and 'missing_exchange_provenance' not in reasons: reasons.append('missing_exchange_provenance')
    if consistency is Consistency.MISMATCH: reasons.append('rest_ws_mismatch')
    if consistency is Consistency.INVALID_INPUT: reasons.append('rest_ws_invalid_input')
    if consistency is Consistency.NOT_COMPARABLE: reasons.append('rest_ws_not_comparable')
    if replay_state is not ReplayState.NOT_CHECKED: reasons.append('replay_'+replay_state.value.lower())
    return FreshnessAssessment(transport.transport_state,exchange,consistency,replay_state,
                               transport.transport_age_ms,None,False,False,tuple(dict.fromkeys(reasons)))
