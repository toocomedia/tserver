# DESIGN.md

## Brand
Server control panel UI — minimal, technical, compact.

## Goal
Dark left rail, white content, pistachio accent. Strict alignment, flat visuals, no radius, no shadows.

---

## Colors
- background: #FFFFFF (white)
- surface: #F5F6F5 (very light gray)
- sidebar-bg: #0B0C0B (nearly black)
- text: #0F1720 (black)
- muted: #6B6F6A (gray)
- line: #E6E6DF (divider)
- accent-pistachio: #C7F464 (pistachio highlight)
- accent-pistachio-2: #A8E04A (darker pistachio)
- neutral-strong: #111111 (strong neutral)
- danger: #DC2626 (error)
Notes: Use only these colors. Accent only for primary actions and small highlights.

---

## Spacing (8px scale)
- xs: 4px
- sm: 8px
- md: 12px
- lg: 16px
- xl: 24px
Rules: Use sm, md, lg most often. Sidebar padding: sm horizontal, lg vertical.

---

## Typography
- Font-family: Inter, system-sans
- Sizes:
  - H1: 24px / 800 / tight
  - H2: 20px / 700
  - H3: 16px / 700 / uppercase tracking
  - Body: 14px / 400
  - Small: 12px / 600 (labels/status)
- Line-height: 1.1 (titles), 1.4 (body)
- Use bold weights for titles and action labels.

---

## Layout
- Sidebar: 200px (desktop), 72px (mobile/collapsed).
- Topbar: 56px height.
- Content: Full width of the remaining screen. No bounded boxes.

---

## Borders & radius
- layout containers: Use full width `.section` separated by a bottom `var(--color-line)` divider. 
- isolated cards: Use `.panel` for isolated views like Login, with a 1px border.
- border-radius: 0 (no rounding)
- shadows: none

---

## Buttons
Base rules:
- display: inline-flex; align-items:center; justify-content:center; gap: var(--space-xs);
- height: 36px; padding: 0 16px; font-weight:700; font-size: 14px; line-height:normal
- border: 1px solid neutral-strong
- border-radius: 0
- no box-shadow
Variants:
- primary (accent): background: accent-pistachio; color: neutral-strong; border-color: accent-pistachio
- secondary (light): background: white; color: neutral-strong; border-color: neutral-strong
- ghost: background: transparent; color: neutral-strong; border: none
Disabled:
- opacity: 0.45; pointer-events: none
Alignment:
- Buttons in toolbars must share same height and vertical alignment; no extra margin outside their container. Use gap: 8px between buttons.

---

## Sidebar
- width: sidebar-width
- background: sidebar-bg
- padding: 12px
- item height: 40px (align icons + label center)
- item font-size: 14px; font-weight:600
- active item: background: accent-pistachio; color: neutral-strong; left indicator: 4px accent strip
- icon color: #DDEDE0 (muted light)
- compact: allow icon-only collapsed state (width ~72px) — keep vertical alignment same.

---

## Header / Topbar
- height: 56px
- background: white
- left: page title (H1), right: actions (buttons)
- actions aligned to baseline, not wrapping; if mobile, stack vertically.

---

## Tables / Lists
- full width; table-layout: fixed
- th: font-size:12px; text-transform:uppercase; color: muted
- td: font-size:14px; padding: 12px 0
- separators: 1px line between rows
- status label: small badge (12px), uppercase, weight 700
  - running: color accent-pistachio-2 (border + text)
  - stopped: color danger
  - neutral: muted

---

## Sections (formerly Panels)
- sections: full width `.section` container, no outer borders, separated by bottom line
- header inside section: padding 24px 0 16px; bold short title
- detail grid: layout with `gap`, no manual margins
- quick actions: inline buttons aligned with flex gap

---

## Charts & sparklines
- monochrome lines (neutral-strong) with tiny pistachio accent for highlights
- background: transparent or very light surface
- no heavy gradients, no shadows

---

## Icons
- use simple line icons (stroke weight consistent)
- icon size: 16px for rows, 20px for header
- align icons and text baseline center

---

## Accessibility
- contrast: ensure text vs background >= 4.5:1 for body text; accent used sparingly may be lower contrast for decorative only
- keyboard focus: visible 2px outline using neutral-strong
- aria-labels for buttons/links

---

## Component snippets (examples)
Button primary:
- <button class="btn primary">Create</button>
- style: height:34px; background:#C7F464; color:#111111

Sidebar item:
- height:40px; padding:0 12px; display:flex; align-items:center; justify-content:space-between

Server row:
- grid: 1fr auto; left: name + meta, right: small spec box
- active row: background:#F7F8F3; border-left: 4px solid #C7F464

Progress bar:
- container height:18px; background:#F3F3F3; fill: width X%; background:#111 or #C7F464 for highlighted fill

---

## Naming / tokens (quick)
- color.bg, color.surface, color.sidebar, color.text, color.muted, color.line, color.accent, color.accent-2, color.danger
- space.xs, space.sm, space.md, space.lg, space.xl
- type.h1, type.h2, type.h3, type.body, type.small
- size.sidebar, size.topbar, size.button-height

---

## Prompts / Usage
- "Use DESIGN.md rules: dark left rail, white content, pistachio primary accent, strict grid, no radius, no shadow."
- "When generating UI, prefer short titles and minimal descriptions."
- "Follow spacing tokens exactly: sm, md, lg."

---

## Do / Don't
Do:
- keep it compact, aligned, minimal
- use defined colors only
Don't:
- use rounded corners, shadows, or random color accents

---

## Short reference (copyable)
- Colors: #FFFFFF, #F5F6F5, #0B0C0B, #0F1720, #6B6F6A, #E6E6DF, #C7F464, #A8E04A, #DC2626
- Spacing: 4, 8, 12, 16, 24
- Sidebar width: 200px
- Button height: 34px
