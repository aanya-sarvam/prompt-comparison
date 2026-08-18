"""Export a sample of expert-corrected vertex-batch deeds for prompt A/B testing.

Run this LOCALLY (not on Render) against the external DATABASE_URL, e.g. in
PowerShell:

    $env:DATABASE_URL = "postgresql://<user>:<pass>@<host>.render.com/<db>?sslmode=require"
    python export_vertex_samples.py --n 10 --out vertex_10_corrected.json

Picks documents where source='vertex' and at least one field's odia_value
differs from its own src_block->>'odia_text' (i.e. an expert changed the
value Gemini originally extracted). For each picked document, dumps every
field with:
  - IGR/English side  (current_value)          <- feeds build_user_prompt()
  - original Gemini extraction (src_block)      <- what the OLD prompt produced
  - corrected value (odia_value)                <- ground truth to score against
  - page_num                                    <- for locating the right image
"""
from __future__ import annotations

import argparse
import json
import os

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_TIMEZONE = "Asia/Kolkata"


def connect():
    if not DATABASE_URL:
        raise SystemExit("Set $env:DATABASE_URL first (see docstring).")
    con = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    con.execute(f"SET TIME ZONE '{DB_TIMEZONE}'")
    return con


PICK_DOCS_SQL = """
SELECT d.id, d.deed_number, d.deed_type, d.vertex_group, d.pdf_file, d.src_meta
FROM documents d
WHERE d.source = 'vertex'
  AND EXISTS (
        SELECT 1 FROM fields f
        WHERE f.document_id = d.id
          AND btrim(f.odia_value) <> btrim(COALESCE(f.src_block->>'odia_text', ''))
          AND btrim(COALESCE(f.src_block->>'odia_text', '')) <> ''  -- was actually attempted, not just filled from blank
  )
ORDER BY random()
LIMIT %(n)s
"""

FIELDS_FOR_DOC_SQL = """
SELECT id, section, label, ocr_value, current_value, odia_value,
       field_kind, layout_tag, page_num, src_block
FROM fields
WHERE document_id = %(doc_id)s
ORDER BY position
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", default="vertex_10_corrected.json")
    ap.add_argument("--vertex-group", choices=["mismatch", "control"], default=None,
                     help="Optionally restrict to one vertex_group")
    args = ap.parse_args()

    con = connect()
    try:
        sql = PICK_DOCS_SQL
        params = {"n": args.n}
        if args.vertex_group:
            sql = sql.replace(
                "WHERE d.source = 'vertex'",
                "WHERE d.source = 'vertex' AND d.vertex_group = %(vg)s",
            )
            params["vg"] = args.vertex_group
        docs = con.execute(sql, params).fetchall()

        out = []
        for doc in docs:
            fields = con.execute(FIELDS_FOR_DOC_SQL, {"doc_id": doc["id"]}).fetchall()
            n_corrected = sum(
                1 for f in fields
                if (f["odia_value"] or "").strip()
                != ((f["src_block"] or {}).get("odia_text") or "").strip()
            )
            out.append({
                "document_id": doc["id"],
                "deed_number": doc["deed_number"],
                "deed_type": doc["deed_type"],
                "vertex_group": doc["vertex_group"],
                "pdf_file": doc["pdf_file"],
                "src_meta": doc["src_meta"],
                "n_fields": len(fields),
                "n_corrected_fields": n_corrected,
                "fields": fields,
            })

        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2, default=str)

        print(f"Wrote {len(out)} documents to {args.out}")
        for d in out:
            print(f"  doc {d['document_id']} ({d['deed_number']}, {d['deed_type']}, "
                  f"{d['vertex_group']}): {d['n_corrected_fields']}/{d['n_fields']} fields corrected")
    finally:
        con.close()


if __name__ == "__main__":
    main()
