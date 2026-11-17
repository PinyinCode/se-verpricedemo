"""Read-only inventory of VCI BCTC field mappings observed in live documents."""
from bctc_rules import resolve_vci_field_mapping


def build_live_bctc_field_catalog(bctc_by_symbol, max_fields=500):
    aggregates = {}
    canonical_sources = {}
    for symbol, doc in (bctc_by_symbol or {}).items():
        if not isinstance(doc, dict):
            continue
        for section in ("income_statement", "balance_sheet", "cash_flow"):
            rows = doc.get(section) or []
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                source_field = str(row.get("source_field") or "").strip().lower()
                title = str(row.get("name") or "").strip()
                stored_id = str(row.get("id") or "").strip()
                source_section = str(row.get("source_section") or section).strip().lower()
                if not source_field:
                    continue
                resolved = resolve_vci_field_mapping(source_section, source_field, title)
                period = str(row.get("period") or "").strip().upper()
                canonical_id = resolved.get("canonical_id") or stored_id
                if canonical_id and period:
                    collision_key = (str(symbol).upper(), source_section, period, canonical_id)
                    canonical_sources.setdefault(collision_key, set()).add(source_field)
                key = (source_section, source_field, title, stored_id,
                       resolved.get("canonical_id") or "", resolved.get("mapping_method") or "UNMAPPED")
                item = aggregates.setdefault(key, {
                    "source_section": source_section,
                    "source_field": source_field,
                    "source_title": title,
                    "stored_canonical_id": stored_id,
                    "dictionary_canonical_id": resolved.get("canonical_id"),
                    "mapping_method": resolved.get("mapping_method"),
                    "mapping_status": resolved.get("mapping_status"),
                    "row_count": 0,
                    "_issuers": set(),
                    "_periods": set(),
                })
                item["row_count"] += 1
                item["_issuers"].add(str(symbol).upper())
                period = str(row.get("period") or "").strip()
                if period:
                    item["_periods"].add(period)
    rows = []
    for item in aggregates.values():
        item["issuer_count"] = len(item.pop("_issuers"))
        item["period_count"] = len(item.pop("_periods"))
        item["mapping_mismatch"] = bool(
            item["dictionary_canonical_id"] and
            item["stored_canonical_id"] != item["dictionary_canonical_id"]
        )
        rows.append(item)
    rows.sort(key=lambda x: (-x["issuer_count"], -x["row_count"],
                             x["source_section"], x["source_field"], x["source_title"]))
    total = len(rows)
    rows = rows[:max(1, int(max_fields))]
    collision_counts = {}
    collision_sources = {}
    for (_symbol, _section, _period, canonical_id), fields in canonical_sources.items():
        if len(fields) > 1:
            collision_counts[canonical_id] = collision_counts.get(canonical_id, 0) + 1
            collision_sources.setdefault(canonical_id, set()).update(fields)
    collisions = [
        {"canonical_id": key, "issuer_period_collision_count": count,
         "source_fields": sorted(collision_sources[key])}
        for key, count in sorted(collision_counts.items())
    ]
    return {
        "field_count": total,
        "reported_field_count": len(rows),
        "unmapped_field_count": sum(1 for x in rows if not x["dictionary_canonical_id"]),
        "mapping_mismatch_count": sum(1 for x in rows if x["mapping_mismatch"]),
        "fields": rows,
        "canonical_source_collisions": collisions,
        "safety": {"financial_values_exported": False, "issuer_symbols_exported": False},
    }
