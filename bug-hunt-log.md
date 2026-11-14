# Meja Marmar — bug-hunt log, 14 Nov 2026

Session: 10:23–11:26 MYT · competition build `https://www.worldbesttester.com/sut/mejamarmar/` · Chrome (Playwright) on Windows 11 · shared team database.

Accounts used: demo diners **Nur Aisyah Rahman** and **Tan Wei Ling** (both were being edited from another session, so most evidence uses a private diner, **QA Tester / qa.tester141126@example.com / Test1234**, created during the session), and Dapur manager **Rosnah Abdullah**.

## Confirmed defects

| ID | Title | Requirement | Severity / Priority |
|---|---|---|---|
| [BUG-01](BUG-01-busy-stall-accepts-at-15.md) | A Busy stall still accepts a tray at exactly 15 open tickets (send and cash payment) | MEJ-STL-05 | Major (S2) / High (P2) |
| [BUG-02](BUG-02-no-tapau-dine-in-button.md) | My tray shows no Tapau / Dine in button on a line | MEJ-TRY-03/04 | Minor (S3) / Medium (P3) |
| [BUG-03](BUG-03-sold-out-offered-on-ready-ticket.md) | Sold out is offered and accepted on a Ready ticket | MEJ-REF-01, MEJ-TKT-02 | Major (S2) / High (P2) |
| [BUG-04](BUG-04-soldout-refund-is-zero.md) | A sold-out line refunds RM0.00 instead of its section total, and the money is not credited | MEJ-REF-02/04/05, MEJ-BAL-03, MEJ-TKT-04 | Major (S2) / High (P2) |
| [BUG-05](BUG-05-last-orders-not-enforced.md) | A tray is sent and paid after the stall's last orders | MEJ-CTR-05, MEJ-TRY-07, MEJ-CTR-06 | Major (S2) / High (P2) |

## Verified passes — do not re-file these

Everything below matched the FRS, with the exact strings and figures observed.

| # | Check | Requirement | Observed |
|---|---|---|---|
| 1 | Demo-account button fills **Email + Password** | MEJ-ACC-01 | Clicking "Nur Aisyah Rahman" / "Tan Wei Ling" / "Rosnah Abdullah" filled `nuraisyah.rahman@example.com` / `marmar2026`, etc. |
| 2 | Line quantity `0` refused, line unchanged | MEJ-TRY-01 | "Choose a quantity from 1 to 9." |
| 3 | Line quantity `10` refused, line unchanged | MEJ-TRY-01 | "You can have at most 9 of one dish on a line." |
| 4 | Note of 61 characters refused | MEJ-TRY-07 | "A note has at most 60 characters." |
| 5 | Note text is stored/shown as typed | MEJ-SEC-04 | `<b>hello</b> & "x" <i>y</i> 1234567890 abcdefghij` shown literally on Send tray (not rendered) |
| 6 | Table `0` and `121` refused | MEJ-CTR-03 | "Please enter a table number from 1 to 120."; field marked invalid |
| 7 | Out-of-service table refused | MEJ-CTR-02 | Table 40 → "Table 40 is out of service. Please choose another table."; table stayed 1 |
| 8 | Table name format | MEJ-CTR-01 | 105 → "Table 105 · Dewan Sejuk"; 1 → "Table 1 · Laman" |
| 9 | Table-service charge | MEJ-TOT-03 | At table 105: dine-in items 3.00 → service 0.30, tax 0.20, section 3.50; second stall 4.50 → 0.45, 0.30, 5.25; tray 8.75 |
| 10 | Self-collect has no service charge | MEJ-TOT-03 | At table 1: Items 1.50, tax 0.09, total 1.59; no Service charge row |
| 11 | Two-cup teh tarik cap | MEJ-TEH-03 | 3 cups → "2 at RM1.50; 1 at RM2.80" = RM5.80, tax RM0.35, total RM6.15 |
| 12 | Cash rounding | MEJ-PAY-02 | 1.59 → amount 1.60, "Rounding RM0.01"; 42.93 → 42.95, "Rounding RM0.02"; 10.39 → 10.40, "Rounding RM0.01" |
| 13 | Balance too low refused | MEJ-PAY-01 | "Your balance is RM0.00 and this tray is RM1.59. Top up at the cashier or pay cash." |
| 14 | Send checks table before stall hours | MEJ-TRY-07 | No table → "Please choose a table on My tray first." |
| 15 | Six open trays on a table | MEJ-CTR-04 | "Table 1 already has 6 open trays. Please choose another table." (before the stall-hours check) |
| 16 | Before-opening refusal | MEJ-CTR-05 | "Asam Laksa Mak Esah opens at 11.00 am." (10:35, stall opens 11.00) |
| 17 | Awaiting payment + pay code | MEJ-PAY-03 | "Pay RM1.60 at the cashier with code 7116."; four digits, shown large on the tray page |
| 18 | Edit tray returns tray to Draft | MEJ-PAY-05 | Confirmation → "Tray T-1055 is back on My tray. Send it again when it is ready." |
| 19 | Old pay code stops working | MEJ-PAY-05 | Code 7116 → "No tray is awaiting payment with code 7116." |
| 20 | Re-send gives a new code | MEJ-PAY-05 | New code 1687, amount unchanged 1.60 |
| 21 | Cash received validation | MEJ-PAY-04 | 1.61 → "Cash received must be at least RM1.60 and in steps of 5 sen." |
| 22 | Take payment + change | MEJ-PAY-04 | 2.00 against 1.60 → "Payment taken. Change RM0.40."; ticket created, New, "Waiting for the stall" |
| 23 | Cancel a New ticket | MEJ-REF-03/04 | "Cancelled, refunded RM1.59" (not RM1.60 — rounding not refunded); tray "Complete. Refunded: Kopi & Teh Pak Zainal RM1.59."; balance credited 1.59 |
| 24 | Create-account validation | MEJ-ACC-02 | Name with a digit, `qa.tester` (no @), `abcdefgh` (no digit), mismatched confirmation — each gave the exact FRS message; valid account created with "Welcome to Meja Marmar." |
| 25 | Teh tarik window length | MEJ-TEH-04 | 11:00–11:20 → "A teh tarik hour lasts from 30 minutes to 3 hours."; 11:00–11:29 saved |
| 26 | At most 4 teh tarik windows | MEJ-TEH-04 | 5th window → "There can be at most 4 teh tarik hours." |
| 27 | Busy refusal at 16 open tickets | MEJ-STL-05 | "Yong Tau Foo Auntie Swee is busy with 16 orders. Please try again in a few minutes." (contrast BUG-01 at 15) |
| 28 | Cook lands on their own kitchen | MEJ-DAP-01 | Signed in as Zainal Ariffin Musa → `/dapur/kitchen/1` (Kopi & Teh) |
| 29 | Cook cannot open another stall's kitchen | MEJ-DAP-01 | `/dapur/kitchen/2` → 403 "This kitchen belongs to another stall." |
| 30 | Cook cannot open Payments | MEJ-DAP-01 | 403 "This page is for the cashier and the centre manager." |
| 31 | Cook cannot open The centre | MEJ-DAP-01 | 403 "This page is for the centre manager." |
| 32 | Delete a teh tarik window | MEJ-TEH-04 | Confirmation → "The teh tarik hour has been deleted."; list back to 3 windows |
| 33 | Call number moves Cooking → Ready | MEJ-TKT-02 | Ticket 053 left Cooking, Ready 1; diner page showed "Ready: collect at stall 6" (Laman self-collect) |
| 34 | Manager lands on Today at Dapur's address | MEJ-DAP-01 | Signed in as Rosnah Abdullah → `/dapur/` (Today) |
| 35 | Mark available restores a dish | MEJ-STL-03 | Dish label "Sold out" removed; button returned to "Mark sold out" |
| 36 | Dish price not a 10-sen step refused | MEJ-DAP-03 | RM1.05 → "A dish costs from RM0.50 to RM99.90, in steps of 10 sen." |
| 37 | Dish name of 1 character refused | MEJ-DAP-03 | "A dish name has 2 to 60 characters." |
| 38 | Dish description of 121 characters refused | MEJ-DAP-03 | "A description has at most 120 characters." |
| 39 | Delete a dish used on a sent tray | MEJ-DAP-03 | "Trays use this dish. Deactivate it instead." |
| 40 | Place note of 31 characters refused | MEJ-DAP-04 | "A place note has at most 30 characters." |
| 41 | Duplicate table number refused | MEJ-DAP-04 | "Table 1 already exists." |
| 42 | Table number 0 and 121 refused | MEJ-DAP-04 | "Please enter a table number from 1 to 120." |
| 43 | Stall name of 1 character refused | MEJ-DAP-02 | "A stall name has 2 to 40 characters." |
| 44 | Stall hours outside the centre / under 1 hour refused | MEJ-DAP-02 | "A stall opens and closes inside the centre's hours, 7.00 am to 11.00 pm, and stays open at least 1 hour." |
| 45 | Delete a stall with dishes/tickets/cooks | MEJ-DAP-02 | "This stall has dishes, tickets or cooks. Deactivate it instead." |
| 46 | Own account cannot be deactivated or have its role changed | MEJ-DAP-06 | "You cannot deactivate, delete or change the role of your own account." |
| 47 | Staff login rules | MEJ-DAP-06 | 2 letters → "Choose a login of 3 to 20 small letters, a to z."; `rosnah` → "This login is taken."; Cook with no stall → "A cook must belong to a stall." |
| 48 | Cashier landing and menu | MEJ-DAP-01 | Signed in as Kavitha Raman → Payments; menu only Payments, Top-ups, Trays |
| 49 | Cashier denied a manager page | MEJ-DAP-01 | `/dapur/stalls` → 403 "This page is for the centre manager." |
| 50 | Cashier's tray page is read-only | MEJ-DAP-01 | No Sold out button; only the global Reset data |
| 51 | Top-up rules | MEJ-BAL-02 | RM15 → "Top-ups are made in steps of RM10.00."; balance RM493.96 + RM10 → "A balance can hold at most RM500.00. This diner cannot top up now." |
| 52 | Centre hours rules | MEJ-DAP-04 | 07:00–12:00 → "The centre stays open at least 6 hours."; 08:00–23:00 → "…Change Kopi & Teh Pak Zainal's hours first." |
| 53 | Manager can open any kitchen | MEJ-DAP-01 | `/dapur/kitchen/2` opened as manager |
| 54 | Deactivate / reactivate a stall | MEJ-DAP-02 | "Satay Bara Senja has been deactivated."; absent from the diner list and its page 404s; "has been activated." restores it |
| 55 | Place note in the diner's table name | MEJ-CTR-01 | Table 1 note "Window" → "Table 1 · Laman, Window"; cleared afterwards |
| 56 | Last-orders state wording | MEJ-CTR-05 | Stall page: "Last orders were at 11.50 am" (correct; enforcement is BUG-05) |

## Observations — check before reporting

1. **Platform credentials appear in the SUT sign-in form.** After signing in to World Best Tester, the Meja Marmar sign-in fields are prefilled with the WBT email and password. This is almost certainly the browser password manager sharing the `worldbesttester.com` origin, not the SUT; do not report it without reproducing in a clean browser profile with no saved passwords.
2. **A payment method is preselected** (balance when the balance covers the tray, cash when it does not). The FRS includes a "Please choose how you pay…" check; with a default always checked that state is unreachable. Ambiguous — UX choice or deviation; verify against the judges' reading before filing.
3. **Count wording** is correct ("1 dish", "2 dishes", "1 stall", "1 open ticket").
4. **Tabs:** the app is tested in its own tab; a diner and a staff member can be signed in in the same browser as the FRS allows.

## Not covered yet (next targets)

Five-stall cap and the 9-of-one-dish merge; Tapau containers and service-charge exclusion; Close for today; Start cooking and Hand over (Call number was exercised); Today's takings arithmetic; hall delete with tables and table delete used (both need destructive-action approval); Reset password, deactivating a diner or member of staff, and the deactivated-session behaviour; board split-flap, Pause, reduced motion and "Yours"; 1024–1440 px and 200 % zoom; keyboard/labels; password change and email case-insensitivity; dish price change applying only to later trays.

## Housekeeping for the shared database

- Created account **QA Tester** (`qa.tester141126@example.com`). Balance stayed **RM1.59** throughout.
- Trays: **T-1055** (Complete; cancelled ticket refunded 1.59), **T-1058** (Paid; ticket 053 sold out, refund recorded as RM0.00 — BUG-04), **T-1076** (Paid; ticket 056 sold out, refund RM0.00 — BUG-04; tray still reads Paid), **T-1085** (Paid 9.55 cash, ticket at Chicken Rice Ah Lan — BUG-05; hours restored afterwards).
- The sold-out action marked the dish **Yong tau foo with noodles** sold out; it was restored with **Mark available** at 11:25.
- Added and then deleted a 4th teh tarik window **11.00–11.29 am**; the list is back to the seeded three windows.
- Stall 10 (Satay Bara Senja) was deactivated and reactivated; stall 5 (Chicken Rice Ah Lan) closing time was set to 12:20 for BUG-05 and restored to **21:00**. Table 1's place note was set to "Window" and cleared.
- Reset data (Annex E) returns everything to the seeded state — do that before a clean retest of BUG-01, which needs stall 6 at exactly 15 open tickets.
- The demo diners Nur Aisyah and Tan Wei Ling were being edited by a second session during the hunt (an added Nasi lemak line, an emptied tray, an added Roti canai). Use one driver per account, or separate accounts, when retesting.
