# BUG-05 — A tray is sent and paid after the stall's last orders

| Field | Value |
|---|---|
| **ID** | BUG-05 |
| **Requirement** | MEJ-CTR-05 (last orders), MEJ-TRY-07 (send checks), MEJ-CTR-06 (Take payment re-check) |
| **Severity** | Major (S2) — an order is accepted and cash taken for a stall that has stopped taking orders |
| **Priority** | High (P2) — every stall is affected for 30 minutes before closing, and for the rest of the day after closing |
| **Status** | Confirmed on the competition build, 14 Nov 2026 |
| **Reproducibility** | Always, once the clock is past a stall's last orders (closing time − 30 minutes) |
| **Environment** | Chrome via Playwright, Windows 11; SUT `https://www.worldbesttester.com/sut/mejamarmar/`; Malaysian time; team database |

## Summary

MEJ-CTR-05: from a stall's **last orders** (30 minutes before closing) until midnight, its closing time included, "a tray holding its dishes cannot be sent" with:

> `Last orders at <stall> were at <time>.`

The app shows the correct state — the stall page says **"Last orders were at 11.50 am"** — but it still **sends** a tray containing that stall's dish and still **takes the cash payment**, creating a ticket after the stall stopped taking orders.

## Preconditions

- Manager access to Dapur.
- A stall whose last orders have passed but whose closing time has not: used **Chicken Rice Ah Lan**, opening **07:00**, closing **12:20** → last orders **11:50**. Test time **11:53–11:54**.
- A diner signed in (QA Tester), a table with fewer than 6 open trays (used Table 3).

## Steps to reproduce

1. As the manager, set Chicken Rice Ah Lan's hours to **07:00–12:20** and save. Its page now reads **"Last orders were at 11.50 am"** (checked at 11:53).
2. As the diner, open **Stalls → Chicken Rice Ah Lan**, add **1 × Roast chicken rice**, and note the page still says last orders were at 11.50 am.
3. On **My tray**, choose **Table 3**, open **Send tray**, choose **Pay cash at the cashier**, press **Send tray**.
4. As the manager, open **Dapur → Payments**, find the code, **Take payment** with the amount to pay.

## Expected

- Step 3: nothing is sent; the page shows **"Last orders at Chicken Rice Ah Lan were at 11.50 am."**
- Step 4: refused on the re-check (MEJ-CTR-06) with the same message.

## Actual

- Step 3 at **11:53**: tray **T-1085** is sent — *"Pay RM9.55 at the cashier with code 1686."*, **Awaiting payment**, "sent Sat 14 Nov 2026, 11.53 am".
- The stall page at **11:53:32** still reads **"Last orders were at 11.50 am"**.
- Step 3 repeated at **11:54** (Edit tray → Send tray): sent again, code 5257, "sent 11.54 am".
- Step 4 at **11:54:33**: **"Payment taken. Change RM0.00."** — the tray becomes Paid and a ticket is created at the stall.

## Evidence (14 Nov 2026, MYT)

| Time | Observation |
|---|---|
| 11:51:45 | Manager saves Chicken Rice Ah Lan closing 12:20 ("has been saved") |
| 11:51:55 | Stall page: "Last orders were at 11.50 am" |
| 11:53:22 | Tray T-1085 sent: code 1686, "sent 11.53 am" |
| 11:53:32 | Stall page still: "Last orders were at 11.50 am" |
| 11:54:04 | Edit tray → re-send accepted: code 5257, "sent 11.54 am" |
| 11:54:33 | Take payment 9.55 accepted: "Payment taken. Change RM0.00." |

## Notes for the retest

- The state wording is right; only the enforcement is wrong. Compare the stall page's sentence with the send result — they contradict each other.
- Set the hours yourself so the window is live: closing time = now + 30–40 minutes, opening time inside the centre's hours, at least 1 hour long. Revert the hours afterwards.
- The existing T-1085 is paid; reset data before a clean run.
- Related boundary, different rule: BUG-01 (Busy at exactly 15 open tickets). Both are "the check does not trip at the boundary the UI displays".

## Suggested fix

In the Send tray validation and the Take payment re-check, compare the clock against **last orders** (closing − 30 minutes) as well as closing, inclusive of the boundary minute, and return the exact MEJ-CTR-05 message. Add boundary tests at last orders − 1 minute, last orders, last orders + 1 minute, closing and closing + 1 minute.
