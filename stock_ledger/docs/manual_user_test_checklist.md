# Stock — Manual User Test Checklist
Print this. Tick each box after you see the screen match “System should”.

Date: __________  Tester: __________  Site/Unit: __________

Legend: `[ ]` not done · `[x]` pass · `[F]` fail (write note)

---

## Before you start

| Check | Done |
|-------|------|
| You have a warehouse location and a kitchen location | [ ] |
| You have a product (e.g. potato) with unit kg | [ ] |
| Stock / balances screen opens | [ ] |
| Goods in / Transfer / Reconcile / Scan screens open | [ ] |

---

## A. Goods in (receive delivery)

### A1 — Receive without barcodes
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 1 | Create/select a lot for the product | Lot appears / can be chosen | [ ] |
| 2 | Goods in **100 kg** into **warehouse** (no labels) | Warehouse stock for that lot shows **100** | [ ] |
| 3 | Refresh / reopen balances | Still **100** — not doubled | [ ] |

### A2 — Receive 5 bags with labels
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 4 | Goods in **100 kg** and print **5 bags × 20 kg** | Warehouse shows **100** (not 200) | [ ] |
| 5 | Screen / print list shows **5** labels | Exactly 5 bags listed | [ ] |
| 6 | Scan one bag | Product, lot, **20 kg remaining**, warehouse | [ ] |
| 7 | Scan all 5 bags once each | All 5 work; none missing | [ ] |

### A3 — Mistakes on goods in
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 8 | Try goods in with **0** or blank qty | Error — stock unchanged | [ ] |
| 9 | Try print **6 bags × 20** on a **100 kg** receipt | Error — over-print blocked | [ ] |
| 10 | Submit same goods in twice (double tap Save) | Stock still correct once — **not** doubled | [ ] |
| 11 | Void one misprinted bag | Bag void; warehouse still **100** | [ ] |
| 12 | Try reprint after void | Blocked or clear message | [ ] |
| 13 | Reprint a good bag (printer jam) | Same barcode; stock unchanged | [ ] |

### A4 — Second delivery same product
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 14 | New lot, goods in **50 kg** | Two lots: e.g. 100 + 50, not mixed into one | [ ] |

**Section A notes:** _______________________________________________

---

## B. Goods transfer (warehouse → kitchen)

Start from: warehouse has stock from section A.

### B1 — Happy path
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 15 | Transfer **40 kg** of a lot warehouse → kitchen | Warehouse **−40**, kitchen **+40** | [ ] |
| 16 | Open kitchen stock / recipe stock screen | Kitchen already shows the **40** (no second scan-in) | [ ] |
| 17 | Transfer another **20 kg** same lot | Warehouse and kitchen both update again | [ ] |
| 18 | Partial pallet style: if you received **920**, move only **320** | Warehouse keeps remainder; kitchen gets **320** | [ ] |

### B2 — Mistakes on transfer
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 19 | Transfer more than warehouse has | Error — kitchen does not go up | [ ] |
| 20 | From and To same location | Error | [ ] |
| 21 | Pick wrong lot (same product, other batch) | Wrong lot moves — confirm lot/trace is clear on screen before Save | [ ] |
| 22 | Double tap Transfer | Only one move — not double | [ ] |

### B3 — Barcode after transfer (known behaviour)
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 23 | After transfer, scan a bag from that lot | Note what location it shows. Balances moved by lot; bag screen may still say warehouse — write what you see | [ ] |

What did scan show for location? _______________  Acceptable for now? Y / N

**Section B notes:** _______________________________________________

---

## C. Stock reconcile (physical count)

### C1 — Happy path
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 24 | Count warehouse lot lower than system (e.g. system 60, count **55**) | Stock becomes **55**; adjustment saved | [ ] |
| 25 | Count kitchen higher (e.g. system 40, count **42**) | Stock becomes **42** | [ ] |
| 26 | Enter count that matches system exactly | Message: nothing to adjust / blocked | [ ] |

### C2 — Mistakes on reconcile
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 27 | Typo huge count (e.g. **550** instead of **55**) | System will accept if you confirm — UI should make you notice | [ ] |
| 28 | Reconcile wrong location | Only that location changes — check before Save | [ ] |
| 29 | Reconcile wrong lot | Other lot unchanged | [ ] |
| 30 | Double tap Save on reconcile | Only one adjustment | [ ] |

### C3 — Barcode after reconcile (known behaviour)
| # | You do | System should | Tick |
|---|--------|---------------|------|
| 31 | After reducing lot stock, scan a bag | Lot balance vs bag remaining may disagree — write both numbers | [ ] |

Lot on screen: _____   Bag remaining: _____   OK for now? Y / N

**Section C notes:** _______________________________________________

---

## D. Scan / use stock (kitchen)

| # | You do | System should | Tick |
|---|--------|---------------|------|
| 32 | Scan bag, use **5 kg** | Bag shows **15** left (if was 20); stock down **5** | [ ] |
| 33 | Use rest of bag later | Bag fully used / consumed; remaining **0** | [ ] |
| 34 | Scan unknown / bad barcode | Clear error — no stock change | [ ] |
| 35 | Scan voided bag | Cannot use | [ ] |
| 36 | Try use more than left on bag | Error | [ ] |

**Section D notes:** _______________________________________________

---

## E. Full day (do once at the end)

| # | You do | System should | Tick |
|---|--------|---------------|------|
| 37 | Receive 5×20 bags → transfer some to kitchen → use some → end count | Numbers make sense: received ≈ warehouse + kitchen + used ± count fixes | [ ] |
| 38 | Ask someone to check audit / history for that lot | See receive, transfer, use, count in order | [ ] |

Write final numbers:

| Place | Qty |
|-------|-----|
| Warehouse | |
| Kitchen | |
| Used / consumed | |
| Count adjustments | |
| Original received | |

**Do they add up?** Y / N   If N, note: _______________

---

## Sign-off

| | |
|--|--|
| All **must** rows passed? | Y / N |
| Failures listed below? | Y / N |
| Tester signature | |
| Date | |

### Failures / bugs found
1. ________________________________________________
2. ________________________________________________
3. ________________________________________________

---

## Quick pass order (if short on time)

1. A1 + A2 (goods in + 5 bags on screen)  
2. B1 steps 15–16 (transfer shows in kitchen)  
3. D steps 32–33 (scan use)  
4. C1 steps 24–26 (reconcile)  
5. E step 37 (full day maths)
