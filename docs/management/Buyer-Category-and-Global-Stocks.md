# Buyer Category, Global Stocks, and Purchase Order Follow-up

**Gazebo Cloud · Management pack**  
**Date:** 17 September 2026  
**Status:** Proposed — not built yet  
**For:** Management

How to use this pack: read each section, look at the numbered picture, leave the evidence table empty. After the developer finishes, record your screen and paste the file or a link in that table.

---

## 1. Why we are doing this

Buyers and planners need a simple label on raw materials: **Key product**, **Other product**, or **Planner monitor**. Admin sets that label on the product. It can be changed later. Every change is kept in the product timeline.

Global Stocks should filter like the Products page (category, sub-category, sub-sub category), plus this new buyer label. The Location filter we already have stays.

People should tick which columns they see. If they hide Trace or Supplier, quantities add up. Unit 2 and Low Risk never mix.

Open purchase orders sit on the right of that report. When warehouse finishes goods-in for a PO, that PO leaves the report and the next one moves up.

Each night at **00:10** we email the list of POs still Ordered or Partial.

Planning Excel can optionally add one sheet that totals the same raw material across recipe levels.

We will not break what already works.

---

## Picture 1. The whole change, step by step

```mermaid
flowchart LR
  s1["1 Open a raw material in Product"]
  s2["2 Set Buyer category on Purchase Guide"]
  s3["3 Filter Global Stocks by category and buyer label"]
  s4["4 Tick which stock columns to show"]
  s5["5 Open purchase orders show on the right"]
  s6["6 At 00:10 email lists POs still open"]
  s1 --> s2 --> s3 --> s4 --> s5 --> s6
```

Planning Excel can also add one extra sheet that totals the same raw material.

---

## Picture 2. Who does what

```mermaid
flowchart TD
  w1["1 Admin sets Buyer category on each raw material"]
  w2["2 Buyer uses that label when placing orders later"]
  w3["3 Warehouse does goods-in. Finished POs leave the stock report"]
  w4["4 Planner can export a consolidated raw-material total"]
  w5["5 Management checks this pack and the screen recordings"]
  w1 --> w2 --> w3 --> w4 --> w5
```

---

## 2. Req 1 — Buyer category

**What it is.** Three labels on a raw material.

- **Key product** — used a lot, important every day
- **Other product** — not a key product
- **Planner monitor** — the production planner watches it and orders when needed

**Where it lives.** Product → Purchase → Guide. New row: Buyer category.

**Who uses it.** Admin sets it. Buyers use it later when placing orders.

It can be edited any time. The timeline records the old value, the new value, who, and when.

**What “done” looks like.** The dropdown saves. The timeline shows the change. Old Purchase Guide fields still work.

### Picture 3. How admin sets Buyer category

```mermaid
flowchart LR
  b1["1 Open the product"]
  b2["2 Go to Purchase, then Guide"]
  b3["3 New row: Buyer category"]
  b4["4 Pick Key, Other, or Planner monitor"]
  b5["5 Save"]
  b1 --> b2 --> b3 --> b4 --> b5
```

### Picture 4. How the audit log stores each change

```mermaid
flowchart LR
  a1["1 Admin changes Buyer category"]
  a2["2 System stores old value and new value"]
  a3["3 Timeline shows who, when, and what changed"]
  a4["4 Change it again later. Each change is a new log line"]
  a1 --> a2 --> a3 --> a4
```

### Evidence of outcome (after developer)

Leave blank until you record the screen.

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 3. Req 2 — Filters on Global Stocks

Add the same category menus as the Products page, plus Buyer category. Keep Location.

- Category → Sub-category → Sub-sub category
- Buyer category (the three labels from Req 1)
- Location stays (Unit 2, Low Risk, and other warehouse names)

Search, Item, Warning, and Hide zero stay as they are.

**What “done” looks like.** You can narrow the list by product tree, buyer label, and location, without losing the filters you already have.

### Picture 5. Filter order on Global Stocks

```mermaid
flowchart LR
  f1["1 Choose Category"]
  f2["2 Choose Sub-category"]
  f3["3 Choose Sub-sub category"]
  f4["4 Choose Buyer category"]
  f5["5 Keep using Location as today"]
  f1 --> f2 --> f3 --> f4 --> f5
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 4. Req 3 — Column chooser

The table can show many columns. You choose which ones are visible. Extra columns scroll to the right.

**What “done” looks like.** Right-click works. Hidden columns come back when you tick them again. The page scrolls sideways.

### Picture 6. Right-click the header

```mermaid
flowchart LR
  c1["1 Look at the stock table header"]
  c2["2 Right-click the header"]
  c3["3 Tick a column to show it, or untick to hide it"]
  c4["4 Scroll right to see extra columns such as purchase orders"]
  c1 --> c2 --> c3 --> c4
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 5. Req 3.1 — Add qty when a split column is hidden

Product code is the identity. Product name stays with it.

**Hard rule.** Locations never merge. Unit 2 and Low Risk stay separate even when Trace or Supplier is hidden.

**What “done” looks like.** Hide Trace or Supplier and qty adds inside the same location. Two warehouses stay two rows.

### Picture 7. Peas — hide Trace, add qty

**Before**

- Peas | Unit 2 | Trace A | 10 kg
- Peas | Unit 2 | Trace B | 8 kg
- Two rows. Same product. Same location.

**After**

- Peas | Unit 2 | 18 kg
- One row. Qty 10 + 8 = 18 kg.

```mermaid
flowchart LR
  p1["1 Right-click the header"]
  p2["2 Untick Trace number"]
  p3["3 Qty from those lots is added"]
  p4["4 Still one location: Unit 2"]
  p1 --> p2 --> p3 --> p4
```

### Picture 8. Salt — hide Supplier, add qty

**Before**

- Salt | Unit 2 | Supplier A | 5 kg
- Salt | Unit 2 | Supplier B | 5 kg
- Salt | Unit 2 | Supplier C | 5 kg
- Salt | Unit 2 | Supplier D | 5 kg

**After**

- Salt | Unit 2 | 20 kg
- One row. 5 + 5 + 5 + 5 = 20 kg.

```mermaid
flowchart LR
  t1["1 Right-click the header"]
  t2["2 Untick Supplier"]
  t3["3 Qty from those suppliers is added"]
  t4["4 Still one location: Unit 2"]
  t1 --> t2 --> t3 --> t4
```

### Picture 9. Do not mix locations

```mermaid
flowchart TD
  n1["1 Peas in Unit 2 stay on a Unit 2 row"]
  n2["2 Peas in Low Risk stay on a Low Risk row"]
  n3["3 Hiding Trace or Supplier only adds qty inside the same location"]
  n4["4 Grand total columns follow the same split"]
  n1 --> n2 --> n3 --> n4
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 6. Req 4 — Future purchase orders on the report

On the right of Global Stocks, each product row can show open POs. Soonest delivery first.

- Each PO slot: remaining qty, delivery date, PO number
- PO 1, then PO 2, then PO 3, as needed
- Only Ordered and Partial POs. Not draft, not cancelled, not fully received

**Goods-in rule.** If PO 43123 ordered 90 kg and warehouse received 40 kg, the row still shows that PO with 50 kg left. When the last 50 kg is received, that PO disappears and the next future PO moves into first place.

**What “done” looks like.** Open POs show. Remaining qty is correct. Finished POs leave. The next PO takes their place.

### Picture 10. How future POs appear on the row

```mermaid
flowchart LR
  o1["1 Find the product on Global Stocks example peas"]
  o2["2 Scroll right to the purchase order columns"]
  o3["3 PO 1 is the soonest open order: remaining qty, date, number"]
  o4["4 PO 2 and PO 3 sit next to it, in date order"]
  o1 --> o2 --> o3 --> o4
```

### Picture 11. Goods-in moves the list

```mermaid
flowchart TD
  g1["1 PO 43123 ordered 90 kg peas. It sits in PO 1"]
  g2["2 Warehouse receives 40 kg. Status is Partial. Qty shows 50 kg left"]
  g3["3 Warehouse receives the last 50 kg. That PO is finished"]
  g4["4 PO 43123 drops off the report"]
  g5["5 The next future PO slides into PO 1"]
  g1 --> g2 --> g3 --> g4 --> g5
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 7. Req 5 — Three quantity columns

- **Current stock qty** — already on the page
- **Future order stock qty** — total remaining qty on open POs for that row
- **Grand total** — current + future

Example. Peas: 100 kg in stock. 50 kg still on open POs. Grand total 150 kg.

**What “done” looks like.** The three numbers match stock plus remaining PO qty, and they follow the same location split as Req 3.1.

### Picture 12. How the three qty columns are built

```mermaid
flowchart LR
  q1["1 Current stock qty already on the page"]
  q2["2 Future order stock qty = remaining qty on open POs"]
  q3["3 Grand total = current stock + future order stock"]
  q1 --> q2 --> q3
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 8. Req 6 — Reminder email at 00:10

Same idea as the closing stock email: a short branded message and a file attached. Addresses are set in the app.

- Include: Ordered (nothing received yet) and Partial (some received, not finished)
- Exclude: Draft, Received, Cancelled
- Sort: newest PO date first (16 Sep, then 15 Sep, then 14 Sep)
- This is the full open list each night, not a “only new since yesterday” list

**What “done” looks like.** At 00:10 the named inboxes get the email. The list matches open POs in the system, newest first.

### Picture 13. Night job, newest PO date first

```mermaid
flowchart LR
  e1["1 Admin sets the email addresses in the app"]
  e2["2 Every night at 00:10 the job runs"]
  e3["3 It lists POs that are Ordered or Partial"]
  e4["4 Newest PO date is first"]
  e5["5 Email looks like closing stock. A file is attached"]
  e1 --> e2 --> e3 --> e4 --> e5
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 9. Req 7 — Planning Excel, consolidated raw material

Today the plan Excel lists each requirement on its own row. Peas at one level and peas at another level are two rows.

**What “done” looks like.** A popup asks if you want the consolidated sheet. If yes, peas 2 kg + 10 kg become 12 kg. Plan Requirements and Working do not change.

### Picture 14. Optional extra sheet. Old sheets stay

```mermaid
flowchart LR
  x1["1 Open a plan and click Export Excel"]
  x2["2 Popup asks: Consolidate req of raw material?"]
  x3["3 If you tick it, a new sheet is added"]
  x4["4 Peas 2 kg + 10 kg become Peas 12 kg"]
  x1 --> x2 --> x3 --> x4
```

### Evidence of outcome (after developer)

| Field | Fill in after the work is done |
| --- | --- |
| Date recorded | ________________________________ |
| Recorded by | ________________________________ |
| Screen recording / screenshot | ________________________________ |
| Pass / Fail | ________________________________ |
| Notes | ________________________________ |

---

## 10. What we will not break

### Picture 15. Existing working parts stay

```mermaid
flowchart TD
  k1["1 Purchase Guide fields that already exist still save the same way"]
  k2["2 Global Stocks live update, Location, Item, Warning and Hide zero stay"]
  k3["3 Default stock columns stay the first view"]
  k4["4 Planning Excel sheets Plan Requirements and Working stay"]
  k5["5 Closing stock email and its address list stay separate"]
  k1 --> k2 --> k3 --> k4 --> k5
```

- Purchase unit, format and version on Guide
- Product timeline (we only add the new field into it)
- Global Stocks live feed and current filters
- The nine columns you see today remain the default view
- Planning Excel sheets Plan Requirements and Working
- Closing stock email and its own address list

---

## 11. Sign-off

This pack describes the work. It is not built yet. Sign when the understanding is accepted. After build, fill the evidence tables with screen recordings.

| | |
| --- | --- |
| Prepared for management | Date: 17 Sep 2026 |
| Approved by (name / signature) | Date: ________ |
| Ready for developers to build | Yes / No: ________ |
| Evidence pack complete (after build) | Yes / No: ________ |

---

To get a real Word file (`.docx`) with pictures inside: switch this chat to Agent mode and say **generate word**. Plan mode cannot create `.docx` files.
