# Stock Management Tool — what it is

**For managers only.** Fixes a wrong goods-in, goods-out, or transfer in one step.

Floor staff cannot use this. Grant the **Stock Management** admin area to a user who needs it.

---

## The problem it solves

Stock is **append-only**. You cannot edit or delete a ledger row. Today, fixing a mistake means several developer APIs — and reversed stickers still scan and still show on reports.

This tool gives managers one clear flow: **remove the bad transaction, then redo it correctly.**

---

## How to use it (4 steps)

1. **Find** — Scan the sticker (`E123`) or search for the transaction.
2. **Preview** — The system shows what will be undone (stock moves, linked picks, transfer other leg).
3. **Remove** — Enter a **reason** and confirm. One click does the full undo.
4. **Redo** — Go to the normal **goods-in** or **goods-out** screen and enter the correct transaction. New stickers print there.

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

## What it does not do

- **No hard delete** — rows stay for traceability and the hash chain.
- **No reprint** — new labels come from a normal receive/issue.
- **No fix inside production** — if stock was already used on the shop floor (MADE), remove is blocked. Void production first, then retry.

---

## Manager message after Remove

> Redo this on goods-in or goods-out. Bin the old stickers.

That is the whole workflow.
