# BUG-04 — A sold-out line refunds RM0.00 instead of its section total, and the money is not credited

| Field | Value |
|---|---|
| **ID** | BUG-04 |
| **Requirement** | MEJ-REF-02 (sold-out refund in full, service charge and tax included), MEJ-REF-04 (exact figures), MEJ-REF-05 (where refunds show), MEJ-BAL-03, MEJ-TKT-04 (Complete) |
| **Severity** | Major (S2) — the diner loses the full price of a sold-out line; the tray and balance history contradict the line |
| **Priority** | High (P2) — every sold-out line is affected; workaround is to cancel the ticket before the stall acts |
| **Status** | Confirmed on the competition build, 14 Nov 2026, on two tickets |
| **Reproducibility** | Always (New and Ready tickets tested) |
| **Environment** | Chrome via Playwright, Windows 11; SUT `https://www.worldbesttester.com/sut/mejamarmar/`; team database |

## Summary

When a ticket line is marked **Sold out**, MEJ-REF-02 requires the line to be "refunded in full, service charge and tax included: the section total before, less the section total again without the line", credited to the balance (MEJ-BAL-03), and shown as `Sold out, refunded <amount>`; when every line is sold out the ticket is Refunded and the tray becomes Complete (MEJ-TKT-04).

The app marks the ticket line **"Sold out, refunded RM0.00"** while its section total is **RM10.39**. No refund appears on the Balance history, the balance is unchanged, the tray's Refunds panel says **"Nothing on this tray has been refunded."**, and the tray stays **Paid** instead of Complete.

Reproduced twice:
- ticket **053** (T-1058, Ready at the time) — section RM10.39;
- ticket **056** (T-1076, New) — section RM10.39.

In both cases the diner paid RM10.40 in cash and received nothing back; the QA Tester balance stayed at **RM1.59** (only the earlier ticket-cancellation refund).

## Preconditions

- Signed in as a diner with a paid tray of one line (QA Tester, `qa.tester141126@example.com` / `Test1234`).
- Signed in to Dapur as the stall's cook or the manager (used: manager Rosnah Abdullah).
- A ticket in **New** (or Cooking) at a stall with the dish available.

## Steps to reproduce (New-path)

1. Add **1 × Yong tau foo with noodles (RM9.80)** from **Yong Tau Foo Auntie Swee**, table **2 · Laman**, and send it with **Pay cash at the cashier**. The tray total is RM10.39, amount to pay **RM10.40**.
2. As the manager, **Dapur → Payments**, find the pay code, **Take payment** with **Cash received 10.40**.
3. As the stall's cook or the manager, open **Dapur → Kitchen → Yong Tau Foo Auntie Swee**.
4. On the ticket's line, press **Sold out** and confirm.
5. Open the diner's tray page and the **Balance** page.

## Expected

- Ticket line: **"Sold out, refunded RM10.39"** (the section total, service charge and tax included, not the RM10.40 cash amount — MEJ-REF-04).
- The ticket becomes **Refunded**; the tray becomes **Complete**, with **"Complete. Refunded: Yong Tau Foo Auntie Swee RM10.39."** (MEJ-TKT-04).
- **Balance** rises by RM10.39 (from RM1.59 to RM12.98) and the history shows a **Refund** for T-1076 with its time and amount (MEJ-REF-05).

## Actual

- Ticket line: **"Sold out, refunded RM0.00"**; the section still shows Items RM9.80, Service tax RM0.59, Section total RM10.39.
- Refunds panel: **"Nothing on this tray has been refunded."**
- Tray state: still **"Paid"** — not Complete, no refund summary.
- Balance: unchanged **RM1.59**; no refund row for T-1076 (only "Refund, T-1055, ticket 218 cancelled RM1.59").
- The ticket does leave the kitchen display (New 3 → 2), so the order is gone from the stall.

## Evidence (14 Nov 2026, MYT)

| Time | Observation |
|---|---|
| 11:22:37 | T-1076 sent: "Pay RM10.40 at the cashier with code 2415." |
| 11:23:28 | Manager takes payment 10.40: "Payment taken. Change RM0.00." |
| 11:23:57 | Ticket 056 marked Sold out and confirmed; leaves the New column |
| 11:24:04 | Tray T-1076: "Sold out, refunded RM0.00"; section total RM10.39; Refunds "Nothing on this tray has been refunded."; tray "Paid" |
| 11:24:12 | Balance RM1.59 — unchanged |

Screenshots: `evidence/tray-T1076-soldout-refunded-0.png`, `evidence/balance-unchanged-after-soldout.png`.

The earlier Ready-path reproduction is in BUG-03: ticket 053, T-1058, also "Sold out, refunded RM0.00".

## Notes for the retest

- Reset data first to clear the test trays. Use a fresh diner or a tray paid by cash; a balance payment would also work if the balance covers it.
- Do not confuse this with **Cancel this ticket**, which refunds correctly (T-1055: "Cancelled, refunded RM1.59", tray "Complete. Refunded: Kopi & Teh Pak Zainal RM1.59."). The defect is specific to the **Sold out** action.
- Keep the line's section total visible in the evidence: the expected refund is that figure, not the cash amount to pay.

## Suggested fix

Compute the refund as MEJ-REF-02 states — section total before minus the section total recalculated without the line, with every other line at its sent price — then credit it to the balance, write the balance-history and tray refund records, and re-evaluate MEJ-TKT-04 so the tray becomes Complete with its refund summary. Add tests for a one-line ticket and a multi-line ticket, in New and Cooking.
