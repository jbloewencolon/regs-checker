"""Tracker routes — HTMX-powered AI law tracker CRUD.

Extracted from dashboard.py. Provides:
  - CSV-backed tracker table with inline editing
  - Import / export of tracker CSV
  - Helper to convert tracker rows to seed records
"""

from __future__ import annotations

from html import escape as html_escape
from pathlib import Path

from fastapi import APIRouter, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

router = APIRouter()

TRACKER_CSV = Path("static/ai_law_tracker.csv")
TRACKER_FIELDS = [
    "State/Terr", "AI Scope", "Relevant Law", "Bill ID",
    "Effective Date", "Key Requirements", "Enforcements Penalties",
    "Status", "Source URL",
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


def _tracker_row_html(i: int, row: dict, editing: bool = False) -> str:
    """Render a single tracker row as HTML (display or edit mode)."""
    state = html_escape(row.get("State/Terr", ""))
    scope = html_escape(row.get("AI Scope", ""))
    law = html_escape(row.get("Relevant Law", ""))
    bill = html_escape(row.get("Bill ID", ""))
    date = html_escape(row.get("Effective Date", ""))
    key_reqs = row.get("Key Requirements", "")
    enforce = row.get("Enforcements Penalties", "")
    status = html_escape(row.get("Status", "Active"))
    url = row.get("Source URL", "")
    url_esc = html_escape(url)
    url_short = html_escape(url[:45]) + ("..." if len(url) > 45 else "")

    if editing:
        return (
            f'<tr id="tracker-row-{i}" class="tracker-editing"'
            f'  style="background:var(--bg-secondary);">'
            f'<td colspan="9" style="padding:8px;">'
            f'<form hx-post="/dashboard/api/tracker/{i}/edit"'
            f'      hx-target="#tracker-table-body"'
            f'      hx-swap="innerHTML"'
            f'      style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));'
            f'      gap:6px;font-size:12px;">'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    State/Terr'
            f'    <input type="text" name="State/Terr" value="{state}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    AI Scope'
            f'    <input type="text" name="AI Scope" value="{scope}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Relevant Law'
            f'    <input type="text" name="Relevant Law" value="{law}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Bill ID'
            f'    <input type="text" name="Bill ID" value="{bill}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Effective Date'
            f'    <input type="text" name="Effective Date" value="{date}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;">'
            f'    Status'
            f'    <select name="Status" style="font-size:12px;padding:3px 5px;">'
            f'      <option value="Active"{"selected" if status == "Active" else ""}>Active</option>'
            f'      <option value="Enacted"{"selected" if status == "Enacted" else ""}>Enacted</option>'
            f'      <option value="Pending"{"selected" if status == "Pending" else ""}>Pending</option>'
            f'      <option value="Failed"{"selected" if status == "Failed" else ""}>Failed</option>'
            f'      <option value="Repealed"{"selected" if status == "Repealed" else ""}>Repealed</option>'
            f'    </select>'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:span 2;">'
            f'    Source URL'
            f'    <input type="url" name="Source URL" value="{url_esc}"'
            f'           style="width:100%;font-size:12px;padding:3px 5px;">'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:1/-1;">'
            f'    Key Requirements'
            f'    <textarea name="Key Requirements" rows="2"'
            f'              style="width:100%;font-size:12px;padding:3px 5px;"'
            f'    >{html_escape(key_reqs)}</textarea>'
            f'  </label>'
            f'  <label style="display:flex;flex-direction:column;gap:2px;grid-column:1/-1;">'
            f'    Enforcements/Penalties'
            f'    <textarea name="Enforcements Penalties" rows="1"'
            f'              style="width:100%;font-size:12px;padding:3px 5px;"'
            f'    >{html_escape(enforce)}</textarea>'
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

    # Status badge color
    status_color = {
        "Active": "var(--success)", "Enacted": "var(--info)",
        "Pending": "var(--warning)", "Failed": "var(--danger)",
        "Repealed": "var(--text-muted)",
    }.get(status, "var(--text)")

    return (
        f'<tr id="tracker-row-{i}">'
        f'<td><strong>{state}</strong></td>'
        f'<td style="font-size:12px;">{scope}</td>'
        f'<td style="font-size:12px;max-width:200px;overflow:hidden;'
        f'    text-overflow:ellipsis;white-space:nowrap;"'
        f'    title="{law}">{law}</td>'
        f'<td style="font-size:12px;">{bill}</td>'
        f'<td style="font-size:12px;">{date}</td>'
        f'<td><span style="color:{status_color};font-size:12px;">{status}</span></td>'
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
            '<tr><td colspan="8" style="text-align:center;padding:20px;">'
            'No records. Add a row or import a CSV.</td></tr>'
        )
    return "".join(
        _tracker_row_html(i, r, editing=(i == edit_idx))
        for i, r in enumerate(rows)
    )


def _tracker_csv_to_records() -> list[dict]:
    """Convert ai_law_tracker.csv rows into the record format seed_from_tracker expects.

    Returns list of dicts with keys:
        state, state_code, ai_scope, law_name, law_url, bill_id,
        effective_date, key_requirements, enforcement
    """
    from src.core.us_states import STATE_CODES

    tracker_rows = _read_tracker()
    records = []
    for row in tracker_rows:
        state_name = row.get("State/Terr", "").strip()
        state_code = STATE_CODES.get(state_name, "")
        # Also handle if someone puts the 2-letter code directly
        if not state_code and len(state_name) == 2:
            state_code = state_name.upper()

        records.append({
            "state": state_name,
            "state_code": state_code,
            "ai_scope": row.get("AI Scope", ""),
            "law_name": row.get("Relevant Law", ""),
            "law_url": row.get("Source URL", ""),
            "bill_id": row.get("Bill ID", ""),
            "effective_date": row.get("Effective Date", ""),
            "key_requirements": row.get("Key Requirements", ""),
            "enforcement": row.get("Enforcements Penalties", ""),
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
            if q_lower in (r.get("State/Terr", "") + r.get("Relevant Law", "")
                           + r.get("Bill ID", "") + r.get("AI Scope", "")).lower()
        ]
    else:
        rows_filtered = rows
    return HTMLResponse(_tracker_table_body(rows_filtered))


@router.get("/api/tracker/{idx}/edit")
def tracker_edit_form(idx: int) -> HTMLResponse:
    """Return the table body with one row in edit mode."""
    rows = _read_tracker()
    if idx < 0 or idx >= len(rows):
        return HTMLResponse('<tr><td colspan="8">Row not found.</td></tr>')
    return HTMLResponse(_tracker_table_body(rows, edit_idx=idx))


@router.post("/api/tracker/{idx}/edit")
async def tracker_save_row(idx: int, request: Request) -> HTMLResponse:
    """Save edits to a tracker row and return the refreshed table body."""
    rows = _read_tracker()
    if idx < 0 or idx >= len(rows):
        return HTMLResponse('<tr><td colspan="8">Row not found.</td></tr>')

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
        return HTMLResponse('<tr><td colspan="8">Row not found.</td></tr>')
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
    if not new_row.get("State/Terr"):
        new_row["State/Terr"] = "XX"
    if not new_row.get("Status"):
        new_row["Status"] = "Active"
    rows.append(new_row)
    _write_tracker(rows)
    return HTMLResponse(_tracker_table_body(rows))


@router.get("/api/tracker/export")
def tracker_export() -> StreamingResponse:
    """Download the tracker CSV."""
    import io
    import csv

    rows = _read_tracker()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TRACKER_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=ai_law_tracker.csv"},
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

    missing = set(TRACKER_FIELDS) - set(reader.fieldnames)
    if missing:
        return HTMLResponse(
            f'<div class="result-panel error">'
            f'Missing columns: {html_escape(", ".join(sorted(missing)))}. '
            f'Expected: {html_escape(", ".join(TRACKER_FIELDS))}'
            f'</div>'
        )

    rows = []
    for r in reader:
        clean = {f: (r.get(f) or "").strip() for f in TRACKER_FIELDS}
        if clean.get("State/Terr"):
            rows.append(clean)

    _write_tracker(rows)
    return HTMLResponse(
        f'<div class="result-panel success" style="margin-bottom:8px;">'
        f'Imported <strong>{len(rows)}</strong> records.</div>'
        + _tracker_table_body(rows)
    )
