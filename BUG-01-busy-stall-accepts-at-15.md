# BUG-01 — A Busy stall still accepts a tray at exactly 15 open tickets

| Field | Value |
|---|---|
| **ID** | BUG-01 |
| **Requirement** | MEJ-STL-05 (Busy); send checks per MEJ-TRY-07 / MEJ-CTR-06 |
| **Severity** | Major (S2) — a business rule that protects a stall from overload is not enforced at its boundary; cash is taken and a ticket is created |
| **Priority** | High (P2) — core ordering rule broken; workaround exists (stall blocks at 16+) |
| **Status** | Confirmed on the competition build, 14 Nov 2026 |
| **Reproducibility** | Always, while the stall has exactly 15 open tickets |
| **Environment** | Chrome via Playwright, Windows 11; SUT `https://www.worldbesttester.com/sut/mejamarmar/`; Malaysian time; team database |

## Summary

MEJ-STL-05 says a stall with **15 or more** open tickets (New, Cooking or Ready) is Busy and that "a tray holding them can be neither sent nor paid at the cashier while it is Busy", with the message:

> `<stall> is busy with <n> orders. Please try again in a few minutes.`

At exactly **15** open tickets the app labels the stall **Busy** (tile, page, Dapur Today) but still allows the tray to be sent and the cash payment to be taken. At **16** both are correctly refused. The guard is off by one: it behaves as `open_tickets > 15` instead of `open_tickets >= 15`.

## Preconditions

- Signed in as a diner (used: `QA Tester` / `qa.tester141126@example.com`, password `Test1234`).
- Yong Tau Foo Auntie Swee (stall 6) has **exactly 15** open tickets. Reset data (Annex E) restores the seeded state; verify on **Dapur → Today**: the row shows **Busy** with New 10, Cooking 3, Ready 2, total **15**.
- A table with fewer than 6 open trays (used: **Table 2 · Laman**).

## Steps to reproduce

1. As the diner, open **Stalls → Yong Tau Foo Auntie Swee** and add **1 × Yong tau foo with noodles (RM9.80)**, Dine in. *(The dish may be added while Busy — that part is correct.)*
2. Open **My tray**, enter **Table 2**, press **Use this table**.
3. Open **Send tray**, choose **Pay cash at the cashier**, press **Send tray**.
4. As the manager, open **Dapur → Payments**, find the code shown on the tray page (e.g. **3873**), open **Take payment**, enter **Cash received 10.40**, press **Take payment**.

## Expected

- Step 3: nothing is sent; the page shows **"Yong Tau Foo Auntie Swee is busy with 15 orders. Please try again in a few minutes."**
- Step 4: payment is refused on the re-check (MEJ-CTR-06) with the same Busy message.

## Actual

- Step 3: the tray **is sent** and becomes **Awaiting payment** — e.g. tray **T-1058**, *"Pay RM10.40 at the cashier with code 3873."*
- Step 4: **"Payment taken. Change RM0.00."** — the tray becomes **Paid** and a ticket is created at the Busy stall (ticket state "Waiting for the stall").

## Evidence (14 Nov 2026, MYT)

| Time | Observation |
|---|---|
| 10:36:41 | Stall 6 page/tile shows **Busy**; stall list shows **Busy** |
| 10:37:04 | Tray **T-1058** accepted: *"Pay RM10.40 at the cashier with code 3873."* |
| 10:37:25 | Dapur Today, stall 6: **Busy**, New 10 + Cooking 3 + Ready 2 = **15** |
| 10:37:48 | Take payment accepted: *"Payment taken. Change RM0.00."* |
| 10:38:00 | Dapur Today, stall 6: New 11 + Cooking 3 + Ready 2 = **16** (our ticket added) |
| 10:38:49 | A second tray with the same dish is refused: *"Yong Tau Foo Auntie Swee is busy with 16 orders. Please try again in a few minutes."* |

Screenshots: `evidence/tray-T1058-paid-while-stall6-busy.png`, `evidence/dapur-today-stall6-busy-16.png`, `evidence/send-refused-at-16-orders.png`.

The 15 → 16 step change is the proof: the same action is refused at 16 but accepted at 15, while the UI calls the stall Busy at both counts.

## Notes for the retest

- The team database is shared, so another tester can collect or add tickets between steps. Check the count on **Dapur → Today** immediately before sending and again after taking payment; the defect only shows at exactly 15.
- The tray used in evidence (T-1058) is already Paid, so it now contributes one open ticket. A reset restores the seeded 15.
- Keep it one defect: send and Take payment fail for the same reason (the Busy guard excludes 15). If the two checks are fixed independently, split the report.

## Suggested fix (for the developer)

Treat Busy as `open_tickets >= 15` in **both** the Send tray validation and the Take payment re-check, using the same helper that renders the "Busy" label. Add a boundary test at 14, 15 and 16 open tickets.
