# Stock Management Tool — what it is

**For managers only.** Fixes a wrong goods-in, goods-out, or transfer in one step.

Floor staff cannot use this. Grant the **Stock Management** admin area to a user who needs it.

---

## The problem it solves

Stock is **append-only**. You cannot edit or delete a ledger row. Today, fixing a mistake means several developer APIs — and reversed stickers still scan and still show on reports.

This tool gives managers one clear flow: **remove the bad transaction, then redo it correctly.**

Connected picks (goods-out from a sticker) are undone **in the same Remove** — you do not reverse each row by hand.

---

## How to use it (4 steps)

1. **Find** — Scan the sticker (`E123`) or search for the transaction.
2. **Preview** — Read **confirmation lines** (what will be undone, in order) and the **redo checklist**.
3. **Remove** — Enter a **reason** and confirm. One click does the full undo.
4. **Complete the checklist** — Redo each step on normal goods-in / goods-out. New stickers print there.

**Bin the old stickers.** They are void and must not be used.

---

## What Remove does

| Situation | What happens |
|-----------|--------------|
| Not yet posted (still in queue) | Cancels it. Sticker void. |
| Posted receipt / issue / transfer | Reverses stock. Voids stickers and unit labels. |
| Transfer | Both legs reversed together. |
| Receipt already picked | Picks reversed first, then the receipt. |
| Already removed | Safe to call again — no double undo. |

**Hidden from ops:** void stickers fail on scan. Goods-in/out reports skip removed rows. Closing stock already nets to zero.

**Still in audit:** Full history stays on the audit timeline with reason, who, and when.

---

## Worked example — received 19 boxes, meant 90; already issued 5

Do **not** reverse the 5-box out and the 19-box in separately.

1. Open Stock Management on the **goods-in** sticker (`E…` for the 19).
2. Preview shows (example):
   - Reverse goods-out `E456` — 5 Box (from this sticker)
   - Reverse goods-in `E123` — 19 Box
   - Void sticker `E123` …
3. Confirm Remove with a reason.
4. Checklist after remove:
   - Re-issue **5** from the **new** sticker (if that pick was real)
   - Receive the **correct** qty (**90**) on goods-in — new sticker prints
   - Bin old stickers
5. Traceability: audit still shows the mistake + removals; new `E{id}` is the live barcode.

---

## What it does not do

- **No hard delete** — rows stay for traceability and the hash chain.
- **No reprint** — new labels come from a normal receive/issue.
- **No auto-recreate** — you redo on goods-in/out using the checklist.
- **No fix inside production** — if stock was already used on the shop floor (MADE), remove is blocked. Void production first, then retry.

---

## Can you bring back a removed transaction?

**No.** There is no "undo remove" API.

Remove cancels or reverses the row and **voids the stickers**. That cannot be rolled back in one click.

| After remove | Can you undo? |
|--------------|---------------|
| Cancelled (unposted) | No — posting stays cancelled |
| Reversed (posted) | No — each row reverses only once |
| Void stickers (`E{id}`) | No — old labels must be binned |

**If remove was a mistake:** redo the correct transaction on normal **goods-in** or **goods-out**. You get a **new entry and new sticker**. Do not reuse the old one.

The full history (original, reversal, reason, who, when) stays on the **audit timeline** for investigation — but ops screens treat removed rows as gone.

---

## Manager message after Remove

> Removed. Complete the checklist, then bin old stickers.

Show `redo_todos` from the API as an unchecked list until the manager finishes each step.

That is the whole workflow.
