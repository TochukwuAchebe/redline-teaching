# Accessibility and usability review

Carried out on the deployed app in Chrome, 22 October 2026, as a structured expert walkthrough. The reviewer built the product, so this is not a study with independent users. A session with ACME reviewers is the next step.

## Automated audit (axe-core 4.10, WCAG 2.2 A and AA plus best practice)

Re-run on the redesigned interface (v3), at 1440 px:

| Screen | Result |
|---|---|
| Welcome and sign in | 0 violations, 41 checks passed |
| Check a deck (upload and recent reviews) | 0 violations, 42 checks passed |
| Review a slide | 0 violations, 47 checks passed |
| Redesign suggestion | 0 violations, 48 checks passed |
| What changed (before and after) | 0 violations, 40 checks passed |
| Deck ready | 0 violations, 33 checks passed |

Re-run on 23 October 2026 on the current release (axe-core 4.10.2, WCAG 2.0/2.1 A and AA plus best practice, Chrome): sign in 0 violations (44 checks passed), upload and recent reviews 0 (42), review a slide with its redesign panel 0, a 33-slide review 0 (48 checks passed).

Fixed during the audit:
- Caption grey raised from 3.6:1 to 4.9:1 contrast.
- The screen-reader heading moved inside the header landmark.
- The finish screen given a level-one heading.

Severity is never shown by colour alone. Every pill and state carries its word: *Must fix*, *Should fix*, *Suggestion*, *Will be fixed*, *Fixed*, *On your to-do list*, *Rejected*.

## Keyboard

- Every control can be reached and operated by keyboard, and every one shows a visible focus ring.
- Focus order is logical: top bar, slide rail, slide, findings.
- Shortcuts: `J`/`K` change slide, `A` accepts, `R` rejects, `T` adds to the to-do list, `U` undoes, `?` shows help. Escape closes menus and help and returns focus to the control that opened them.
- Added: a **Skip to findings** link, so keyboard users don't have to tab through every slide thumbnail.
- Fixed: on compact cards a mouse click on Accept or Reject only selected the card. It now records the decision on the first click, while keyboard focus still selects the card.

## Screen readers

- **Landmarks:** header, "Slides" navigation, main slide area and "Findings" panel.
- **Labels:** every highlight box is labelled, e.g. "Finding 2: Colour not in ACME palette"; the slide image has alternative text.
- **Announcements:** confirmations and errors go through a permanent live region, so they are always announced.
- **Page titles:** each screen has its own title ("Sign in · Redline", "Review a deck · Redline", or the deck's name), so screen-reader users and browser tabs can tell screens apart.

## Layout, zoom and motion

- Checked at 375, 768 and 1440 pixels wide after the redesign, with no sideways scrolling; on stacked layouts the slide sizes from the screen width. Nothing scrolls sideways, and on stacked layouts the top bar stays pinned so the next action stays in reach. 200% browser zoom on a laptop gives a similar width to the 768 px check, but was not tested separately.
- Animations respect the system's reduced-motion setting.
- Text and background colours on the audited screens meet WCAG AA contrast (4.5:1 for text).

## Usability walkthrough: a first-time ACME reviewer

| Task | Outcome |
|---|---|
| Understand what the tool does from the first screen | Clear: one heading, four numbered steps and one drop zone |
| Upload a wrong file | Clear, specific messages for empty, corrupt and non-PowerPoint files; nothing is added to History |
| Find and understand an issue | Numbered box on the slide matches the numbered card; the card shows the change as a tracked edit |
| Decide quickly | One click on ✓ or ✕; repeated issues settle with "Accept all"; `U` undoes |
| Know what the tool didn't check | "What to check by eye" in the panel footer lists every guideline item that isn't automated |
| Apply fixes and confirm | Fixed, Will be fixed and Manual edit are distinct in colour and label; Before/After shows the change |
| Get help | "?" explains deciding and lists every shortcut |

**Improvements made from the walkthrough:**
- the first-click fix;
- the skip link;
- a clearer upload icon (an arrow, not a lone chevron);
- a by-eye list that now names every guideline item that isn't automated.

**Remaining:** a session with real ACME reviewers, and a screen-reader pass with NVDA or VoiceOver end to end.
