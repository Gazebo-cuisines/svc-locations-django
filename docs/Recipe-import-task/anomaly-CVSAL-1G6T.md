# Anomaly report — CVSAL-1G6T

Pilot: Gazebo G&G Vegetable Samosa 100G × 6. **Draft only. PDF grams. No 0.80 yield.**

**Picks 19 Aug:** A2 tray **692 PKLEE001-13**. A3 sleeve **744 PKTHE006-03**. A4 tamarind **344 SPICE0-66**. A5 film still omitted.

| Source | Evidence |
|--------|----------|
| Mixer PDF | `harvi/1 LR/.../GFF003R VEGETABLE SAMOSA V.17.pdf` issue 17, 04.11.2021, total **186080 g**, spice trace GFF003R-S |
| Spice PDF | `harvi/2 SPICES/.../GFF003R-S VEGETABLE SAMOSA - SPICES V.14.pdf` issue 14, 23.11.2021, total **11040 g** |
| Steam potato PDF | `harvi/1 LR/.../GFF241R - Steamed Potato 10mm Diced V.2.pdf` issue 02, 17.12.2018, **10600 g** |
| Steam carrot PDF | `harvi/1 LR/.../GFF242R - Steamed Carrot 10mm Diced V.3.pdf` issue 03, frozen carrot added, **5000 g** |
| QAS | `GFF186MC V.6` SKUs `CVSAL-G12T & CVSAL-1G6T`, filling GFF003R 100 g, tray code changed |
| Pedro | live `tblproducttree` — used only when no PDF |

Pedro mixer/spice = **1.25× PDF**. Not applied. Logged for later plan yield.

---

### A1 Batch scale — evidence only
PDF 186080 vs Pedro 232600. Write PDF. Existing spice draft 708 already 11040.

### A2 Tray — picked **692**
QAS `PKESS001-09` id **681** (Enviropax 141×141×40) vs Pedro `PKLEE001-13` id **692** (Leeways 141×141). User: Pedro tray.

### A3 Sleeve — picked **744**
Pedro `GNG000-03` (95×263) **not in new DB**. Nearest `PKTHE006-03` id **744** Labels G&G Veg Samosa 95×268mm. Alternate 759 is 75×124 outer label.

### A4 Tamarind — keep **344**
PDF: “Tamarind Paste 320 g”. Draft line 6 = `SPICE0-66` id 344. Pedro `SAUCE0-24` is **THAI PANANG** in new DB (id 151) — never use. Concentrate is `SAUCE0-23` id 150.

### A5 Film — omitted
QAS/Pedro `PKKMP001-08` K-peel **240mm**. Missing. Near: 800 = 275mm K-peel; 799 = 240mm PET weld (wrong type). Not substituted.

### A6 Pastry unit
Pedro qty 1 **unit**. Product 7 `PASTRY-01` default unit is **grams**. Recipe line uses unit **1**.

### A7 Belt fill 80.2 g
Not on PDF. Pedro live. Written as Pedro, noted.

### A8 Box
**527** `OC0014` / alt `PKCON001-35`. Product unit is grams; recipe line qty 1 **unit**.

### A9 Spice dest
708 is Spice Room 15 → Low Risk 3 (not Mixers 84). Left as-is.

### A10 Pack yield 0.99
Not copied. `process_loss` = 1.0000.

---

## Units (`product_unit` live)

1 unit · 2 grams · 3 meters · 5 Kg · 6 Box · 7 Liter · 9001 Each · 9002 Case · 9003 Bag · 9004 Bottle · 9005 TRAY

Pedro 1/2/3 = these 1/2/3.
