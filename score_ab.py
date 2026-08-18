"""Score OLD (grounding jsonl) vs NEW (prompt_v2 Gemini output) against expert
ground truth (export.json's odia_value), per field.

Usage:
    python score_ab.py \\
        --export vertex_10_corrected.json \\
        --old old_outputs_10.jsonl \\
        --new new_outputs.json \\
        --out ab_scorecard.json

Pure stdlib - runs locally or here. Scoring is SET-BASED per (id, attr):
truth can have several values for a list field (one per party/plot), so we
measure how many of the expert's values each side covers (recall), plus a
simple coverage flag (any value produced where truth is non-empty). Scalars
are singletons, so this reduces to the usual 0/1 match.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
from collections import defaultdict

DIGIT_HINTS = ("plot", "khata", "area", "amount", "reg_no", "date")
NAME_HINTS = ("name", "buyer", "seller", "relation")


def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())


_ODIA_DIGITS = {ord("୦") + i: str(i) for i in range(10)}       # ୦-୯ -> 0-9
_DEVA_DIGITS = {ord("०") + i: str(i) for i in range(10)}       # ०-९ -> 0-9 (just in case)


def digits(s):
    s = (s or "").translate(_ODIA_DIGITS).translate(_DEVA_DIGITS)
    return re.sub(r"[^0-9]", "", s)


def kind_of(fid, attr):
    key = f"{fid}.{attr}".lower()
    if any(h in key for h in DIGIT_HINTS):
        return "digit"
    if any(h in key for h in NAME_HINTS):
        return "name"
    return "text"


def sim(a, b):
    a, b = norm(a), norm(b)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def val_match(kind, pred, truth):
    if kind == "digit":
        return digits(pred) == digits(truth) and digits(truth) != ""
    thr = 0.80 if kind == "name" else 0.60
    return sim(pred, truth) >= thr


def split_multi(v):
    """A portal ground-truth list value may hold several comma-joined items."""
    return [p.strip() for p in re.split(r"[,\uFF0C]", v or "") if p.strip()]


# ---- ground truth: (id, attr) -> list of expert values ---------------------
def truth_map(export_doc):
    m = defaultdict(list)
    for f in export_doc["fields"]:
        sb = f.get("src_block") or {}
        fid, attr = sb.get("id") or "", sb.get("attr") or ""
        tv = (f.get("odia_value") or "").strip()
        if not tv:
            continue
        for piece in (split_multi(tv) if attr or fid.endswith("_details") else [tv]):
            m[(fid, attr)].append(piece)
    return m


# ---- predictions: (id, attr) -> list of predicted odia_text ----------------
def pred_map_from_rows(rows):
    m = defaultdict(list)
    for r in rows or []:
        fid, attr = r.get("id") or "", r.get("attr") or ""
        txt = (r.get("odia_text") or "").strip()
        if txt:
            m[(fid, attr)].append(txt)
    return m


def rows_of_new(output):
    if isinstance(output, list):
        return output
    return []


def score_side(truth, pred):
    """Return per-(id,attr) dict: recall, covered, kind, n_truth, n_matched."""
    out = {}
    for key, tvals in truth.items():
        fid, attr = key
        kind = kind_of(fid, attr)
        pvals = pred.get(key, [])
        matched = 0
        for t in tvals:
            if any(val_match(kind, p, t) for p in pvals):
                matched += 1
        out[key] = {
            "kind": kind, "n_truth": len(tvals), "n_matched": matched,
            "recall": matched / len(tvals) if tvals else 1.0,
            "covered": len(pvals) > 0,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--old", required=True, help="grounding jsonl filtered to the 10 deeds")
    ap.add_argument("--new", required=True, help="new_outputs.json from run_prompt_v2.py")
    ap.add_argument("--out", default="ab_scorecard.json")
    args = ap.parse_args()

    export = {str(d["deed_number"]): d for d in json.load(open(args.export, encoding="utf-8"))}

    old_by_deed = {}
    with open(args.old, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            key = str(obj.get("key") or obj.get("reg_no") or "")
            old_by_deed[key] = obj.get("fields", [])

    new_by_deed = {str(r["deed_number"]): rows_of_new(r["new_prompt_output"])
                   for r in json.load(open(args.new, encoding="utf-8"))}

    agg = {"old": defaultdict(lambda: [0, 0, 0.0, 0]),   # covered, truth_total, recall_sum, field_count
           "new": defaultdict(lambda: [0, 0, 0.0, 0])}
    per_deed = []
    fixed, broken = [], []

    for deed, doc in export.items():
        if deed not in new_by_deed:
            continue
        tm = truth_map(doc)
        old_s = score_side(tm, pred_map_from_rows(old_by_deed.get(deed, [])))
        new_s = score_side(tm, pred_map_from_rows(new_by_deed.get(deed, [])))

        d_old_cov = d_new_cov = d_old_rec = d_new_rec = 0.0
        for key, tvals in tm.items():
            fid, attr = key
            grp = "property_details" if fid == "property_details" else (
                "party_details" if fid in ("seller_details", "buyer_details") else "scalar")
            for side, s in (("old", old_s[key]), ("new", new_s[key])):
                a = agg[side][grp]
                a[0] += int(s["covered"]); a[1] += 1
                a[2] += s["recall"]; a[3] += 1
            os_, ns_ = old_s[key], new_s[key]
            d_old_cov += os_["covered"]; d_new_cov += ns_["covered"]
            d_old_rec += os_["recall"];  d_new_rec += ns_["recall"]
            if os_["recall"] < 1.0 and ns_["recall"] == 1.0:
                fixed.append({"deed": deed, "field": f"{fid}.{attr}", "truth": tvals})
            if os_["recall"] == 1.0 and ns_["recall"] < 1.0:
                broken.append({"deed": deed, "field": f"{fid}.{attr}", "truth": tvals})

        n = len(tm)
        per_deed.append({
            "deed": deed, "n_fields": n,
            "old_coverage": round(d_old_cov / n, 3), "new_coverage": round(d_new_cov / n, 3),
            "old_recall": round(d_old_rec / n, 3), "new_recall": round(d_new_rec / n, 3),
        })

    def summarize(side):
        rows = {}
        tot_cov = tot_n = 0; rec_sum = rec_n = 0
        for grp, a in agg[side].items():
            rows[grp] = {"coverage": round(a[0] / a[1], 3) if a[1] else None,
                          "recall": round(a[2] / a[3], 3) if a[3] else None,
                          "n": a[1]}
            tot_cov += a[0]; tot_n += a[1]; rec_sum += a[2]; rec_n += a[3]
        rows["ALL"] = {"coverage": round(tot_cov / tot_n, 3) if tot_n else None,
                        "recall": round(rec_sum / rec_n, 3) if rec_n else None, "n": tot_n}
        return rows

    result = {
        "old": summarize("old"), "new": summarize("new"),
        "per_deed": per_deed,
        "fields_fixed_by_new": fixed, "fields_broken_by_new": broken,
    }
    json.dump(result, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=== OLD (prompt.py) ===");  print(json.dumps(result["old"], indent=2))
    print("\n=== NEW (prompt_v2.py) ==="); print(json.dumps(result["new"], indent=2))
    print(f"\nfixed by new: {len(fixed)} | broken by new: {len(broken)}")
    print(f"scorecard -> {args.out}")


if __name__ == "__main__":
    main()
