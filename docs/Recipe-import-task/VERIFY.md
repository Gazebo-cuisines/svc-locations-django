# Verify one live-202 recipe (other agent)

You did **not** import this SKU. Re-extract evidence yourself. Fail closed.

Stock code: **(paste)**  
Rulebook: [RULEBOOK.md](RULEBOOK.md)

## You must

1. Open the current signed PDF(s) with `pypdf`. List every ingredient + grams + total.
2. Query live DB recipe lines for that tree (`recipe_component.quantity`).
3. Confirm **Qty == PDF grams** for every mix/spice/steam line that mapped to an existing code. Net/gross empty.
4. Confirm PDF total == sum of PDF lines. DB sum may be short where RM/pack was omitted (must be on the anomaly sheet asking the user for a catalogue code). Do **not** fail for those omitted lines.
5. Confirm the attached file on each stage is the **current** signed copy (not Obsolete).
6. Confirm pack: film not swapped to wrong type/width; sleeve/tray substitutions match the anomaly sheet.
7. Confirm notes are plain English. Timeline `actor_name` = System Admin. Version is **draft**.
8. Open `anomaly-<STOCKCODE>.xlsx` and check every tree line is listed.

## Return only

```
SKU: <code>
PDF: PASS | FAIL
DB grams: PASS | FAIL
Evidence files: PASS | FAIL
Pack substitutions: PASS | FAIL
Notes/stamp/draft: PASS | FAIL
Overall: PASS | FAIL
Fails: <bullet list, or none>
```

**FAIL** if any mapped gram is wrong, a mapped PDF line is missing, a new RM/pack was created, or an Obsolete file was used. Omitted missing RM/pack listed on the anomaly sheet is expected. Do not “almost pass”.
