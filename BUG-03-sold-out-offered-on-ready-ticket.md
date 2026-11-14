# BUG-03 — Sold out is offered and accepted on a Ready ticket

| Field | Value |
|---|---|
| **ID** | BUG-03 |
| **Requirement** | MEJ-REF-01 (Sold out on a line — only while New or Cooking), MEJ-TKT-02 (state model) |
| **Severity** | Major (S2) — a prepared, called order can be cancelled from a state the requirements protect |
| **Priority** | High (P2) — breaks the ticket state rule at a food-handling boundary |
| **Status** | Confirmed on the competition build, 14 Nov 2026 |
| **Reproducibility** | Always, on a Ready ticket |
| **Environment** | Chrome via Playwright, Windows 11; SUT `https://www.worldbesttester.com/sut/mejamarmar/`; team database |

## Summary

MEJ-REF-01 says the cook or manager presses **Sold out** on a ticket line "only while its ticket is New or Cooking; in another state it is **not offered**, and a request is refused: *'Ticket <number> is <state>: nothing on it can be marked sold out.'*"

On a **Ready** ticket the line still shows an enabled **Sold out** button. Pressing it opens the normal confirmation ("Mark 1 x … on ticket 053 sold out? …"), and confirming **accepts** the action: the ticket leaves the Ready column and its line is marked sold out. No refusal message appears.

## Preconditions

- A paid ticket in **Ready** at a stall (used: ticket **053**, Yong Tau Foo Auntie Swee, Table 2 · Laman, tray T-1058).
- Signed in to Dapur as that stall's cook or the manager (used: cook Chan Swee Lin).

## Steps to reproduce

1. Take a paid ticket through **Start cooking** and **Call number** so that it sits in the **Ready** column (Ready 1).
2. On the Ready ticket's line, press **Sold out**.
3. Confirm the dialog.

## Expected

- The **Sold out** control is not offered on a Ready ticket.
- If the action is requested anyway, it is refused: **"Ticket 053 is Ready: nothing on it can be marked sold out."** and nothing changes.

## Actual

- The line shows an enabled **Sold out** button (DOM: `disabled=false`, visible).
- Pressing it opens the standard confirmation; confirming proceeds — Ready goes from 1 to 0 and the ticket disappears from the kitchen display (the refund is also wrong — see BUG-04).

## Evidence (14 Nov 2026, MYT)

| Time | Observation |
|---|---|
| 11:18:46 | Ticket 053 in **Ready 1**; line has an enabled "Sold out" button (`disabled=false`) |
| 11:19:15 | Pressing it opens "Mark 1 x Yong tau foo with noodles on ticket 053 sold out? …" |
| 11:19:29 | Confirming is accepted: **Ready 0**, ticket 053 gone; no refusal message |

DOM check at 11:19: button `{text: "Sold out", disabled: false, ariaDisabled: null, hidden: false}` inside the Ready column.

## Impact

- The state table protects a Ready order from being pulled: the food is prepared and the number has been called. The app lets the stall cancel it anyway, bypassing the New/Cooking guard.
- Combined with BUG-04, the diner's money is not returned, so a Ready order can be cancelled with no refund.
- The refusal message required by MEJ-REF-01 is unreachable in this state, so the behaviour cannot be distinguished from a supported action.

## Suggested fix

Render the **Sold out** control only when the ticket is New or Cooking, and have the server reject the action otherwise with the exact MEJ-REF-01 message. Add a state-transition test for Sold out in each of New, Cooking, Ready, Collected and Refunded.
