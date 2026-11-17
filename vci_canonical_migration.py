"""Conservative, read-only planner for legacy VCI canonical-ID migration."""
from collections import Counter, defaultdict

from bctc_rules import resolve_vci_field_mapping

SECTIONS = ("income_statement", "balance_sheet", "cash_flow")


def build_vci_canonical_migration_dry_run(bctc_docs, include_internal=False):
    """Plan exact-dictionary ID repairs; never mutate or export financial values."""
    groups = defaultdict(list)
    records = []
    for doc in bctc_docs if isinstance(bctc_docs, (list, tuple)) else []:
        if not isinstance(doc, dict):
            continue
        symbol_raw = str(doc.get("symbol") or "").strip()
        symbol = symbol_raw.upper()
        if not symbol:
            continue
        for section in SECTIONS:
            rows = doc.get(section) or []
            if not isinstance(rows, list):
                continue
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                source_field_raw = str(row.get("source_field") or "").strip()
                source_field = source_field_raw.lower()
                stored_id = str(row.get("id") or "").strip()
                if not source_field and not stored_id:
                    continue
                resolved = resolve_vci_field_mapping(
                    section, source_field, row.get("name") or ""
                )
                canonical_id = resolved.get("canonical_id")
                if not canonical_id and stored_id and not stored_id.upper().startswith("VCI_"):
                    canonical_id = stored_id
                period_key = "period" if row.get("period") not in (None, "") else "report_period"
                period_raw = str(row.get(period_key) or "").strip()
                period = period_raw.upper()
                record = {
                    "symbol": symbol,
                    "symbol_raw": symbol_raw,
                    "section": section,
                    "index": index,
                    "source_field": source_field,
                    "source_field_raw": source_field_raw,
                    "old_id": stored_id,
                    "canonical_id": canonical_id,
                    "mapping_method": resolved.get("mapping_method"),
                    "period": period,
                    "period_raw": period_raw,
                    "period_key": period_key,
                    "document_id": doc.get("_id"),
                }
                records.append(record)
                if canonical_id and period:
                    groups[(symbol, section, period, canonical_id)].append(record)

    counts = Counter()
    by_section = Counter()
    by_field = Counter()
    conflicts = Counter()
    planned = []
    for record in records:
        if not record["canonical_id"] or not record["source_field"]:
            counts["unmapped_or_unresolved"] += 1
            continue
        if record["old_id"] == record["canonical_id"]:
            counts["already_canonical"] += 1
            continue
        if record["mapping_method"] != "EXACT_FIELD_DICTIONARY":
            counts["candidate_mapping_not_exact"] += 1
            continue
        if not record["period"]:
            counts["missing_period"] += 1
            continue
        group = groups[(record["symbol"], record["section"],
                        record["period"], record["canonical_id"])]
        if len(group) != 1:
            counts["collision_quarantined"] += 1
            conflicts[record["canonical_id"]] += 1
            continue
        counts["safe_exact_candidates"] += 1
        by_section[record["section"]] += 1
        by_field[(record["source_field"], record["canonical_id"])] += 1
        planned.append({
            # Exact selector required for a later, separately approved migration.
            # Never include financial values in the migration manifest.
            "symbol": record["symbol"],
            "symbol_raw": record["symbol_raw"],
            "section": record["section"],
            "row_index": record["index"],
            "period": record["period"],
            "source_field": record["source_field"],
            "source_field_raw": record["source_field_raw"],
            "old_id": record["old_id"],
            "new_id": record["canonical_id"],
            "period_raw": record["period_raw"],
            "period_key": record["period_key"],
            **({"document_id": record["document_id"]} if include_internal and record["document_id"] is not None else {}),
        })

    return {
        "mode": "DRY_RUN_READ_ONLY",
        "write_operations_executed": 0,
        "counts": dict(sorted(counts.items())),
        "safe_candidate_count": len(planned),
        "safe_candidates_by_section": dict(sorted(by_section.items())),
        "safe_candidates_by_source_field": [
            {"source_field": field, "canonical_id": canonical,
             "row_count": count}
            for (field, canonical), count in sorted(by_field.items())
        ],
        "collision_quarantine_by_canonical_id": [
            {"canonical_id": key, "row_count": count}
            for key, count in sorted(conflicts.items())
        ],
        "sample_safe_candidates": [
            {key: value for key, value in item.items() if key != "document_id"}
            for item in planned[:50]
        ],
        **({"safe_candidates_internal": planned} if include_internal else {}),
        "safety": {
            "financial_values_exported": False,
            "issuer_symbols_exported": False,
            "mongo_writes_enabled": False,
            "title_rule_or_generic_mappings_auto_migrated": False,
        },
    }
