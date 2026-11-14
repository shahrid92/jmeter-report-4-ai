# BUG-02 — My tray shows no Tapau / Dine in button on a line

| Field | Value |
|---|---|
| **ID** | BUG-02 |
| **Requirement** | MEJ-TRY-03 / MEJ-TRY-04 (a line moves by drag, by keyboard, **or with the line's Tapau or Dine in button**) |
| **Severity** | Minor (S3) — the move is still possible by drag or Space; this is a missing affordance, not lost data |
| **Priority** | Medium (P3) — affects discoverability and pointer-only users; keyboard users can use Space |
| **Status** | Confirmed on the competition build, 14 Nov 2026 |
| **Reproducibility** | Always |
| **Environment** | Chrome via Playwright, Windows 11; SUT `https://www.worldbesttester.com/sut/mejamarmar/`; team database |

## Summary

MEJ-TRY-03/04 says every line on **My tray** moves between the marble table and the tapau bag "by dragging its dish, by the keyboard (Space picks it up, Space on the bag or table drops it) **or with the line's Tapau or Dine in button**".

The line has no such button. Its Dine in / Tapau choice is a static badge (`<span class="badge">Dine in</span>`). A DOM query of every `button`, `a`, `input`, `select` and `[role=button]` on the page finds no control whose text or label mentions Tapau or Dine in — the only line controls are the draggable dish button, **Update**, **Remove**, **Save note** and the suggestion chips.

## Preconditions

Signed in as any diner (`QA Tester` / `qa.tester141126@example.com` / `Test1234` used), with at least one dish on the tray.

## Steps to reproduce

1. Add any dish to the tray (e.g. **Yong tau foo with noodles**, RM9.80).
2. Open **My tray**.
3. Look at the line under the marble table (or, after a drag, under the tapau bag).

## Expected

Each line offers a button that moves it to the other choice — **Tapau** on a Dine-in line, **Dine in** on a Tapau line (as the third move method, alongside drag and Space).

## Actual

No such button exists. "Dine in" is a read-only badge next to the unit price. The line can only be moved by dragging the dish or by the Space-key route.

Evidence: `evidence/tray-line-no-tapau-button.png`; DOM check (14 Nov, 10:41 MYT) returned no interactive element matching `/tapau|dine in/i`.

## Impact

- A diner using a mouse must discover drag; a click-only user has no visible way to change Dine in ↔ Tapau.
- The FRS's explicit third control is absent, so the tray's Dine in/Tapau workflow is incomplete as specified.
- Not reported as an enhancement: the button is named in the requirement, not a suggestion.

## Suggested fix

Render a per-line button (`Tapau` when the line is Dine in, `Dine in` when it is Tapau) that submits the same move the drag and Space handlers use, including the merge and note-conflict rules (MEJ-TRY-05/06).
