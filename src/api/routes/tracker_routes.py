"""Tracker routes — HTMX-powered AI law tracker CRUD.

Extracted from dashboard.py. Provides:
  - CSV-backed tracker table with inline editing
  - Import / export of tracker CSV
  - Helper to convert tracker rows to seed records

Ground truth: data/fact_laws.csv (241 laws).
"""

from __future__ import annotations

from html import escape as html_escape
from pathlib import Path

from fastapi import APIRouter, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

router = APIRouter()

TRACKER_CSV = Path("data/fact_laws.csv")
TRACKER_FIELDS = [
    "law_id", "canonical_law_id", "bill_number", "jurisdiction_id",
    "status_id", "effective_date", "title", "ai_scope_summary",
    "key_requirements_raw", "enforcement_penalties", "source_id",
    "source_url", "last_updated_at", "iapp_scope", "iapp_section",
]

# Display columns shown in the tracker table (subset for readability)
DISPLAY_COLS = [
    ("jurisdiction_id", "Jurisdiction"),
    ("title", "Title"),
    ("bill_number", "Bill #"),
    ("ai_scope_summary", "AI Scope"),
    ("effective_date", "Eff. Date"),
    ("status_id", "Status"),
    ("source_id", "Source"),
    ("source_url", "URL"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_tracker() -> list[dict]:
    """Read the law tracker CSV and return rows as dicts."""
    import csv

    if not TRACKER_CSV.exists():
        return []
    with open(TRACKER_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_tracker(rows: list[dict]) -> None:
    """Write rows back to the law tracker CSV."""
    import csv

    with open(TRACKER_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRACKER_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _status_label(status_id: str) -> str:
    """Map status_id to a human-readable label."""
    labels = {
        "1": "Enacted", "2": "Pending", "3": "Failed",
        "4": "Repealed", "5": "Active",
    }
    return labels.get(str(status_id).strip(), status_id or "Unknown")


def _source_label(source_id: str) -> str:
    """Map source_id to a human-readable label."""
    labels = {"1": "Orrick", "2": "IAPP"}
    return labels.get(str(source_id).strip(), source_id or "—")


def _status_color(status_id: str) -> str:
    """Map status_id to a CSS color."""
    colors = {
        "1": "var(--info)",       # Enacted
        "2": "var(--warning)",    # Pending
        "3": "var(--danger)",     # Failed
        "4": "var(--text-muted)", # Repealed
        "5": "var(--success)",    # Active
    }
    return colors.get(str(status_id).strip(), "var(--text)")


def _tracker_row_html(i: int, row: dict, editing: bool = False) -> str:
    """Render a single tracker row as HTML (display or edit mode)."""
    jurisdiction = html_escape(row.get("jurisdiction_id", ""))
    title = html_escape(row.get("title", ""))
    bill = html_escape(row.get("bill_number", ""))
    scope = html_escape(row.get("ai_scope_summary", ""))
    date = html_escape(row.get("effective_date", ""))
    status_id = row.get("status_id", "")
    source_id = row.get("source_id", "")
    url = row.get("source_url", "")
    url_esc = html_escape(url)
    url_short = html_escape(url[:45]) + ("..." if len(url) > 45 else "")

    if editing:
        return (
            f'<tr id="tracker-row-{i}" class="tracker-editing"'
            f'  style="background:var(--bg-secondary);">'
            f'<td colspan="{len(DISPLAY_COLS) + 1}" style="padding:8px;">'
            f'<form hx-post="/dashboard/api/tracker/{i}/edit"'
            f'      hx-target="#tracker-table-body"'
            f'      hx-swap="innerHTML"'
            f'      style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));'
            f'      gap:6px;font-size:12px;">'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Jurisdiction'
            f'    <input type="text" name="jurisdiction_id" value="{jurisdiction}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Title'
            f'    <input type="text" name="title" value="{title}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Bill #'
            f'    <input type="text" name="bill_number" value="{bill}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    AI Scope'
            f'    <input type="text" name="ai_scope_summary" value="{scope}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Effective Date'
            f'    <input type="text" name="effective_date" value="{date}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Status ID'
            f'    <select name="status_id" style="font-size:12px;padding:3px 5px;">'
            f'      <option value="1"{"selected" if status_id == "1" else ""}>Enacted</option>'
            f'      <option value="2"{"selected" if status_id == "2" else ""}>Pending</option>'
            f'      <option value="3"{"selected" if status_id == "3" else ""}>Failed</option>'
            f'      <option value="4"{"selected" if status_id == "4" else ""}>Repealed</option>'
            f'      <option value="5"{"selected" if status_id == "5" else ""}>Active</option>'
            f'    </select>'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Source ID (1=Orrick, 2=IAPP)'
            f'    <input type="text" name="source_id" value="{html_escape(source_id)}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:span 2;">'
            f'    Source URL'
            f'    <input type="url" name="source_url" value="{url_esc}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:1/-1;">'
            f'    Key Requirements'
            f'    <textarea name="key_requirements_raw" rows="2"'
            f'              style="width:100%;font-size:12px;padding:3px 5px;"'
            f'    >{html_escape(row.get("key_requirements_raw", ""))}</textarea>'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:1/-1;">'
            f'    Enforcement/Penalties'
            f'    <textarea name="enforcement_penalties" rows="1"'
            f'              style="width:100%;font-size:12px;padding:3px 5px;"'
            f'    >{html_escape(row.get("enforcement_penalties", ""))}</textarea>'
            f'  </label>'
            f'  <div style="display:flex;gap:6px;align-items:end;">'
            f'    <button type="submit" class="btn btn-sm btn-primary" hx-disabled-elt="this">'
            f'      <span class="btn-label">Save</span>'
            f'      <span class="htmx-indicator"><span class="spinner"></span></span>'
            f'    </button>'
            f'    <button type="button" class="btn btn-sm"'
            f'            hx-get="/dashboard/api/tracker"'
            f'            hx-target="#tracker-table-body"'
            f'            hx-swap="innerHTML"'
            f'            hx-params="none">Cancel</button>'
            f'  </div>'
            f'</form>'
            f'</td></tr>'
        )

    # URL display
    url_cell = "&mdash;"
    if url:
        url_cell = (
            f'<a href="{url_esc}" target="_blank" rel="noopener"'
            f'   style="font-size:11px;word-break:break-all;"'
            f'   title="{url_esc}">{url_short}</a>'
        )

    status_text = _status_label(status_id)
    s_color = _status_color(status_id)
    source_text = _source_label(source_id)

    return (
        f'<tr id="tracker-row-{i}">'
        f'<td><strong>{jurisdiction}</strong></td>'
        f'<td style="font-size:12px;max-width:220px;overflow:hidden;'
        f'    text-overflow:ellipsis;white-space:nowrap;"'
        f'    title="{title}">{title}</td>'
        f'<td style="font-size:12px;">{bill}</td>'
        f'<td style="font-size:12px;max-width:140px;overflow:hidden;'
        f'    text-overflow:ellipsis;white-space:nowrap;"'
        f'    title="{scope}">{scope}</td>'
        f'<td style="font-size:12px;">{date}</td>'
        f'<td><span style="color:{s_color};font-size:12px;">{status_text}</span></td>'
        f'<td style="font-size:12px;">{source_text}</td>'
        f'<td style="max-width:160px;">{url_cell}</td>'
        f'<td style="text-align:center;">'
        f'  <button class="btn btn-sm"'
        f'          hx-get="/dashboard/api/tracker/{i}/edit"'
        f'          hx-target="#tracker-table-body"'
        f'          hx-swap="innerHTML">Edit</button>'
        f'  <button class="btn btn-sm"'
        f'          hx-delete="/dashboard/api/tracker/{i}"'
        f'          hx-target="#tracker-table-body"'
        f'          hx-swap="innerHTML"'
        f'          hx-confirm="Delete this row?">Del</button>'
        f'</td>'
        f'</tr>'
    )


def _tracker_table_body(rows: list[dict], edit_idx: int = -1) -> str:
    """Render all tracker rows as a <tbody> innerHTML."""
    if not rows:
        return (
            f'<tr><td colspan="{len(DISPLAY_COLS) + 1}" style="text-align:center;padding:20px;">'
            'No records. Add a row or import a CSV.</td></tr>'
        )
    return "".join(
        _tracker_row_html(i, r, editing=(i == edit_idx))
        for i, r in enumerate(rows)
    )


def _tracker_csv_to_records() -> list[dict]:
    """Convert fact_laws.csv rows into the record format seed_from_tracker expects.

    Returns list of dicts with keys:
        state, state_code, ai_scope, law_name, law_url, bill_id,
        effective_date, key_requirements, enforcement
    """
    tracker_rows = _read_tracker()
    records = []
    for row in tracker_rows:
        jurisdiction = row.get("jurisdiction_id", "").strip()

        records.append({
            "state": jurisdiction,
            "state_code": jurisdiction,
            "ai_scope": row.get("ai_scope_summary", ""),
            "law_name": row.get("title", ""),
            "law_url": row.get("source_url", ""),
            "bill_id": row.get("bill_number", ""),
            "effective_date": row.get("effective_date", ""),
            "key_requirements": row.get("key_requirements_raw", ""),
            "enforcement": row.get("enforcement_penalties", ""),
        })
    return records


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/tracker")
def tracker_list(
    q: str = Query("", alias="q"),
) -> HTMLResponse:
    """Render the tracker table body (all rows)."""
    rows = _read_tracker()
    if q:
        q_lower = q.lower()
        rows_filtered = [
            r for r in rows
            if q_lower in (
                r.get("jurisdiction_id", "") + r.get("title", "")
                + r.get("bill_number", "") + r.get("ai_scope_summary", "")
            ).lower()
        ]
    else:
        rows_filtered = rows
    return HTMLResponse(_tracker_table_body(rows_filtered))


@router.get("/api/tracker/{idx}/edit")
def tracker_edit_form(idx: int) -> HTMLResponse:
    """Return the table body with one row in edit mode."""
    rows = _read_tracker()
    if idx < 0 or idx >= len(rows):
        return HTMLResponse(f'<tr><td colspan="{len(DISPLAY_COLS) + 1}">Row not found.</td></tr>')
    return HTMLResponse(_tracker_table_body(rows, edit_idx=idx))


@router.post("/api/tracker/{idx}/edit")
async def tracker_save_row(idx: int, request: Request) -> HTMLResponse:
    """Save edits to a tracker row and return the refreshed table body."""
    rows = _read_tracker()
    if idx < 0 or idx >= len(rows):
        return HTMLResponse(f'<tr><td colspan="{len(DISPLAY_COLS) + 1}">Row not found.</td></tr>')

    form = await request.form()
    for field in TRACKER_FIELDS:
        val = form.get(field)
        if val is not None:
            rows[idx][field] = val.strip()

    _write_tracker(rows)
    return HTMLResponse(_tracker_table_body(rows))


@router.delete("/api/tracker/{idx}")
def tracker_delete_row(idx: int) -> HTMLResponse:
    """Delete a tracker row and return the refreshed table body."""
    rows = _read_tracker()
    if idx < 0 or idx >= len(rows):
        return HTMLResponse(f'<tr><td colspan="{len(DISPLAY_COLS) + 1}">Row not found.</td></tr>')
    rows.pop(idx)
    _write_tracker(rows)
    return HTMLResponse(_tracker_table_body(rows))


@router.post("/api/tracker/add")
async def tracker_add_row(request: Request) -> HTMLResponse:
    """Add a new row to the tracker and return the refreshed table body."""
    rows = _read_tracker()
    form = await request.form()
    new_row = {}
    for field in TRACKER_FIELDS:
        new_row[field] = (form.get(field) or "").strip()
    if not new_row.get("jurisdiction_id"):
        new_row["jurisdiction_id"] = "XX"
    rows.append(new_row)
    _write_tracker(rows)
    return HTMLResponse(_tracker_table_body(rows))


@router.get("/api/tracker/export")
def tracker_export() -> StreamingResponse:
    """Download the tracker CSV."""
    import csv
    import io

    rows = _read_tracker()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TRACKER_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=fact_laws.csv"},
    )


@router.post("/api/tracker/import")
async def tracker_import(file: UploadFile) -> HTMLResponse:
    """Import a CSV to replace the tracker. Validates columns first."""
    import csv
    import io

    content = (await file.read()).decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(content))

    # Validate header
    if not reader.fieldnames:
        return HTMLResponse(
            '<div class="result-panel error">Empty or invalid CSV file.</div>'
        )

    # Check for required columns (allow extra columns)
    required = {"law_id", "jurisdiction_id", "title"}
    missing = required - set(reader.fieldnames)
    if missing:
        return HTMLResponse(
            f'<div class="result-panel error">'
            f'Missing required columns: {html_escape(", ".join(sorted(missing)))}. '
            f'Expected at minimum: {html_escape(", ".join(sorted(required)))}'
            f'</div>'
        )

    rows = []
    for r in reader:
        clean = {f: (r.get(f) or "").strip() for f in TRACKER_FIELDS}
        if clean.get("law_id") or clean.get("title"):
            rows.append(clean)

    _write_tracker(rows)
    return HTMLResponse(
        f'<div class="result-panel success" style="margin-bottom:8px;">'
        f'Imported <strong>{len(rows)}</strong> records.</div>'
        + _tracker_table_body(rows)
    )
