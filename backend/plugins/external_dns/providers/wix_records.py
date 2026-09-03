"""
plugins/external_dns/providers/wix_records.py — Wix rrset read-modify-write math.

Wix stores one record object per (hostName, type) with a `values[]` array and
only accepts PATCH `additions`/`deletions` of whole rrsets. These pure planners
turn a desired add/delete/update into the (additions, deletions) pair to PATCH,
given the zone's current records. Kept separate from wix.py so the tricky set
math is unit-testable in isolation.
"""
from __future__ import annotations

from plugins.external_dns.providers.base import ExternalDnsError


def find_rrset(records: list[dict], host: str, rtype: str) -> dict | None:
    host_l = (host or "").strip().lower()
    rtype_u = (rtype or "").strip().upper()
    for rec in records or []:
        if (rec.get("type") or "").upper() == rtype_u and \
           (rec.get("hostName") or "").strip().lower() == host_l:
            return rec
    return None


def _values(rrset: dict | None) -> list[str]:
    return list((rrset or {}).get("values", []) or [])


def _deletion(rrset: dict | None) -> dict | None:
    vals = _values(rrset)
    if not rrset or not vals:
        return None
    return {
        "type": (rrset.get("type") or "").upper(),
        "hostName": rrset.get("hostName") or "",
        "values": vals,
    }


def _addition(host: str, rtype: str, values: list[str], ttl: int) -> dict | None:
    if not values:
        return None
    return {
        "type": (rtype or "").upper(),
        "hostName": host,
        "ttl": int(ttl or 3600),
        "values": list(values),
    }


def _guard(values: list[str], max_values: int) -> None:
    if max_values and len(values) > max_values:
        raise ExternalDnsError(
            f"Wix allows at most {max_values} values per record type.", status_code=400
        )


def plan_add(records, host, rtype, value, ttl, max_values=0):
    existing = find_rrset(records, host, rtype)
    values = _values(existing)
    if value in values:
        return [], []                      # already present — no-op
    new_values = values + [value]
    _guard(new_values, max_values)
    deletions = [d for d in (_deletion(existing),) if d]
    additions = [a for a in (_addition(host, rtype, new_values, ttl),) if a]
    return additions, deletions


def plan_delete_value(records, host, rtype, value):
    existing = find_rrset(records, host, rtype)
    values = _values(existing)
    if not values:
        return [], []
    remaining = [v for v in values if v != value]
    deletions = [d for d in (_deletion(existing),) if d]
    additions = [a for a in (_addition(host, rtype, remaining, (existing or {}).get("ttl") or 3600),) if a]
    return additions, deletions


def plan_update(records, old_host, old_type, old_value, new_host, new_type, new_value, ttl, max_values=0):
    """Replace one value, tolerating a host and/or type change."""
    old_rrset = find_rrset(records, old_host, old_type)
    if old_rrset is None:
        # Nothing to replace — degrade to an add of the new value.
        return plan_add(records, new_host, new_type, new_value, ttl, max_values)

    same = (old_host or "").lower() == (new_host or "").lower() and \
           (old_type or "").upper() == (new_type or "").upper()

    if same:
        values = [new_value if v == old_value else v for v in _values(old_rrset)]
        if new_value not in values:
            values.append(new_value)
        _guard(values, max_values)
        deletions = [d for d in (_deletion(old_rrset),) if d]
        additions = [a for a in (_addition(new_host, new_type, values, ttl),) if a]
        return additions, deletions

    # Host/type changed: shrink the old rrset and grow the (possibly new) target.
    new_rrset = find_rrset(records, new_host, new_type)
    old_remaining = [v for v in _values(old_rrset) if v != old_value]
    new_values = _values(new_rrset)
    if new_value not in new_values:
        new_values = new_values + [new_value]
    _guard(new_values, max_values)

    deletions = [d for d in (_deletion(old_rrset), _deletion(new_rrset)) if d]
    additions = [
        a for a in (
            _addition(old_host, old_type, old_remaining, (old_rrset or {}).get("ttl") or 3600),
            _addition(new_host, new_type, new_values, ttl),
        ) if a
    ]
    return additions, deletions
