# Prompt v1 vs v2 — 4-way comparison viewer

Streamlit app comparing, per deed and per field:

**Original IGR metadata · Prompt 1 (prompt.py) · Expert-corrected · Prompt 2 (prompt_v2.py)**

## Run

```bash
pip install -r requirements.txt
streamlit run app_compare4.py
```

The three data files are bundled, so it runs out of the box. Use the sidebar to
upload your own to override.

## Files

| File | What it is |
|------|-----------|
| `app_compare4.py` | Streamlit UI (deed selector, 4 columns, verdicts, summary) |
| `reconcile4.py` | Builds the 4-way comparison from the three JSON files |
| `diff_logic.py` | Odia-aware matching (dates/numbers/currency/spelling/script folding) — reused from the original comparison repo |
| `vertex_10_corrected.json` | DB export: 10 expert-validated deeds (`odia_value` = ground truth) |
| `old_outputs_10.jsonl` | Prompt 1 output (subset of `grounding_results_batch1.jsonl`) |
| `new_outputs.json` | Prompt 2 output (fresh `prompt_v2.py` Gemini run) |
| `prompt.py`, `prompt_v2.py` | The two prompts being compared |
| `prompt_v1_to_v2.diff` | Exact diff between them |
| `PROMPT_V2_CHANGES.md` | What changed and why, with the measured results |

## Verdict legend

- ✅ **match** — agrees with the expert value (after Odia-aware folding)
- ❌ **differs** — genuine content difference from the expert
- ⬜ **not found** — expert entered a value, the prompt returned blank
- ➕ **extra** — prompt found something the expert left blank
- ≈ **category** — `deed_type` only: English label vs on-page Odia term (expected)

## Regenerating the data (on a machine with GCS + Vertex access)

1. `export_vertex_samples.py` → `vertex_10_corrected.json` (DB export)
2. `download_images.ps1` → pull the 10 deeds' page images from GCS
3. `run_prompt_v2.py` → fresh `prompt_v2.py` Gemini run → `new_outputs.json`
4. Filter `grounding_results_batch1.jsonl` to the 10 deeds → `old_outputs_10.jsonl`
5. `score_ab.py` → headline scorecard (coverage / recall, fixed / regressed)
