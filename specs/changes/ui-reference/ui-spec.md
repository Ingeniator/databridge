# UI Specification: Browser Data Inspector

**Feature Branch**: `001-datasource-adapter-redesign`
**Date**: 2026-06-02
**Reference screenshot**: provided 2026-06-02

---

## Overview

The browser data inspector is a single-page interface at `/browser` for browsing, filtering, and exporting data from configured datasources. This spec covers the visual design of the primary inspection workflow — connection tabs through data preview.

---

## Layout

```
┌─────────────────────────────────────────────────────────────┐
│  [S3 Storage] [ClickHouse Production*] [PostgreSQL]         │
│  + Add Connection                    LAST SYNCED 2 mins ago │
│                                      ● HEALTHY STATUS       │
├─────────────────────────────────────────────────────────────┤
│ Refine Dataset                                   CLEAR ALL  │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ 🗄 SCHEMA DISCOVERY (32 FIELDS)   Inferred from sample▲│  │
│ │  [resource.attributes.service.name STRING]              │  │
│ │  [resource.attributes.deployment.environment STRING]    │  │
│ │  [span.attributes.http.method STRING]                   │  │
│ │  Automated schema detection has mapped 32 fields…       │  │
│ └────────────────────────────────────────────────────────┘  │
│  [FIELD: TIMESTAMP ↔]        PREDICATE FILTER              │
│  ┌──────────────────┐  ┌───────────────────────────────┐   │
│  │ Last 24 hours  ▼ │  │ 🔽 e.g. status == 'error'… ≡ │   │
│  └──────────────────┘  └───────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│ Data Preview  👁 VISIBILITY          LIMIT 1000  TOTAL: 4.2M│
│  ID          TIMESTAMP      PAYLOAD                STATUS   │
│  #9821-XA    2024-11-20 …   {"event":"checkout"…  PROCESSED │
│  #9821-XB    2024-11-20 …   {"event":"view",…     PROCESSED │
│                       Load More Rows                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Components

### 1. Connection Tab Bar

- Tabs render as horizontal pill/tab buttons; active tab is **bold + blue underline**.
- Each tab shows the connection name; a type badge (`s3`, `clickhouse`, `trino`, etc.) appears inline.
- `+ Add Connection` appears at the end of the tab list as a dashed ghost tab.
- **Right side of tab bar**: two metadata items separated by a vertical gutter.
  - `LAST SYNCED` — 10px uppercase label above relative timestamp ("2 mins ago", "Just now")
  - `● HEALTHY STATUS` — green dot + bold label. States: `HEALTHY STATUS` (green), `SYNCING…` (gray, pulsing), `ERROR` (red).

### 2. Refine Dataset Card

Outer card: `bg-surface-container-low`, `rounded-2xl`, shadow, border.

#### 2a. Card Header
- Left: "Refine Dataset" (`font-headline`, `text-lg`, bold)
- Right: "CLEAR ALL" — 10px uppercase primary-colored text button. Shown only when at least one filter, time range, or search is active.

#### 2b. Schema Discovery Section

Appears inside the card, above the filter row. Hidden until schema detection completes.

**Header row:**
- Left: database icon (Material Symbol `database`) + `SCHEMA DISCOVERY (N FIELDS)` — 11px uppercase, `on-surface-variant/70`
- Right: `Inferred from sample data` — 11px italic muted text, plus `▲`/`▼` collapse toggle button

**Field chips row** (shown when expanded):
- Up to 3 representative chips inline, each showing `field.name` in monospace + a `TYPE` pill badge (e.g. `STRING`, `INT`, `BOOL`).
- Chip style: `bg-surface-container`, border, `rounded-lg`, small font.
- Type badge: muted background, uppercase, tiny text.

**Description line:**
- 1 line of muted text: `Automated schema detection has mapped N fields from the data stream…`
- Collapsed when section is toggled.

**Actions (schema bar footer):**
- `⚙ Columns ▾` — opens column picker panel.
- `↺` — re-runs schema detection.

#### 2c. Filter Row

Two-column layout (`flex`, `gap-6`), separated by a thin divider below the schema section.

**Left column — Time Range:**
- `[FIELD: TIMESTAMP ↔]` — outlined blue badge/chip showing the active time field name (uppercased last path segment). Clicking it shows the time field `<select>` to change which field drives time filtering.
- When time field is "off": badge is hidden; time picker is dimmed (opacity 45%, pointer-events none).
- Below the badge: the existing time range dropdown (`Last 24 hours`, etc.).

**Right column — Predicate Filter:**
- Label: `PREDICATE FILTER` — 10px uppercase, `on-surface-variant`.
- Input: full-width, monospace, `rounded-lg`, border. Placeholder: `e.g. status == 'error' AND payload.val > 100`.
- Left icon: `filter_alt` Material Symbol (replaces the search magnifier).
- Right icon button: `tune` Material Symbol — opens the advanced structured filter builder panel below.
- When structured filters are active, the `tune` icon turns primary-colored.

**Advanced filter panel** (below filter row, collapsible):
- Appears when tune icon is clicked.
- Shows existing filter rules as chips + the add-rule row (`field` select, `op` select, `value` input, `+ Add` button).
- AND/OR mode toggle shown when ≥2 rules exist.

### 3. Data Preview Section

Separate card below the Refine Dataset card.

**Header row:**
- Left: "Data Preview" heading + `👁 VISIBILITY` button (opens column picker, same as `⚙ Columns`).
- Right: `LIMIT 1000` (shows current result limit; editable in future) + `TOTAL: N ROWS` (count of rows in current result set, formatted with commas). Hidden when no results yet.

**Table:**
- Columns are schema-driven (visible columns from column picker).
- Each row has a checkbox (left) and a `Preview` button (right).
- **Status column rendering**: when a column named `status`, `level`, or `state` has values like `processed`, `complete`, `error`, `failed`, the cell renders as a colored pill badge rather than plain text:
  - `processed` / `complete` / `success` → amber/gold background
  - `error` / `failed` → red background
  - `pending` / `queued` → blue background

**Footer:**
- `Load More Rows` — centered link shown when the result count equals the current limit. Triggers a new search with a higher limit (2× current).

---

## Color & Typography Notes

| Token | Value | Usage |
|---|---|---|
| `primary` | `#094cb2` | Active tab underline, CLEAR ALL, time-field badge border, tune icon (active) |
| `primary-fixed` | `#d9e2ff` | Time-field badge background tint |
| `on-surface-variant` | `#434653` | Labels, muted text |
| `outline-variant` | `#c3c6d5` | Borders, dividers |
| `tertiary-container` | `#bfab49` | PROCESSED status badge background |

Typography uses the existing `font-label` (Public Sans) for labels/badges and `font-mono` (system monospace via Tailwind) for field paths and data values.

---

## State Transitions

| State | Schema Bar | Status Indicator | Filter Row |
|---|---|---|---|
| Tab just selected | Hidden | "Syncing…" (gray dot) | Shown but filter icon hidden |
| Schema detecting | "Detecting schema…" (pulsing dot) | "Syncing…" | Filter icon hidden |
| Schema ready | Full discovery section visible | "HEALTHY STATUS" + "Just now" | Filter icon visible, time badge shown |
| Schema error | Error state (red dot, Retry button) | "ERROR" | Filter icon shown (no time field) |
| No data in range | Yellow warning bar | "HEALTHY STATUS" | Filter icon shown |

---

## Accessibility & Testing

- `data-testid="time-field-badge"` — time field chip
- `data-testid="predicate-filter-input"` (alias for `search-input`) — filter expression input
- `data-testid="filter-advanced-btn"` — tune icon button
- `data-testid="schema-collapse-btn"` — schema section collapse button
- `data-testid="load-more-btn"` — load more rows button
- `data-testid="total-rows-display"` — total row count element
- `data-testid="visibility-btn"` — visibility / column picker button
