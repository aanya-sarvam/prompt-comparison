"""
Deed Grounding — Old vs New Prompt Comparison Viewer
Run locally:  streamlit run app_v2.py
Expects these four files in the same folder (or upload from the sidebar):
  realtime_fields_v1.csv / realtime_summary_v1.json   (OLD prompt)
  realtime_fields.csv    / realtime_summary.json      (NEW prompt)
"""

import json
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Deed Grounding — Old vs New Prompt", layout="wide")

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


st.title("🧾 Deed Grounding — Old vs New Prompt")
st.caption("Same 10 deeds, same real-time Gemini test, only `prompt.py` changed between runs.")

with st.sidebar:
    st.header("Data source")
    st.caption("Defaults to the bundled files — upload to override.")
    old_csv_f = st.file_uploader("OLD realtime_fields.csv", type="csv", key="oc")
    old_json_f = st.file_uploader("OLD realtime_summary.json", type="json", key="oj")
    new_csv_f = st.file_uploader("NEW realtime_fields.csv", type="csv", key="nc")
    new_json_f = st.file_uploader("NEW realtime_summary.json", type="json", key="nj")

    old_df = load_csv(old_csv_f) if old_csv_f else load_csv("realtime_fields_v1.csv")
    new_df = load_csv(new_csv_f) if new_csv_f else load_csv("realtime_fields.csv")
    old_summary = load_json(old_json_f) if old_json_f else load_json("realtime_summary_v1.json")
    new_summary = load_json(new_json_f) if new_json_f else load_json("realtime_summary.json")

merged = old_df.merge(new_df, on=KEY, suffixes=("_old", "_new"), how="outer")
merged["flipped"] = merged["found_old"].astype(str) != merged["found_new"].astype(str)
merged["direction"] = ""
merged.loc[(merged["found_old"] == False) & (merged["found_new"] == True), "direction"] = "improved"
merged.loc[(merged["found_old"] == True) & (merged["found_new"] == False), "direction"] = "regressed"

# --- Canonical DB metadata value (should be identical across runs; coalesce
# and flag any real conflicts so data-quality issues surface instead of
# silently picking one side).
_ev_old = merged["english_value_old"].fillna("")
_ev_new = merged["english_value_new"].fillna("")
merged["metadata_value"] = _ev_old.where(_ev_old != "", _ev_new)
merged["metadata_conflict"] = (_ev_old != "") & (_ev_new != "") & (_ev_old != _ev_new)
merged["metadata_missing_one_side"] = (
    ((_ev_old == "") ^ (_ev_new == "")) & ~merged["metadata_conflict"]
)

tab_summary, tab_changes, tab_deeds, tab_metadata = st.tabs(
    ["📊 Field-level delta", "🔀 What actually flipped", "📄 Per-deed side-by-side", "🗂️ DB metadata"]
)

# --- Summary --------------------------------------------------------------
with tab_summary:
    op = old_summary.get("per_field", {})
    np_ = new_summary.get("per_field", {})
    fields = sorted(set(op) | set(np_))
    rows = []
    for f in fields:
        o = op.get(f, {}).get("pct")
        n = np_.get(f, {}).get("pct")
        d = round(n - o, 1) if isinstance(o, (int, float)) and isinstance(n, (int, float)) else None
        rows.append({"field": f, "old_pct": o, "new_pct": n, "delta": d})
    sdf = pd.DataFrame(rows).sort_values("delta", ascending=False, na_position="last")

    c1, c2, c3 = st.columns(3)
    c1.metric("Deeds tested", new_summary.get("n_results", "—"))
    improved_n = (sdf["delta"] > 0).sum()
    regressed_n = (sdf["delta"] < 0).sum()
    c2.metric("Fields improved", int(improved_n))
    c3.metric("Fields regressed", int(regressed_n))

    st.dataframe(
        sdf.style.background_gradient(subset=["delta"], cmap="RdYlGn", vmin=-20, vmax=20),
        use_container_width=True, hide_index=True,
    )
    st.bar_chart(sdf.set_index("field")[["old_pct", "new_pct"]])

# --- What flipped ----------------------------------------------------------
with tab_changes:
    flips = merged[merged["flipped"]].copy()
    st.subheader(f"{len(flips)} field-instances changed found-status between prompt versions")

    for direction, label, color in [("improved", "✅ Newly found (old prompt missed it)", "green"),
                                     ("regressed", "No longer found (old prompt reported it)", "orange")]:
        group = flips[flips["direction"] == direction]
        st.markdown(f"### {label} — {len(group)}")
        for _, r in group.iterrows():
            with st.container(border=True):
                st.markdown(f"**Deed `{r['reg_no']}` · field `{r['field_id']}`**")
                st.caption(f"Original metadata (GCS): **{r.get('metadata_value') or '—'}**")
                col1, col2 = st.columns(2)
                with col1:
                    st.caption("OLD prompt")
                    st.write("found:", r["found_old"])
                    st.write("value:", r.get("odia_text_old") or "—")
                    st.caption(r.get("notes_old") or "")
                with col2:
                    st.caption("NEW prompt")
                    st.write("found:", r["found_new"])
                    st.write("value:", r.get("odia_text_new") or "—")
                    st.caption(r.get("notes_new") or "")
    if flips.empty:
        st.write("No changes detected between the two uploaded runs.")

# --- Per-deed side by side ---------------------------------------------------
with tab_deeds:
    st.caption(
        "For each field: the **original metadata** (the value pulled from the deed's "
        "record and sent to Gemini as the grounding target) vs what the **old prompt** "
        "found on the page vs what the **new prompt** found."
    )
    reg_nos = sorted(merged["reg_no"].astype(str).unique().tolist())
    choice = st.selectbox("Select deed (reg_no)", reg_nos)
    ddf = merged[merged["reg_no"].astype(str) == choice].copy()

    view = ddf[[
        "field_id", "item_index", "attr", "metadata_value",
        "found_old", "odia_text_old", "confidence_old",
        "found_new", "odia_text_new", "confidence_new",
        "flipped",
    ]].rename(columns={
        "field_id": "Field",
        "item_index": "Item",
        "attr": "Attr",
        "metadata_value": "Original Metadata (GCS)",
        "found_old": "Old: Found",
        "odia_text_old": "Old: Transcribed",
        "confidence_old": "Old: Conf.",
        "found_new": "New: Found",
        "odia_text_new": "New: Transcribed",
        "confidence_new": "New: Conf.",
        "flipped": "Flipped",
    })

    def hl(row):
        styles = [""] * len(row)
        if row["Flipped"]:
            styles = ["background-color: #fff3b0"] * len(row)
        return styles

    st.dataframe(
        view.style.apply(hl, axis=1),
        use_container_width=True, hide_index=True,
    )

# --- DB metadata quality tab -------------------------------------------------
with tab_metadata:
    st.subheader("DB metadata value consistency across the two CSV exports")
    st.caption(
        "The 'english_value' (DB metadata target) should be identical for the same "
        "deed/field regardless of which prompt version was tested. Rows below flag "
        "where it wasn't — a data-export issue, not a prompt issue."
    )

    conflicts = merged[merged["metadata_conflict"]]
    missing = merged[merged["metadata_missing_one_side"]]

    c1, c2 = st.columns(2)
    c1.metric("Real conflicts (different non-empty values)", len(conflicts))
    c2.metric("Present in one export, blank in the other", len(missing))

    if not conflicts.empty:
        st.markdown("### ⚠️ Conflicting values")
        st.dataframe(
            conflicts[["reg_no", "field_id", "item_index", "attr",
                       "english_value_old", "english_value_new"]],
            use_container_width=True, hide_index=True,
        )

    if not missing.empty:
        st.markdown("### Missing on one side")
        st.dataframe(
            missing[["reg_no", "field_id", "item_index", "attr",
                     "english_value_old", "english_value_new"]],
            use_container_width=True, hide_index=True,
        )

    st.divider()
    st.markdown("### Full metadata reference (canonical, coalesced)")
    ref_cols = ["reg_no", "field_id", "item_index", "attr", "metadata_value"]
    st.dataframe(merged[ref_cols].sort_values(["reg_no", "field_id"]),
                 use_container_width=True, hide_index=True)
