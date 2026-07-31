"""
diff_logic.py — reconciles the deed-validator ground truth export
(ground_truth.csv, section/label keyed, comma-joined multi-item strings)
against a fresh Gemini realtime run (realtime_fields.csv, field_id/attr/
item_index keyed) so the two can be compared field-by-field per deed.

Canonical field vocabulary follows the Gemini side (field_id[.attr]),
since that's what prompt.py needs to be refined against.
"""
import re
import pandas as pd

# section (lowercased, "(n)" suffix stripped) + label (lowercased) -> canonical field
LABEL_MAP = {
    ("deed details", "deed type"): "deed_type",
    ("deed details", "district"): "district",
    ("deed details", "office"): "office",
    ("deed details", "registration office"): "office",
    ("deed details", "registration date"): "registration_date",
    ("deed details", "presentation date"): "presentation_date",
    ("deed details", "consideration amount"): "consideration_amount",
    ("deed details", "old reg no"): "old_reg_no",
    ("deed details", "old registration no"): "old_reg_no",
    ("seller", "name"): "seller_details.name",
    ("seller", "relation name"): "seller_details.relation_name",
    ("seller", "address"): "seller_details.address",
    ("buyer", "name"): "buyer_details.name",
    ("buyer", "relation name"): "buyer_details.relation_name",
    ("buyer", "address"): "buyer_details.address",
    ("properties", "village"): "property_details.village",
    ("properties", "khata"): "property_details.khata",
    ("properties", "plot"): "property_details.plot",
    ("properties", "area"): "property_details.area",
}

_STRIP_COUNT = re.compile(r"\s*\(\d+\)\s*$")


def _norm_section(section: str) -> str:
    s = _STRIP_COUNT.sub("", str(section or "")).strip().lower()
    if s.startswith("seller"):
        return "seller"
    if s.startswith("buyer"):
        return "buyer"
    if s.startswith("propert"):
        return "properties"
    return s


def canonical_field(section: str, label: str):
    key = (_norm_section(section), str(label or "").strip().lower())
    return LABEL_MAP.get(key)


def _clean(v):
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    return str(v).strip()


def _norm_compare(v: str) -> str:
    """Loose normalization for equality checks: collapse whitespace/commas,
    drop trailing punctuation, case-fold. Not used for display, only for
    the match/mismatch verdict."""
    v = _clean(v)
    v = re.sub(r"\s+", " ", v)
    v = v.strip(" ,.")
    return v.lower()


def load_ground_truth(path_csv: str) -> pd.DataFrame:
    gt = pd.read_csv(path_csv)
    gt["field"] = gt.apply(lambda r: canonical_field(r["section"], r["label"]), axis=1)
    gt["deed_number"] = gt["deed_number"].astype(str)
    return gt


def load_realtime(path_csv: str) -> pd.DataFrame:
    rt = pd.read_csv(path_csv)
    rt["reg_no"] = rt["reg_no"].astype(str)
    rt["field"] = rt.apply(
        lambda r: r["field_id"] if _clean(r.get("attr")) == ""
        else f"{r['field_id']}.{r['attr']}", axis=1)
    if "latin_readback" not in rt.columns:
        rt["latin_readback"] = ""
    return rt


def _joined_realtime_value(rt_rows: pd.DataFrame, col: str) -> str:
    """Join a field's per-item_index rows (english_value or odia_text) in
    item_index order with ', ' — mirrors the DB's comma-joined group value."""
    ordered = rt_rows.sort_values("item_index")
    vals = [_clean(v) for v in ordered[col].tolist()]
    return ", ".join(vals)


def build_comparison(gt: pd.DataFrame, rt: pd.DataFrame) -> pd.DataFrame:
    """One row per (deed_number, canonical field): ground truth (English +
    Odia, as corrected by the expert) vs the fresh Gemini run's joined value,
    plus a match verdict for each channel."""
    rows = []
    deed_numbers = sorted(set(gt["deed_number"]) & set(rt["reg_no"]))
    for deed in deed_numbers:
        gt_d = gt[gt["deed_number"] == deed]
        rt_d = rt[rt["reg_no"] == deed]
        fields = sorted(set(f for f in gt_d["field"].tolist() if f))
        for field in fields:
            gt_row = gt_d[gt_d["field"] == field]
            if gt_row.empty:
                continue
            gt_row = gt_row.iloc[0]
            rt_rows = rt_d[rt_d["field"] == field]

            gt_en = _clean(gt_row["corrected_english"])
            gt_od = _clean(gt_row["corrected_odia"])
            gemini_orig_en = _clean(gt_row["gemini_original_english"])

            if rt_rows.empty:
                rt_en, rt_od, rt_readback = "", "", ""
                notes = "no matching row in fresh Gemini run"
            else:
                rt_en = _joined_realtime_value(rt_rows, "english_value")
                rt_od = _joined_realtime_value(rt_rows, "odia_text")
                rt_readback = _joined_realtime_value(rt_rows, "latin_readback")
                notes = "; ".join(
                    n for n in rt_rows.sort_values("item_index")["notes"].tolist()
                    if _clean(n))

            # ground_truth_readback: the expert's corrected_english column, used
            # as the romanized/English-side form of their Odia correction (this
            # is what corrected_english IS in practice for place names, reg
            # numbers, etc. — see e.g. district: corrected_odia="ଅନୁଗୋଳ",
            # corrected_english="ANGUL").
            gt_readback = gt_en

            en_match = _norm_compare(gt_en) == _norm_compare(rt_en) if gt_en else None
            od_match = _norm_compare(gt_od) == _norm_compare(rt_od) if gt_od else None
            readback_match = (
                _norm_compare(gt_readback) == _norm_compare(rt_readback)
                if gt_readback else None
            )

            # Script-only mismatch: Gemini's odia_text disagrees with the
            # expert's Odia script, BUT the romanized readback lines up fine —
            # i.e. Gemini extracted the right content, just rendered it in the
            # wrong script for the odia_text field. Genuine content mismatch:
            # readback itself disagrees, meaning Gemini got the underlying
            # value wrong, not just its script.
            if od_match is False and readback_match is True:
                issue_type = "script-only (content correct, wrong script)"
            elif readback_match is False:
                issue_type = "content mismatch"
            elif od_match is False and readback_match is None:
                issue_type = "content mismatch (no readback to cross-check)"
            else:
                issue_type = "match"

            rows.append({
                "deed_number": deed,
                "field": field,
                "ground_truth_english": gt_en,
                "fresh_gemini_english": rt_en,
                "english_match": en_match,
                "ground_truth_odia": gt_od,
                "fresh_gemini_odia": rt_od,
                "odia_match": od_match,
                "ground_truth_readback": gt_readback,
                "fresh_gemini_readback": rt_readback,
                "readback_match": readback_match,
                "issue_type": issue_type,
                "gemini_original_english_at_ingest": gemini_orig_en,
                "was_corrected_by_expert": _norm_compare(gt_en) != _norm_compare(gemini_orig_en),
                "notes": notes,
            })
    return pd.DataFrame(rows)


def field_summary(comparison: pd.DataFrame) -> pd.DataFrame:
    """Aggregate mismatch rate per field — this is the prompt-refinement
    target list."""
    out = []
    for field, grp in comparison.groupby("field"):
        n = len(grp)
        en_checked = grp["english_match"].notna().sum()
        en_mismatches = (grp["english_match"] == False).sum()
        od_checked = grp["odia_match"].notna().sum()
        od_mismatches = (grp["odia_match"] == False).sum()
        rb_checked = grp["readback_match"].notna().sum()
        rb_mismatches = (grp["readback_match"] == False).sum()
        script_only = (grp["issue_type"] == "script-only (content correct, wrong script)").sum()
        content_mismatch = grp["issue_type"].str.startswith("content mismatch", na=False).sum()
        out.append({
            "field": field,
            "n_deeds": n,
            "english_mismatches": int(en_mismatches),
            "english_checked": int(en_checked),
            "english_accuracy_pct": round(100 * (1 - en_mismatches / en_checked), 1) if en_checked else None,
            "odia_mismatches": int(od_mismatches),
            "odia_checked": int(od_checked),
            "odia_accuracy_pct": round(100 * (1 - od_mismatches / od_checked), 1) if od_checked else None,
            "readback_mismatches": int(rb_mismatches),
            "readback_checked": int(rb_checked),
            "readback_accuracy_pct": round(100 * (1 - rb_mismatches / rb_checked), 1) if rb_checked else None,
            "script_only_issues": int(script_only),
            "genuine_content_mismatches": int(content_mismatch),
        })
    df = pd.DataFrame(out)
    return df.sort_values(["english_accuracy_pct", "odia_accuracy_pct"], na_position="first")
