"""
Deed Grounding — Run A vs Run B Comparison Viewer
Run locally:  streamlit run deed_run_comparison.py
Works for any two comparable runs (different prompt version, different model,
different temperature, etc.) — label each run yourself in the sidebar.

Expects these four files in the same folder (or upload from the sidebar):
  realtime_fields_a.csv / realtime_summary_a.json   (Run A / baseline)
  realtime_fields_b.csv / realtime_summary_b.json   (Run B / comparison)
"""

import json
import os
import pandas as pd
import streamlit as st

from diff_logic import (
    load_ground_truth, load_realtime, build_comparison, field_summary,
)

st.set_page_config(page_title="Deed Grounding — Run Comparison", layout="wide")

KEY = ["reg_no", "field_id", "item_index", "attr"]


@st.cache_data
def load_csv(path):
    df = pd.read_csv(path)
    df["found"] = df["found"].astype(str).str.strip().str.lower() == "true"
    return df


@st.cache_data
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


st.title("🧾 Deed Grounding — Run Comparison")
st.caption("Same 10 deeds, same real-time harness — compare any two runs (prompt version, model, temperature, etc.)")

with st.sidebar:
    st.header("Labels")
    label_a = st.text_input("Run A label", value="gemini-3.1-pro-preview")
    label_b = st.text_input("Run B label", value="gemini-3.6-flash (temp=0)")

    st.header("Data source")
    st.caption("Defaults to the bundled files — upload to override.")
    a_csv_f = st.file_uploader(f"Run A realtime_fields.csv", type="csv", key="ac")
    a_json_f = st.file_uploader(f"Run A realtime_summary.json", type="json", key="aj")
    b_csv_f = st.file_uploader(f"Run B realtime_fields.csv", type="csv", key="bc")
    b_json_f = st.file_uploader(f"Run B realtime_summary.json", type="json", key="bj")

    st.header("Ground truth (prompt refinement)")
    st.caption("Expert-corrected values from the deed-validator DB, vs a fresh Gemini run on the same deeds.")
    gt_csv_f = st.file_uploader("ground_truth.csv", type="csv", key="gtc")
    refine_csv_f = st.file_uploader("realtime_fields.csv (fresh run)", type="csv", key="rfc")

def _load_csv_or_none(path):
    return load_csv(path) if os.path.exists(path) else None


def _load_json_or_none(path):
    return load_json(path) if os.path.exists(path) else None


a_df = load_csv(a_csv_f) if a_csv_f else _load_csv_or_none("realtime_fields_a.csv")
b_df = load_csv(b_csv_f) if b_csv_f else _load_csv_or_none("realtime_fields_b.csv")
a_summary = load_json(a_json_f) if a_json_f else _load_json_or_none("realtime_summary_a.json")
b_summary = load_json(b_json_f) if b_json_f else _load_json_or_none("realtime_summary_b.json")

if a_df is None or b_df is None or a_summary is None or b_summary is None:
    st.warning(
        "No bundled data files found on this deployment. Please upload all "
        "four files above (Run A CSV + JSON, Run B CSV + JSON) to continue.",
        icon="⚠️",
    )
    st.stop()

gt_df = (load_ground_truth(gt_csv_f) if gt_csv_f
         else (load_ground_truth("ground_truth.csv") if os.path.exists("ground_truth.csv") else None))
refine_df = (load_realtime(refine_csv_f) if refine_csv_f
             else (load_realtime("realtime_fields_refine.csv")
                   if os.path.exists("realtime_fields_refine.csv") else None))
gt_comparison = build_comparison(gt_df, refine_df) if (gt_df is not None and refine_df is not None) else None
gt_field_summary = field_summary(gt_comparison) if gt_comparison is not None else None

merged = a_df.merge(b_df, on=KEY, suffixes=("_a", "_b"), how="outer", indicator=True)
merged["flipped"] = merged["found_a"].astype(str) != merged["found_b"].astype(str)
merged["direction"] = ""
merged.loc[(merged["found_a"] == False) & (merged["found_b"] == True), "direction"] = "improved"
merged.loc[(merged["found_a"] == True) & (merged["found_b"] == False), "direction"] = "regressed"
# rows present in only one run (e.g. blank-metadata targets that got dropped
# from the target list entirely rather than resolved) — flag separately so
# they don't get silently read as "improved"/"regressed"
merged["row_only_in"] = merged["_merge"].map({
    "left_only": "A only", "right_only": "B only", "both": ""
})

# --- Canonical original metadata value (should be identical across runs;
# coalesce so the same field always shows one value regardless of which
# run's export happened to record it).
_ev_a = merged["english_value_a"].fillna("")
_ev_b = merged["english_value_b"].fillna("")
merged["metadata_value"] = _ev_a.where(_ev_a != "", _ev_b)

tab_summary, tab_changes, tab_deeds, tab_ground_truth = st.tabs(
    ["📊 Field-level delta", "🔀 What actually flipped", "📄 Per-deed side-by-side",
     "✅ Ground Truth (prompt refinement)"]
)

# --- Summary --------------------------------------------------------------
with tab_summary:
    op = a_summary.get("per_field", {})
    np_ = b_summary.get("per_field", {})
    fields = sorted(set(op) | set(np_))
    rows = []
    for f in fields:
        o = op.get(f, {}).get("pct")
        n = np_.get(f, {}).get("pct")
        d = round(n - o, 1) if isinstance(o, (int, float)) and isinstance(n, (int, float)) else None
        rows.append({"field": f, f"{label_a} pct": o, f"{label_b} pct": n, "delta": d})
    sdf = pd.DataFrame(rows).sort_values("delta", ascending=False, na_position="last")

    c1, c2, c3 = st.columns(3)
    c1.metric("Deeds tested", b_summary.get("n_results", "—"))
    improved_n = (sdf["delta"] > 0).sum()
    regressed_n = (sdf["delta"] < 0).sum()
    c2.metric("Fields improved", int(improved_n))
    c3.metric("Fields regressed", int(regressed_n))

    st.dataframe(
        sdf.style.background_gradient(subset=["delta"], cmap="RdYlGn", vmin=-20, vmax=20),
        use_container_width=True, hide_index=True,
    )
    st.bar_chart(sdf.set_index("field")[[f"{label_a} pct", f"{label_b} pct"]])
    st.info(
        "A field showing 100% doesn't always mean it improved — if that field "
        "was blank in the DB metadata for a deed, it's skipped entirely rather "
        "than counted, which can also push the percentage up.",
        icon="ℹ️",
    )

# --- What flipped ----------------------------------------------------------
with tab_changes:
    flips = merged[merged["flipped"] & (merged["row_only_in"] == "")].copy()
    st.subheader(f"{len(flips)} field-instances changed found-status between the two runs")

    for direction, label in [("improved", f"✅ Newly found ({label_a} missed it)"),
                              ("regressed", f"⚠️ No longer found ({label_a} reported it)")]:
        group = flips[flips["direction"] == direction]
        st.markdown(f"### {label} — {len(group)}")
        for _, r in group.iterrows():
            with st.container(border=True):
                st.markdown(f"**Deed `{r['reg_no']}` · field `{r['field_id']}`**")
                st.caption(f"Original metadata (GCS): **{r.get('metadata_value') or '—'}**")
                col1, col2 = st.columns(2)
                with col1:
                    st.caption(label_a)
                    st.write("found:", r["found_a"])
                    st.write("value:", r.get("odia_text_a") or "—")
                    if r.get("latin_readback_a"):
                        st.caption(f"readback: {r['latin_readback_a']}")
                    st.caption(r.get("notes_a") or "")
                with col2:
                    st.caption(label_b)
                    st.write("found:", r["found_b"])
                    st.write("value:", r.get("odia_text_b") or "—")
                    if r.get("latin_readback_b"):
                        st.caption(f"readback: {r['latin_readback_b']}")
                    st.caption(r.get("notes_b") or "")
    if flips.empty:
        st.write("No changes detected between the two uploaded runs.")

# --- Per-deed side by side ---------------------------------------------------
with tab_deeds:
    st.caption(
        f"For each field: the **original metadata** (the value sent to Gemini as "
        f"the grounding target) vs what **{label_a}** found vs what **{label_b}** found."
    )
    reg_nos = sorted(merged["reg_no"].astype(str).unique().tolist())
    choice = st.selectbox("Select deed (reg_no)", reg_nos)
    ddf = merged[merged["reg_no"].astype(str) == choice].copy()

    view = ddf[[
        "field_id", "item_index", "attr", "metadata_value",
        "found_a", "odia_text_a", "latin_readback_a", "confidence_a",
        "found_b", "odia_text_b", "latin_readback_b", "confidence_b",
        "flipped",
    ]].rename(columns={
        "field_id": "Field",
        "item_index": "Item",
        "attr": "Attr",
        "metadata_value": "Original Metadata (GCS)",
        "found_a": f"{label_a}: Found",
        "odia_text_a": f"{label_a}: Transcribed",
        "latin_readback_a": f"{label_a}: Readback",
        "confidence_a": f"{label_a}: Conf.",
        "found_b": f"{label_b}: Found",
        "odia_text_b": f"{label_b}: Transcribed",
        "latin_readback_b": f"{label_b}: Readback",
        "confidence_b": f"{label_b}: Conf.",
        "flipped": "Flipped",
    })

    def hl(row):
        styles = [""] * len(row)
        if row["Flipped"]:
            styles = ["background-color: #ff9800; color: black; font-weight: bold;"] * len(row)
        return styles

    st.dataframe(
        view.style.apply(hl, axis=1),
        use_container_width=True, hide_index=True,
    )

# --- Ground Truth vs latest run ---------------------------------------------
with tab_ground_truth:
    if gt_comparison is None:
        st.warning(
            "Upload both `ground_truth.csv` (expert-corrected DB export) and "
            "the fresh Gemini `realtime_fields.csv` run on the same deeds "
            "(sidebar) to use this tab.",
            icon="⚠️",
        )
    else:
        st.caption(
            "Expert-corrected ground truth vs a fresh Gemini run on the *same* "
            "deeds. Gemini gives an Odia value **and** a Latin readback for it; "
            "the DB export's `corrected_english` plays the same readback role "
            "for the expert's correction — comparing readback-to-readback "
            "sidesteps script-choice noise (Odia digits vs Latin digits, "
            "Odia script vs romanized) and isolates genuine content errors."
        )

        n_deeds = gt_comparison["deed_number"].nunique()
        n_script_only = int((gt_comparison["issue_type"] == "script-only (content correct, wrong script)").sum())
        n_content = int(gt_comparison["issue_type"].str.startswith("content mismatch", na=False).sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("Deeds compared", n_deeds)
        c2.metric("Script-only issues", n_script_only, help="Gemini extracted the right content but rendered odia_text in the wrong script (e.g. Latin instead of Odia).")
        c3.metric("Genuine content mismatches", n_content, help="The romanized readback itself disagrees — Gemini got the underlying value wrong, not just its script.")

        st.markdown("#### Field accuracy (readback-based)")
        st.dataframe(
            gt_field_summary[[
                "field", "n_deeds", "readback_accuracy_pct", "script_only_issues",
                "genuine_content_mismatches", "odia_accuracy_pct", "english_accuracy_pct",
            ]].style.background_gradient(
                subset=["readback_accuracy_pct"], cmap="RdYlGn", vmin=0, vmax=100),
            use_container_width=True, hide_index=True,
        )

        st.markdown("#### Genuine content mismatches — the prompt-refinement list")
        gcm = gt_comparison[gt_comparison["issue_type"].str.startswith("content mismatch", na=False)]
        field_filter = st.multiselect("Filter by field", sorted(gcm["field"].unique()), key="gtfilter")
        if field_filter:
            gcm = gcm[gcm["field"].isin(field_filter)]
        st.dataframe(
            gcm[[
                "deed_number", "field",
                "ground_truth_odia", "fresh_gemini_odia",
                "ground_truth_readback", "fresh_gemini_readback",
            ]].rename(columns={
                "ground_truth_odia": "Ground truth (Odia)",
                "fresh_gemini_odia": "Gemini (Odia)",
                "ground_truth_readback": "Ground truth readback (EN)",
                "fresh_gemini_readback": "Gemini readback (EN)",
            }),
            use_container_width=True, hide_index=True,
        )

        with st.expander("Script-only issues (content correct, wrong script — separate fix)"):
            script_df = gt_comparison[gt_comparison["issue_type"] == "script-only (content correct, wrong script)"]
            st.dataframe(
                script_df[["deed_number", "field", "ground_truth_odia", "fresh_gemini_odia",
                           "ground_truth_readback", "fresh_gemini_readback"]],
                use_container_width=True, hide_index=True,
            )
