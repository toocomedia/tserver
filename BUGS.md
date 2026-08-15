# Bug Report — Resource Guard & Server Optimizations

> Audited: 2026-08-15  
> Phase 1 (in progress): Bug 7 — Low-RAM Mode button + RAM spike during swap resize  
> Phase 2 (planned): Bugs 1, 2, 5, 6, 8  
> Phase 3 (planned): Bugs 3, 4 — Safe Install & cancel callbacks

---

## 🔴 Critical — Resource Guard Logic

### Bug 1 — Guard uses two different metrics (RAM to cancel, swap to block) `[Phase 2]`

**File:** `backend/services/resource_guard_service.py` — Line 98–102 and Line 257–273

The background monitor goes into `active` state and cancels in-progress operations when **RAM % >= user's limit**.
But `preflight()`, which gates *new* operations before they start, blocks them when **swap % >= 80%** — a completely different metric and threshold.

**Result:** The guard blocks new operations because swap hit 80%, even if RAM is at 60% and the user set a 90% RAM limit. Operations that should be allowed are rejected.

---

### Bug 2 — Swap gate (80%) is hardcoded and invisible to the user `[Phase 2]`

**File:** `backend/services/resource_guard_service.py` — Line 258

```python
swap_threshold = prof.get("swap_threshold", 80)
```

This 80% swap block is embedded in resource profiles and is completely separate from the user-visible `memory_limit_percent` field in Settings. The user has no way to configure or even see this threshold. The Settings UI only shows "RAM Warning Threshold", so users have no idea a hidden swap gate exists.

---

### Bug 3 — Safe Install (stop services temporarily) is completely broken `[Phase 3]`

**File:** `backend/services/resource_guard_service.py` — Line 757–762

```python
def _get_adapter(self, candidate: dict):
    return None  # always returns None, no exceptions
```

Both `_stop_candidate()` and `_start_candidate()` call `_get_adapter()` first. Since it always returns `None`, they fall back to `dependency_manager.get_service()` which also returns nothing for registered services.

**Result:** No service is ever stopped or restored in Safe Install Mode. The "turn off optional services temporarily to free RAM, then bring them back after install" feature is 100% a no-op.

---

### Bug 4 — Cancel callbacks are `None` by default; cancelled ops are never unregistered `[Phase 3]`

**File:** `backend/services/resource_guard_service.py` — Line 326–343 and Line 380–387

`register()` defaults to `cancel=None`. The monitor does:
```python
if candidates and candidates[0].cancel:
    candidates[0].cancel()
```
Since `cancel` is `None` in most calls, nothing ever fires. Even if a cancel callback is wired, the operation is **never removed** from `self._operations` after calling it, so the monitor retries cancellation every 5 seconds indefinitely.

---

## 🟠 Major — Server Optimizations UI

### Bug 5 — Clicking "Active" (Low-RAM Mode) makes Swap Storage Size visually jump to 500MB `[Phase 2]`

**File:** `backend/templates/pages/usage/index.html` — Line 599–640

When Low-RAM Optimization Mode becomes active, the JS:
1. Hides the `Off` and `512M` swap buttons
2. Shows a previously-hidden `500M (Base zRAM)` button
3. Immediately marks it as the active selection

No actual OS swap change happens — it is a display-only artifact. But the user sees the Swap Storage Size jump to 500MB the moment they activate Low-RAM Mode, as if it changed automatically. This causes confusion about what the swap size actually is.

---

### Bug 6 — Swap button set changes (5 → 4 buttons) creates illusion of duplicate active buttons `[Phase 2]`

**File:** `backend/templates/pages/usage/partials/optimizations.html` — Line 37–44
**File:** `backend/templates/pages/usage/index.html` — Line 599–617

The HTML has 5 swap buttons but `btn-swap-base` (500M) is hidden by default. When Low-RAM Mode toggles, the visible set changes from `{Off, 512M, 1G, 2G, 4G}` to `{500M, 1G, 2G, 4G}`. During the transition (while `isActionPending = true`), both the old active button and the newly computed active button can show `is-active` simultaneously, making it look like two options are selected at once.

---

### Bug 7 — Low-RAM Mode button turns off after switching swap size (RAM usage looks high) `[Phase 1 — ✅ FIXED]`

**File:** `backend/templates/pages/usage/index.html` — Line 670–677

After any swap size change, the code calls `fetchOptimizationStatus()` which re-polls `/api/system/optimization/status`. During the `set-swap` script execution:

1. The OS turns swap off to resize it
2. All data from swap is pushed back into RAM → **RAM usage spikes**
3. `/proc/sys/vm/swappiness` may temporarily not read `10` while the script runs
4. The backend returns `optimization_active: false`
5. The Low-RAM Mode button flips to **"Inactive"** even though nothing changed

This happens because button state is re-synced from a live OS read mid-operation, without checking `isActionPending` first. The fix is to skip the Low-RAM button re-sync while an action is pending.

---

## 🟡 Minor — Settings UI

### Bug 8 — "RAM Warning Threshold" label is misleading `[Phase 2]`

**File:** `backend/templates/pages/settings/resource_guard/settings.html` — Line 17–19

The input field is labeled **"RAM Warning Threshold"** with hint text "when total VPS RAM usage crosses this percentage, a warning appears". In reality, this same value triggers `state = "active"` in the backend which causes in-progress operations to be cancelled. It is both a warning threshold and a hard cancellation gate. Users who think it is warning-only may set it too high.

---

## Summary

| # | Severity | File | Issue |
|---|----------|------|-------|
| 1 | 🔴 Critical `[Phase 2]` | `resource_guard_service.py` | Guard cancels on RAM% but blocks new ops on swap% — inconsistent metrics |
| 2 | 🔴 Critical `[Phase 2]` | `resource_guard_service.py` | Swap gate is 80% hardcoded, not user-configurable, blocks valid operations |
| 3 | 🔴 Critical `[Phase 3]` | `resource_guard_service.py` | `_get_adapter()` always returns `None` — Safe Install stop/restore never runs |
| 4 | 🔴 Critical `[Phase 3]` | `resource_guard_service.py` | `cancel=None` by default; cancelled ops never unregistered from monitor |
| 5 | 🟠 Major `[Phase 2]` | `usage/index.html` | Low-RAM "Active" click visually jumps Swap Storage Size to 500MB |
| 6 | 🟠 Major `[Phase 2]` | `optimizations.html` + `usage/index.html` | Button set change (5→4) creates illusion of two simultaneously active buttons |
| 7 | 🟠 Major `[✅ Phase 1 Fixed]` | `usage/index.html` | Button state flickering because it re-syncs from transient OS state during actions |
| 8 | 🟡 Minor `[Phase 2]` | `settings.html` | "RAM Warning Threshold" label does not communicate it is also the hard cancellation gate |
