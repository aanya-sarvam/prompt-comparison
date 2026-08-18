"""reconcile4.py — build a 4-way per-deed field comparison:

    Original IGR  |  Prompt 1 (Odia)  |  Expert-corrected (Odia)  |  Prompt 2 (Odia)

from the three artifacts we already have (no CSV needed):
  - export  : vertex_10_corrected.json  (DB export; odia_value = expert truth,
              src_block = original grounding row)
  - old     : old_outputs_10.jsonl      (prompt.py output, one JSON/deed)
  - new     : new_outputs.json          (prompt_v2.py output, one obj/deed)

Match verdicts (P1-vs-expert, P2-vs-expert) reuse the mature Odia-aware
normalization in diff_logic.py (date/number/currency folding, spelling &
script variants, place aliases).
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff_logic as dl  # noqa: E402

LIST_IDS = {"seller_details", "buyer_details", "property_details"}


# ---------- loaders -> {deed: {(id,attr): {odia, english, readback}}} --------
def _agg(rows):
    """Aggregate a deed's grounding rows by (id, attr): join per-item values in
    item_index order with ', ' (mirrors the portal's group value)."""
    buckets = {}
    for r in rows or []:
        fid = r.get("id") or ""
        attr = r.get("attr") or ""
        if not fid:
            continue
        key = (fid, attr)
        buckets.setdefault(key, []).append((
            r.get("item_index") or 0,
            (r.get("odia_text") or "").strip(),
            (r.get("english_value") or "").strip(),
            (r.get("latin_readback") or "").strip(),
        ))
    out = {}
    for key, items in buckets.items():
        items.sort(key=lambda t: t[0])
        out[key] = {
            "odia": ", ".join(v for _, v, _, _ in items if v),
            "english": ", ".join(v for _, _, v, _ in items if v),
            "readback": ", ".join(v for _, _, _, v in items if v),
        }
    return out


def load_old(path):
    by_deed = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            o = json.loads(line)
            deed = str(o.get("key") or o.get("reg_no") or "")
            by_deed[deed] = _agg(o.get("fields", []))
    return by_deed


def load_new(path):
    data = json.load(open(path, encoding="utf-8"))
    out = {}
    for r in data:
        deed = str(r["deed_number"])
        rows = r["new_prompt_output"]
        out[deed] = _agg(rows if isinstance(rows, list) else [])
    return out


def load_truth(path):
    """export.json -> {deed: {(id,attr): {odia (expert), english_meta}}}.
    A given (id,attr) can have several expert rows (one per party/plot); join
    them with ', ' for display and matching."""
    data = json.load(open(path, encoding="utf-8"))
    out = {}
    deed_types = {}
    for d in data:
        deed = str(d["deed_number"])
        deed_types[deed] = d.get("deed_type") or ""
        buckets = {}
        for f in d["fields"]:
            sb = f.get("src_block") or {}
            fid, attr = sb.get("id") or "", sb.get("attr") or ""
            tv = (f.get("odia_value") or "").strip()
            em = (sb.get("english_value") or "").strip()
            if not fid:
                continue
            b = buckets.setdefault((fid, attr), {"odia": [], "english": []})
            if tv:
                b["odia"].append(tv)
            if em:
                b["english"].append(em)
        out[deed] = {k: {"odia": ", ".join(v["odia"]), "english": ", ".join(v["english"])}
                     for k, v in buckets.items()}
    return out, deed_types


# ---------- verdict ---------------------------------------------------------
def verdict(pred_odia, pred_readback, truth_odia, field):
    """'match' | 'mismatch' | 'both-blank' | 'missing' — pred vs expert truth,
    reusing diff_logic's Odia-aware content key + fuzzy name/place tolerance."""
    p, t = dl._clean(pred_odia), dl._clean(truth_odia)
    if not t and not p:
        return "both-blank"
    if not t and p:
        return "extra"          # expert left blank, prompt produced something
    if t and not p:
        return "missing"        # expert has a value, prompt found nothing
    if dl._content_key(p, field) == dl._content_key(t, field):
        return "match"
    base = field.split(".")[0]
    attr = field.split(".")[1] if "." in field else ""
    if base in dl._FUZZY_FIELDS or attr in dl._FUZZY_ATTRS:
        gt_items = [x.strip() for x in t.split(",") if x.strip()]
        pr_items = [x.strip() for x in p.split(",") if x.strip()]
        if gt_items and pr_items and all(
                any(dl._fuzzy_equivalent(g, r, field) for g in gt_items) for r in pr_items):
            return "match"
        # cross-script fallback via readback
        rb_items = [x.strip() for x in (pred_readback or "").split(",") if x.strip()]
        if not dl._is_odia_script(t) and rb_items and all(
                any(dl._fuzzy_equivalent(g, r, field) for g in gt_items) for r in rb_items):
            return "match"
    if base == "deed_type":
        return "deed_type"      # English category vs on-page Odia term — expected
    return "mismatch"


LABELS = {
    "deed_type": "Deed Type", "district": "District", "office": "Office",
    "registration_date": "Registration Date", "presentation_date": "Presentation Date",
    "execution_date": "Execution Date", "consideration_amount": "Consideration Amount",
    "old_reg_no": "Old Reg No",
    "seller_details.name": "Seller · Name", "seller_details.relation_name": "Seller · Relation",
    "seller_details.address": "Seller · Address",
    "buyer_details.name": "Buyer · Name", "buyer_details.relation_name": "Buyer · Relation",
    "buyer_details.address": "Buyer · Address",
    "property_details.village": "Property · Village", "property_details.khata": "Property · Khata",
    "property_details.plot": "Property · Plot", "property_details.area": "Property · Area",
    "property_details.boundary_north": "Property · Boundary N",
    "property_details.boundary_south": "Property · Boundary S",
    "property_details.boundary_east": "Property · Boundary E",
    "property_details.boundary_west": "Property · Boundary W",
}


def field_label(fid, attr):
    key = f"{fid}.{attr}" if attr else fid
    return LABELS.get(key, key)


def build(export_path, old_path, new_path):
    truth, deed_types = load_truth(export_path)
    old = load_old(old_path)
    new = load_new(new_path)

    rows = []
    for deed in sorted(truth):
        keys = set(truth.get(deed, {})) | set(old.get(deed, {})) | set(new.get(deed, {}))
        for (fid, attr) in sorted(keys):
            key = (fid, attr)
            fname = f"{fid}.{attr}" if attr else fid
            t = truth.get(deed, {}).get(key, {})
            o = old.get(deed, {}).get(key, {})
            n = new.get(deed, {}).get(key, {})

            igr = o.get("english") or n.get("english") or t.get("english") or ""
            gt_od = t.get("odia", "")
            p1_od, p1_rb = o.get("odia", ""), o.get("readback", "")
            p2_od, p2_rb = n.get("odia", ""), n.get("readback", "")

            rows.append({
                "deed": deed,
                "group": "Property" if fid == "property_details" else
                         ("Seller" if fid == "seller_details" else
                          ("Buyer" if fid == "buyer_details" else "Deed")),
                "field": field_label(fid, attr),
                "Original IGR (sent to Gemini)": igr,
                "Prompt 1 (Odia)": p1_od,
                "Expert corrected (Odia)": gt_od,
                "Prompt 2 (Odia)": p2_od,
                "P1": verdict(p1_od, p1_rb, gt_od, fname),
                "P2": verdict(p2_od, p2_rb, gt_od, fname),
                "_p1_rb": p1_rb, "_p2_rb": p2_rb,
            })
    return pd.DataFrame(rows), deed_types


# quick CLI self-test
if __name__ == "__main__":
    df, dt = build("vertex_10_corrected.json", "old_outputs_10.jsonl", "new_outputs.json")
    print("rows:", len(df), "deeds:", df["deed"].nunique())
    for side in ("P1", "P2"):
        vc = df[side].value_counts().to_dict()
        print(side, vc)
