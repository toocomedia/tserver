# Design Components & UI Reference Guide

> **Canonical reference for frontend components, macros, partials, and styles across the panel.**
> AI coding assistants and developers must refer to this document before creating or modifying UI to avoid code duplication and maintain visual consistency.

---

## 1. Shared Bottom Delete Window (`delete_drawer.html`)

All destructive actions (single deletes, batch/bulk deletes, uninstalls, resource drops) must use the shared **Bottom Delete Window**.

### Visual & Functional Behavior
- **Position**: Bottom-docked window (`bottom: 0`, max-width `1080px`, rounded top corners) with a dark translucent backdrop blur.
- **Layout (No Unnecessary Dividers)**: Clean horizontal split (or column on mobile):
  - **Left**: Danger badge icon, title, target item badge/name, concise description, and optional form inputs/checkboxes.
  - **Right**: Two large buttons — **Cancel** (secondary) and **Delete** (danger).
- **Hover-Hold Timer Confirmation (No Time Digits)**:
  - The confirm button starts locked (`disabled`).
  - Hovering (`mouseenter` / mobile `touchstart`) starts a smooth progress fill animation across the button (1.2 seconds).
  - Once filled 100%, the button activates with an unlocked glow and becomes clickable.
  - Leaving before completion (`mouseleave`) resets the progress to 0% and keeps the button locked.
  - **No countdown numbers or seconds are displayed in the button text.**

### Usage Methods

#### Method A: Declarative HTML Attributes (Preferred for static buttons)
```html
<button type="button" class="btn btn--danger btn--sm"
        data-delete-drawer-trigger
        data-delete-url="/api/resources/42/delete"
        data-delete-title="Delete Database"
        data-delete-message="This will drop the database and all stored tables permanently."
        data-delete-item="production_db"
        data-delete-label="Delete Database"
        data-delete-extra-id="optional-extra-template-id">
  Delete
</button>
```

#### Method B: JavaScript Programmatic API (`window.openDeleteDrawer`)
```javascript
window.openDeleteDrawer({
  title: "Delete Application",
  message: "Remove the application and all associated resources permanently.",
  itemName: "my-web-app",
  okLabel: "Delete App",
  extraHtml: `<label class="form-check"><input type="checkbox" name="delete_db" value="1"><span>Delete Database</span></label>`,
  onConfirm: async () => {
    const res = await fetch('/api/apps/1/delete', { method: 'POST' });
    if (res.ok) window.location.reload();
  }
});
```

#### Method C: Unified Confirmation Dispatcher (`window.confirmAction`)
```javascript
window.confirmAction(
  "Are you sure you want to delete 5 selected items?",
  async () => {
    await bulkDeleteApi(selectedIds);
    window.toast("Items deleted", "success");
  },
  {
    danger: true, // Automatically delegates to Bottom Delete Window
    title: "Bulk Delete",
    okLabel: "Delete Items",
    itemName: "5 Selected Items"
  }
);
```

---

## 2. Reusable Template Partials (`backend/templates/partials/`)

| Component Partial | Purpose & Jinja Usage |
| :--- | :--- |
| **`delete_drawer.html`** | Shared bottom delete confirmation window (included globally in `layout.html`). |
| **`action_toolbar.html`** | Top table bar with Search input, Status filter, Bulk actions dropdown, and Primary button. |
| **`empty_state.html`** | Standard empty state card when tables/lists have no items. |
| **`list_actions_toggle.html`** | Standard 3-dots dropdown menu tray for action buttons in table rows. |
| **`base_card.html`** | Generic card wrapper component. |
| **`stat_cards.html`** | Metric counter box with title, large number, and icon. |
| **`status_badge.html`** | Status pill badge (active, running, stopped, failed). |
| **`skeleton_overlay.html`** | Shimmer loading placeholder for async content. |
| **`csrf_field.html`** | Hidden CSRF token field for HTML forms (`{% include "partials/csrf_field.html" %}`). |

---

## 3. Component Usage Patterns & Snippets

### A. Action Toolbar (`action_toolbar.html`)
```jinja2
{% set search_placeholder = "Search items by name..." %}
{% set filter_options = [{'val': 'active', 'label': 'Active'}, {'val': 'stopped', 'label': 'Stopped'}] %}
{% set bulk_actions = [{'action': 'start', 'label': 'Start Selected'}, {'action': 'delete', 'label': 'Delete Selected'}] %}
{% set primary_action = {'url': '/create', 'label': 'Add New Item', 'icon': 'plus'} %}
{% include "partials/action_toolbar.html" %}
```

### B. Empty State (`empty_state.html`)
```jinja2
{% from "partials/empty_state.html" import empty_state %}
{{ empty_state(
  icon='folder',
  title='No Files Found',
  desc='Upload files or create directories to get started.',
  action_url='/upload',
  action_label='Upload File',
  action_icon='upload',
  id='files-empty-state'
) }}
```

### C. Row Actions Menu (`list_actions_toggle.html`)
```jinja2
<td class="col-actions" style="position:relative;">
  {% import "partials/list_actions_toggle.html" as list_actions %}
  {{ list_actions.button() }}
  {% call list_actions.tray() %}
    <a class="btn btn--secondary btn--sm" href="/edit/{{ item.id }}">Edit</a>
    <button type="button" class="btn btn--danger btn--sm"
            data-delete-drawer-trigger
            data-delete-url="/delete/{{ item.id }}"
            data-delete-title="Delete Item"
            data-delete-item="{{ item.name }}">
      Delete
    </button>
  {% endcall %}
</td>
```

---

## 4. Design System Tokens & CSS Utilities

### Theme Tokens (`main.css`)
```css
--color-bg: #0f1218;               /* Main panel background */
--color-surface: #181c24;          /* Cards, modals, drawers background */
--color-line: rgba(255,255,255,0.08); /* Borders and subtle dividers */
--color-text: #f1f5f9;             /* Primary text */
--color-muted: #94a3b8;            /* Secondary / muted text */
--color-primary: #3b82f6;          /* Primary buttons and highlights */
--color-accent: #10b981;           /* Success / running green */
--color-danger: #ef4444;           /* Danger / delete red */
--color-warning: #f59e0b;          /* Warning orange */
```

### Button Classes (`buttons.css`)
- `.btn`: Base button styling (flexbox, rounded-lg, transition).
- `.btn--primary`: Solid primary accent button (e.g. Save, Create).
- `.btn--secondary`: Neutral background button (e.g. Cancel, Details).
- `.btn--danger`: Red destructive button (e.g. Delete, Drop, Uninstall).
- `.btn--ghost`: Transparent background button with hover state.
- `.btn--sm`: Compact button height (`32px`, font size `12px`).
- `.btn--icon`: Square icon-only button (`32px` × `32px` or `36px` × `36px`).

### Table Classes (`tables.css`)
- `.table-wrap`: Responsive overflow container with rounded corners and border.
- `.table`: Clean data table with subtle zebra hover rows.
- `.col-actions`: Fixed-width action column aligned to end.

### Alerts (`alerts.css`)
- `.alert.alert--success`: Success banner.
- `.alert.alert--danger`: Error banner.
- `.alert.alert--warning`: Warning banner.
- `.alert.alert--info`: Neutral info banner.

---

## 5. Global Frontend JavaScript Helpers

| Function | Purpose | Example |
| :--- | :--- | :--- |
| `window.toast(msg, type)` | Shows top-right floating toast notification (`'success'`, `'danger'`, `'warning'`). | `toast("Changes saved!", "success");` |
| `window.confirmAction(msg, cb, opts)` | Dispatches action to the bottom delete window (when `danger: true`) or standard confirm. | `confirmAction("Drop table?", dropCb, { danger: true });` |
| `window.openDeleteDrawer(opts)` | Directly triggers the bottom delete window. | `openDeleteDrawer({ title: "Delete", onConfirm: cb });` |
| `window.closeDeleteDrawer()` | Closes the bottom delete window and resets hover states. | `closeDeleteDrawer();` |
| `window.submitPost(url, payload)` | Submits a dynamic form via POST with CSRF protection. | `submitPost('/action', { id: 10 });` |
| `window.openModal(id)` / `closeModal(id)` | Opens/closes a generic modal by ID. | `openModal('add-record-modal');` |

---

## 6. AI Guidelines for Keeping Code Lean & Modular

1. **Do Not Recreate Delete Modals**: Never write a custom `<div class="modal">` for deleting or uninstalling items. Always use `data-delete-drawer-trigger` or `window.openDeleteDrawer`.
2. **Never Generate Monolithic Inline HTML/CSS**: Reuse the partials listed above. Keep page templates under 120 lines.
3. **Use Design Tokens**: Use `var(--color-...)` instead of hardcoded hex values (`#dc2626`, `#000`, etc.).
4. **No Direct `confirm()` in New Features**: Replace native browser `window.confirm()` with `window.confirmAction()` or `window.openDeleteDrawer()`.
