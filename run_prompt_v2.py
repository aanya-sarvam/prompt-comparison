"""Run prompt_v2.py through Gemini on the 10 expert-validated deeds and save the
new outputs. The OLD outputs come from grounding_results_batch1.jsonl (already
on disk) - we do NOT re-run the old prompt.

Run LOCALLY (needs Vertex access + your local page images):

    python run_prompt_v2.py \\
        --export vertex_10_corrected.json \\
        --input-json-dir C:\\Users\\aanya\\Downloads\\deed-validator\\input_json \\
        --images-root  C:\\Users\\aanya\\Downloads\\deed-validator\\data\\vertex_batch\\images \\
        --project vision-projects-463307 --location global \\
        --model gemini-3.6-flash \\
        --out new_outputs.json

Requires: pip install google-genai ; prompt_v2.py importable (same folder).

Targets are reconstructed to match the ORIGINAL run:
  - scalar fields  -> english_value taken from export.json's src_block (the exact
                      values originally fed; avoids guessing execution_date logic)
  - list fields    -> composite strings taken verbatim from the input_json file
                      (sellerDetails / buyerDetails / propertyDetails)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_v2  # noqa: E402

LIST_IDS = {"seller_details", "buyer_details", "property_details"}
# input_json camelCase key -> (target id, type)
LIST_MAP = {
    "sellerDetails": ("seller_details", "party_list"),
    "buyerDetails": ("buyer_details", "party_list"),
    "propertyDetails": ("property_details", "property_list"),
}
SCALAR_LABELS = {
    "deed_type": "Deed Type", "district": "District", "office": "Office",
    "registration_date": "Registration Date", "presentation_date": "Presentation Date",
    "execution_date": "Execution Date", "consideration_amount": "Consideration Amount",
    "old_reg_no": "Old Reg No",
}


# ---------------------------------------------------------------------------
# EDIT THIS to match your local page-image layout. It must return the ordered
# list of page-image file paths for ONE deed. It tries a few common patterns
# and prints what it found; tweak the patterns list if yours differs.
# ---------------------------------------------------------------------------
def get_image_paths(deed_number: str, images_root: str) -> list[str]:
    patterns = [
        os.path.join(images_root, deed_number, "*.jpg"),
        os.path.join(images_root, deed_number, "*.png"),
        os.path.join(images_root, f"{deed_number}_*.jpg"),
        os.path.join(images_root, f"{deed_number}_*.png"),
        os.path.join(images_root, f"{deed_number}*.jpg"),
        os.path.join(images_root, f"{deed_number}*.png"),
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits
    raise FileNotFoundError(
        f"No images for deed {deed_number} under {images_root} "
        f"(tried {len(patterns)} patterns) - edit get_image_paths()."
    )


def build_targets(export_doc: dict, input_json: dict) -> list[dict]:
    targets, seen = [], set()
    # scalars from export src_block (exact original english_values)
    for f in export_doc["fields"]:
        sb = f.get("src_block") or {}
        fid = sb.get("id")
        if fid in LIST_IDS or not fid:
            continue
        if (sb.get("item_index") or 0) != 0 or (sb.get("attr") or ""):
            continue
        if fid in seen:
            continue
        eng = (sb.get("english_value") or "").strip()
        if not eng:
            continue
        targets.append({"id": fid, "label": SCALAR_LABELS.get(fid, f.get("label") or fid),
                         "value": eng, "type": "text"})
        seen.add(fid)
    # list composites from input_json (verbatim original strings)
    for src_key, (fid, ftype) in LIST_MAP.items():
        val = (input_json.get(src_key) or "").strip()
        if val:
            targets.append({"id": fid, "label": fid.replace("_", " ").title(),
                            "value": val, "type": ftype})
    return targets


def call_gemini(client, model, system_instruction, user_prompt, image_paths):
    from google.genai import types
    parts = [types.Part.from_text(text=user_prompt)]
    for p in image_paths:
        mime = "image/png" if p.lower().endswith(".png") else "image/jpeg"
        with open(p, "rb") as fh:
            parts.append(types.Part.from_bytes(data=fh.read(), mime_type=mime))
    resp = client.models.generate_content(
        model=model,
        contents=[types.Content(role="user", parts=parts)],
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
        ),
    )
    text = resp.text or "[]"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw_unparsed": text}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", required=True)
    ap.add_argument("--input-json-dir", required=True)
    ap.add_argument("--images-root", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="global")
    ap.add_argument("--model", default="gemini-3.6-flash")
    ap.add_argument("--out", default="new_outputs.json")
    ap.add_argument("--sleep", type=float, default=2.0)
    args = ap.parse_args()

    from google import genai
    client = genai.Client(vertexai=True, project=args.project, location=args.location)

    export = json.load(open(args.export, encoding="utf-8"))
    results = []
    for doc in export:
        deed = str(doc["deed_number"])
        ij_path = os.path.join(args.input_json_dir, f"{deed}.json")
        if not os.path.exists(ij_path):
            print(f"SKIP {deed}: no input_json at {ij_path}")
            continue
        input_json = json.load(open(ij_path, encoding="utf-8"))
        try:
            image_paths = get_image_paths(deed, args.images_root)
        except FileNotFoundError as e:
            print(f"SKIP {deed}: {e}")
            continue

        targets = build_targets(doc, input_json)
        n_pages = len(image_paths)
        deed_type = doc.get("deed_type") or ""
        user_prompt = prompt_v2.build_user_prompt(targets, n_pages, deed_type)

        print(f"{deed}: {len(targets)} targets, {n_pages} pages -> calling Gemini (prompt_v2)...")
        out = call_gemini(client, args.model, prompt_v2.SYSTEM_INSTRUCTION, user_prompt, image_paths)
        results.append({
            "deed_number": deed, "deed_type": deed_type, "n_pages": n_pages,
            "targets_used": targets, "new_prompt_output": out,
        })
        json.dump(results, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        time.sleep(args.sleep)

    print(f"\nDone. Wrote {len(results)} deeds to {args.out}")


if __name__ == "__main__":
    main()
