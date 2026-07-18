# Frontend Rules

## Stack
- Jinja2 HTML templates (server-rendered)
- Vanilla JavaScript only (no frameworks)
- Plain CSS (no Tailwind, no Bootstrap)
- Inter font from Google Fonts

## File Structure Rules
- Max 150 lines per HTML template file. Split into partials if exceeded.
- Max 200 lines per JS file. Split into modules if exceeded.
- Max 300 lines per CSS file. Split into sections if exceeded.
- One partial = one UI component. No mixing of unrelated markup.

## Folder Structure
```
templates/
  layout.html            # Base shell: sidebar + header + content slot
  
  partials/              # Reusable components
    sidebar.html
    header.html
    table.html
    status_badge.html
    modal.html
    alert.html
  
  pages/                 # One file per page/section
    dashboard.html
    domains/
      index.html         # Domain list
      create.html        # Add domain form
      detail.html        # Domain detail + actions
    dns/
      index.html
      records.html
    ssl/
      index.html
    proxy/
      index.html
      create.html

static/
  css/
    main.css             # CSS variables, resets, base
    layout.css           # Sidebar, header, content area
    components.css       # Buttons, tables, badges, forms
    utils.css            # Spacing helpers, alignment classes
  js/
    main.js              # Global init, shared utils
    modules/
      modal.js           # Modal open/close logic
      toast.js           # Notification toasts
      table.js           # Table filtering, sorting helpers
      forms.js           # Form validation and submission
      domains.js         # Domain page specific logic
      dns.js
      ssl.js
      proxy.js
```

## Template Rules
- layout.html is the only full HTML shell (<!DOCTYPE html>, <head>, etc.).
- All pages extend layout.html via Jinja2 block inheritance.
- No inline styles ever. Use CSS classes only.
- No inline event handlers (onclick="..."). Use data attributes or JS selectors.
- Keep templates logic-free. Use Jinja2 only for loops and variable output.
- No business logic in templates.

## CSS Rules
- Use CSS custom properties (variables) for all colors, spacing, font sizes.
- Define all variables in :root inside main.css.
- Follow the 8px spacing system (--space-1: 8px, --space-2: 16px, etc.).
- Never use arbitrary pixel values not in the spacing system.
- No !important except for utility overrides.
- No ID selectors in CSS. Use classes only.
- BEM-like naming: .sidebar, .sidebar__item, .sidebar__item--active

## CSS Variables (define in main.css)
```css
/* === DESIGN.md tokens — do not add values outside this list === */
:root {
  /* Colors */
  --color-bg:         #FFFFFF;
  --color-surface:    #F5F6F5;
  --color-sidebar:    #0B0C0B;
  --color-text:       #0F1720;
  --color-muted:      #6B6F6A;
  --color-line:       #E6E6DF;
  --color-accent:     #C7F464;
  --color-accent-2:   #A8E04A;
  --color-neutral:    #111111;
  --color-danger:     #DC2626;
  --color-icon:       #DDEDE0;

  /* Spacing (8px scale) */
  --space-xs:  4px;
  --space-sm:  8px;
  --space-md:  12px;
  --space-lg:  16px;
  --space-xl:  24px;

  /* Typography */
  --font-h1:    24px;
  --font-h2:    18px;
  --font-h3:    14px;
  --font-body:  13px;
  --font-small: 11px;

  /* Sizes */
  --sidebar-width:     200px;
  --sidebar-collapsed: 72px;
  --topbar-height:     56px;
  --button-height:     34px;
  --page-gutter:       16px;
  --content-gap:       16px;
  --max-content-width: 1200px;
}
```

## JavaScript Rules
- Use ES6+ modules (type="module").
- No global variables. Use module scope.
- One JS file per feature page.
- Shared utilities go in main.js or a specific module.
- No jQuery. Vanilla DOM only.
- Use fetch() for all API calls. No XMLHttpRequest.
- Always handle fetch errors with try/catch.
- Keep functions under 30 lines. Split if longer.
- Comment only non-obvious logic.

## HTML Rules
- Use semantic elements: <main>, <nav>, <header>, <section>, <table>.
- All form inputs must have a <label>.
- All interactive elements must have a unique id.
- Use data-* attributes for JS hooks (data-action, data-id).
- No inline styles.
- No deprecated HTML attributes.

## Do
- Keep templates thin and dumb.
- Keep CSS structured and variable-driven.
- Keep JS modular and minimal.
- Reuse partials aggressively.
- Follow the design system in DESIGN.md exactly.

## Don't
- No CSS frameworks.
- No JS frameworks or libraries.
- No inline styles or scripts.
- No logic in templates beyond loops and variable output.
- No mixing page JS into shared JS files.
