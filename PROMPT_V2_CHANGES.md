# Prompt v1 → v2: changes and results

Three targeted edits to `prompt.py` → `prompt_v2.py`. Nothing else in the
prompt was touched (63-line diff, see `prompt_v1_to_v2.diff`). Each change maps
to a specific problem seen in the batch-1 missing-field investigation and the
client feedback ("property details are usually there"; buyer/seller **address**
and **plot boundary** are the fields most often "not found").

---

## 1. Self-check rule — fixes the write-through / conflict bug

**Was (v1):** the self-check told Gemini that if its Latin readback disagreed
with `english_value`, it "likely located the wrong text → re-scan, fix it, or
mark not found. Do not report text whose readback contradicts the English."

**Problem:** this is the vertex batch's whole *purpose* inverted. The batch
exists to catch deeds where the scanned document disagrees with the IGR
database. When Gemini correctly read a *conflicting* value (page says plot 2861,
IGR says 2661), the old rule told it to **blank the field** — so the reviewer
saw an empty box ("nothing found") instead of the real conflicting value. This
was the `value_in_note_not_extracted` bucket (~6%+, likely undercounted): the
value sat in `notes` but never made it into `odia_text`.

**Now (v2):** the rule splits a readback mismatch into two cases:
- **(a) wrong location** — really read the wrong row/person/plot → re-scan or
  mark not found (unchanged behaviour).
- **(b) right location, conflicting value** — confident the row/label/position
  is correct but the value genuinely differs from `english_value` → **keep the
  value**: `found: true`, keep `odia_text` verbatim, honest `latin_readback`
  (do *not* force it to match the English), lower `confidence`, and describe the
  conflict in `notes`. Explicit instruction: never use `notes` as a substitute
  for the field value; only mark `found: false` when the location is truly
  illegible/absent, never merely because it disagrees with the database.

## 2. Address extraction robustness

**Was (v1):** no address-specific guidance. `buyer_details.address` (101
missing) and `seller_details.address` (90 missing) were dominated by
`value_absent_on_page` — often really a reading miss where only the first line
was captured.

**Now (v2):** explicit rule for `attr:"address"` — an address is a multi-part
block (village/town, PO, PS/Thana, District, sometimes a house/plot no.),
usually a small block or comma/dash-separated run **next to or just below** the
party's name and **often spanning more than one line**. Read the whole block,
concatenate it in the page's own script, transcribe the **legible part** rather
than blanking the whole field if only part is readable, and match it to the
correct party (not another party or the property location).

## 3. Plot boundary as a first-class list field

**Was (v1):** boundary was never a documented extraction target. In batch-1 the
boundary text sat inside the `propertyDetails` composite string
("Boundary : NAME (North) …") but the LIST FIELDS section only named
village/khata/plot/area as sub-values to pull — so boundary sides were
inconsistently extracted.

**Now (v2):** `property_boundary` is documented as a boundary_list field with
attrs `boundary_north/south/east/west`, and the sub-value extraction list and
attr enum are extended to include them. Guidance notes each side's value may be
a **person's name, a landmark (road/tank/canal), another plot, or a place name**
— transcribe whatever is written, don't assume it's a name.

---

## Measured results — 10 expert-validated deeds, v1 output vs a fresh v2 run

Scored against the experts' corrected Odia values (`odia_value`), with
Odia-aware date/number/currency/spelling folding. "Coverage" = produced a value
where the expert had one (directly the client's "not found" complaint); "Match"
= value agrees with the expert.

| Field group   | Coverage v1 → v2 | Match (recall) v1 → v2 |
|---------------|------------------|------------------------|
| Scalar (8)    | 100% → 100%      | 82.3% → 77.2%          |
| Party (seller/buyer) | 80.0% → 86.7% | 36.9% → 40.5%     |
| Property      | 61.5% → 84.6%    | 44.5% → 57.7%          |
| **ALL (204)** | **81.9% → 91.2%**| **56.9% → 60.2%**      |

- **Coverage +9.3 pp overall, property +23 pp** — the headline win, and exactly
  the client's complaint ("not found"). Half the fields v1 left blank are now
  filled (missing fields 37 → 18).
- **Property recall +13 pp**, driven by boundary sides now being extracted and
  by the write-through fix surfacing conflicting plot/khata values.
- **Scalar recall −5 pp** is the one regression, and it is almost entirely
  *formatting*, not content: v2 sometimes writes a date in a different valid
  format than the expert typed, or transcribes the on-page Odia `deed_type` term
  where the expert kept the English category. These show as "❌ differs" in the
  viewer but are not genuine extraction errors; a stricter date/deed_type folding
  pass would recover most of them.

Net on the validated set: 18 fields improved (recall), 13 changed for the worse
(mostly the scalar formatting cases above). The trade is strongly favourable on
the metric the client actually raised.

### Caveat
This is n=10 deeds, all but one `SALE IMMOVABLE`. Treat the numbers as
directional, not final. A larger, deed-type-stratified run is the right next
step before shipping v2 to the batch — and a boundary-specific pass, since these
10 deeds carry only a handful of boundary values.
