"""Backfill structured URLs from policy_navigator_urls.csv.

Reads the CSV, classifies each URL into three structured columns, and
updates document_families in the Regs Checker database.

The three columns:
  - primary_source_url:   Direct .gov / legislature link to official bill text
  - orrick_reference_url: Orrick AI Law Center jurisdiction page
  - iapp_reference_url:   Static IAPP US AI Legislation Tracker PDF

Usage:
    python -m src.scripts.backfill_urls [--csv PATH] [--dry-run]

The script resolves CSV law_id → document_families.id via the
law_document_bridge table in Policy Navigator. If no bridge is available
(e.g. local dev), it falls back to matching on title + bill_number.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

import structlog
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.core.config import settings

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORRICK_BASE_URL = "https://ai-law-center.orrick.com"

# Domains that indicate a real government / official legislative source
_OFFICIAL_DOMAIN_PATTERNS = [
    ".gov",
    ".us/",
    "legislature.",
    "legis.",
    "capitol.",
    "lis.",
    "leg.",
    "legiscan.com",
    "cppa.ca.gov",
    "njoag.gov",
    "calcivilrights.ca.gov",
]

# Patterns that indicate a stub / placeholder URL (not real bill text)
_STUB_PATTERNS = [
    "orrick.com/ai",
    "(IAPP tracker PDF)",
    "iapp tracker",
]

# ---------------------------------------------------------------------------
# Jurisdiction inference
# ---------------------------------------------------------------------------

# State name → (code, Orrick slug)
_STATE_MAP: dict[str, tuple[str, str]] = {
    "alabama": ("AL", "alabama"),
    "alaska": ("AK", "alaska"),
    "arizona": ("AZ", "arizona"),
    "arkansas": ("AR", "arkansas"),
    "california": ("CA", "california"),
    "colorado": ("CO", "colorado"),
    "connecticut": ("CT", "connecticut"),
    "delaware": ("DE", "delaware"),
    "florida": ("FL", "florida"),
    "georgia": ("GA", "georgia"),
    "hawaii": ("HI", "hawaii"),
    "idaho": ("ID", "idaho"),
    "illinois": ("IL", "illinois"),
    "indiana": ("IN", "indiana"),
    "iowa": ("IA", "iowa"),
    "kansas": ("KS", "kansas"),
    "kentucky": ("KY", "kentucky"),
    "louisiana": ("LA", "louisiana"),
    "maine": ("ME", "maine"),
    "maryland": ("MD", "maryland"),
    "massachusetts": ("MA", "massachusetts"),
    "michigan": ("MI", "michigan"),
    "minnesota": ("MN", "minnesota"),
    "mississippi": ("MS", "mississippi"),
    "missouri": ("MO", "missouri"),
    "montana": ("MT", "montana"),
    "nebraska": ("NE", "nebraska"),
    "nevada": ("NV", "nevada"),
    "new hampshire": ("NH", "new-hampshire"),
    "new jersey": ("NJ", "new-jersey"),
    "new mexico": ("NM", "new-mexico"),
    "new york": ("NY", "new-york"),
    "north carolina": ("NC", "north-carolina"),
    "north dakota": ("ND", "north-dakota"),
    "ohio": ("OH", "ohio"),
    "oklahoma": ("OK", "oklahoma"),
    "oregon": ("OR", "oregon"),
    "pennsylvania": ("PA", "pennsylvania"),
    "rhode island": ("RI", "rhode-island"),
    "south carolina": ("SC", "south-carolina"),
    "south dakota": ("SD", "south-dakota"),
    "tennessee": ("TN", "tennessee"),
    "texas": ("TX", "texas"),
    "utah": ("UT", "utah"),
    "vermont": ("VT", "vermont"),
    "virginia": ("VA", "virginia"),
    "washington": ("WA", "washington"),
    "west virginia": ("WV", "west-virginia"),
    "wisconsin": ("WI", "wisconsin"),
    "wyoming": ("WY", "wyoming"),
}

# URL domain fragments → state name (for inferring from URL)
_URL_DOMAIN_TO_STATE: dict[str, str] = {
    "alison.legislature.state.al.us": "alabama",
    "azleg.gov": "arizona",
    "arkleg.state.ar.us": "arkansas",
    "leginfo.legislature.ca.gov": "california",
    "cppa.ca.gov": "california",
    "calcivilrights.ca.gov": "california",
    "leg.colorado.gov": "colorado",
    "cga.ct.gov": "connecticut",
    "delcode.delaware.gov": "delaware",
    "flrules.org": "florida",
    "myfloridahouse.gov": "florida",
    "capitol.hawaii.gov": "hawaii",
    "data.capitol.hawaii.gov": "hawaii",
    "legislature.idaho.gov": "idaho",
    "ilga.gov": "illinois",
    "legis.iowa.gov": "iowa",
    "kslegislature.gov": "kansas",
    "apps.legislature.ky.gov": "kentucky",
    "legis.la.gov": "louisiana",
    "legislature.maine.gov": "maine",
    "mgaleg.maryland.gov": "maryland",
    "malegislature.gov": "massachusetts",
    "legislature.mi.gov": "michigan",
    "revisor.mn.gov": "minnesota",
    "billstatus.ls.state.ms.us": "mississippi",
    "revisor.mo.gov": "missouri",
    "docs.legmt.gov": "montana",
    "bills.legmt.gov": "montana",
    "nebraskalegislature.gov": "nebraska",
    "leg.state.nv.us": "nevada",
    "gc.nh.gov": "new hampshire",
    "legiscan.com/NH": "new hampshire",
    "njoag.gov": "new jersey",
    "nmlegis.gov": "new mexico",
    "nysenate.gov": "new york",
    "ndlegis.gov": "north dakota",
    "oklegislature.gov": "oklahoma",
    "olis.oregonlegislature.gov": "oregon",
    "legis.state.pa.us": "pennsylvania",
    "status.rilegislature.gov": "rhode island",
    "scstatehouse.gov": "south carolina",
    "mylrc.sdlegislature.gov": "south dakota",
    "capitol.tn.gov": "tennessee",
    "capitol.texas.gov": "texas",
    "le.utah.gov": "utah",
    "legislature.vermont.gov": "vermont",
    "lis.virginia.gov": "virginia",
    "legacylis.virginia.gov": "virginia",
    "law.lis.virginia.gov": "virginia",
    "lis.blob.core.windows.net": "virginia",
    "lawfilesext.leg.wa.gov": "washington",
    "app.leg.wa.gov": "washington",
    "wvlegislature.gov": "west virginia",
    "docs.legis.wisconsin.gov": "wisconsin",
}

# Abbreviation → full state name (for matching in titles)
_ABBREV_TO_STATE: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "montana", "NE": "nebraska", "NV": "nevada",
    "NH": "new hampshire", "NJ": "new jersey", "NM": "new mexico", "NY": "new york",
    "NC": "north carolina", "ND": "north dakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "west virginia",
    "WI": "wisconsin", "WY": "wyoming",
}


def infer_jurisdiction(title: str, source_url: str) -> str | None:
    """Infer the state name from the title and/or URL.

    Returns lowercase state name (e.g. "colorado") or None if unresolvable.
    """
    combined = f"{title} {source_url}".lower()

    # Strategy 1: Check URL domain fragments
    for domain_frag, state in _URL_DOMAIN_TO_STATE.items():
        if domain_frag.lower() in source_url.lower():
            return state

    # Strategy 2: Look for full state names in the title
    for state_name in sorted(_STATE_MAP.keys(), key=len, reverse=True):
        if state_name in combined:
            return state_name

    # Strategy 3: Check for state abbreviations in title (e.g. "Cal.", "N.Y.")
    # Match patterns like "Tex. Penal Code", "Cal. Bus. & Prof. Code"
    abbrev_pattern = re.findall(r"\b([A-Z]{2})\b", title)
    for abbr in abbrev_pattern:
        if abbr in _ABBREV_TO_STATE:
            return _ABBREV_TO_STATE[abbr]

    # Strategy 4: Statute citation patterns
    citation_patterns = {
        r"C\.R\.S\.": "colorado",
        r"Conn\. Gen\. Stat": "connecticut",
        r"Del\. Code": "delaware",
        r"Ga\. Code": "georgia",
        r"Ind\. Code": "indiana",
        r"Iowa Code": "iowa",
        r"KRS\b": "kentucky",
        r"La\. Rev\. Stat": "louisiana",
        r"MRSA\b": "maine",
        r"Md\. Code": "maryland",
        r"Minn\. Stat": "minnesota",
        r"R\.S\.Mo": "missouri",
        r"MCA\b": "montana",
        r"Nev\. Rev\. Stat": "nevada",
        r"N\.H\. Rev\. Stat": "new hampshire",
        r"N\.J\.": "new jersey",
        r"N\.M\. Stat": "new mexico",
        r"N\.Y\.": "new york",
        r"N\.D\. Cent\. Code": "north dakota",
        r"Tex\.": "texas",
        r"Utah Code": "utah",
        r"Va\. Code": "virginia",
        r"Wash\. Rev\. Code": "washington",
        r"W\. Va\. Code": "west virginia",
        r"Wis\. Stat": "wisconsin",
        r"Wy\. Code": "wyoming",
    }
    for pattern, state in citation_patterns.items():
        if re.search(pattern, title):
            return state

    # NYC special case
    if "nyc" in combined or "local law 144" in combined:
        return "new york"

    # Bill number prefixes were considered but not reliable enough for general use.
    return None


# Manual overrides for CSV rows where titles are too truncated to infer.
# These were identified by cross-referencing adjacent rows and bill context.
_LAW_ID_STATE_OVERRIDES: dict[int, str] = {
    15: "california",    # AI Transparency Act Cal. Bus. & Prof. Code
    25: "california",    # Employment and System (CA context from adjacent rows)
    43: "colorado",      # HB 94 (CO context)
    58: "connecticut",   # Data Privacy Act SB1295
    82: "illinois",      # to Right of Publicity HB 4875
    92: "kentucky",      # HB 823
    114: "minnesota",    # Media & Prohibiting Social Media SF 4097
    115: "minnesota",    # Non-Consensual Dissemination of HF 1370
    131: "nevada",       # AI for School Counseling Chapter 391 of NRS
    142: "new hampshire",  # Hampshire Deepfake Act HB 1432
    145: "new jersey",   # criminal penalties for A3540
    153: "new york",     # Automated Employment Decision- A433
    154: "new york",     # for the Creation and Use General Obligations
    158: "new york",     # Performer Disclosures S8420A
    160: "new york",     # The LOADinG Act: Legislative S 7543B
    161: "new york",     # to Deceased S8391
    163: "new york",     # York State Fashion Workers S 9832
    180: "oregon",       # Use of AI in Campaign SB 1571
    183: "rhode island", # Deceptive and Fraudulent Synthetic Media
    185: "rhode island", # HB 1709
    196: "texas",        # AI Sexual Material Harmful to HB 1999
    214: "utah",         # Artificial Pornographic Images HB 148
    215: "utah",         # Explicit Minor HB 238
    223: "vermont",      # An Act Relating to the Use and H 410
    224: "virginia",     # HB 2121
    225: "virginia",     # HB 2250
    232: "virginia",     # HB 1168
    242: "wisconsin",    # to the CSAM Statute SB 314
}


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------


def is_stub_url(url: str) -> bool:
    """Check if a URL is a placeholder/stub (not real bill text)."""
    url_lower = url.lower().strip()
    return any(stub in url_lower for stub in _STUB_PATTERNS)


def is_official_source(url: str) -> bool:
    """Check if a URL points to an official government/legislative source."""
    url_lower = url.lower()
    if is_stub_url(url):
        return False
    return any(domain in url_lower for domain in _OFFICIAL_DOMAIN_PATTERNS)


def is_orrick_pdf(url: str) -> bool:
    """Check if a URL is an Orrick-hosted PDF (not a stub)."""
    url_lower = url.lower()
    return "infobytes.orrick.com" in url_lower or (
        "orrick.com" in url_lower
        and url_lower.endswith(".pdf")
    )


def build_orrick_reference_url(state_name: str) -> str:
    """Build the Orrick AI Law Center URL for a given state."""
    _, slug = _STATE_MAP.get(state_name, ("", state_name.lower().replace(" ", "-")))
    return f"{ORRICK_BASE_URL}/{slug}/"


def classify_url(source_url: str, state_name: str | None) -> dict[str, str | None]:
    """Route a single CSV source_url into the three structured columns.

    Returns dict with keys: primary_source_url, orrick_reference_url,
    iapp_reference_url.
    """
    result: dict[str, str | None] = {
        "primary_source_url": None,
        "orrick_reference_url": None,
        "iapp_reference_url": None,  # Populated by extract_iapp_links.py
    }

    # Orrick reference URL (generated from state)
    if state_name:
        result["orrick_reference_url"] = build_orrick_reference_url(state_name)

    # Primary source URL
    url = source_url.strip()
    if is_stub_url(url):
        # Stub — leave primary_source_url empty so the Fallback Agent
        # knows it needs to go hunting
        result["primary_source_url"] = None
    elif is_official_source(url):
        # Real .gov / legislature link
        result["primary_source_url"] = url
    elif is_orrick_pdf(url):
        # Orrick-hosted PDF of the bill text — still usable as primary
        result["primary_source_url"] = url
    elif url and "http" in url.lower():
        # Some other URL — keep it as primary
        result["primary_source_url"] = url
    # else: empty or garbage — leave None

    return result


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def parse_csv(csv_path: str) -> list[dict]:
    """Parse the policy_navigator_urls.csv and classify each row.

    Returns list of dicts with keys:
        law_id, bill_number, title, source_url, law_status,
        state_name, primary_source_url, orrick_reference_url,
        iapp_reference_url
    """
    rows = []

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            law_id = row.get("law_id", "").strip()
            if not law_id:
                continue

            title = row.get("title", "").strip()
            source_url = row.get("source_url", "").strip()
            bill_number = row.get("bill_number", "").strip()
            law_status = row.get("law_status", "").strip()

            # Infer jurisdiction (manual override takes priority)
            state_name = _LAW_ID_STATE_OVERRIDES.get(int(law_id))
            if not state_name:
                state_name = infer_jurisdiction(title, source_url)

            # Classify URL into three columns
            urls = classify_url(source_url, state_name)

            rows.append({
                "law_id": int(law_id),
                "bill_number": bill_number,
                "title": title,
                "source_url": source_url,
                "law_status": law_status,
                "state_name": state_name,
                **urls,
            })

    return rows


# ---------------------------------------------------------------------------
# Database update
# ---------------------------------------------------------------------------


def _load_reverse_bridge(target_session) -> dict[int, int]:
    """Load law_document_bridge: law_id → system_a_doc_family_id (reverse lookup).

    The bridge table maps Regs Checker document_families.id → Policy Navigator law_id.
    We reverse it so we can look up: CSV law_id → document_families.id.
    """
    try:
        rows = target_session.execute(
            text("SELECT system_a_doc_family_id, law_id FROM law_document_bridge")
        ).fetchall()
        return {row[1]: row[0] for row in rows}  # law_id → doc_family_id
    except Exception as e:
        logger.warning("bridge_load_failed", error=str(e))
        return {}


def _fallback_match(session, title: str, bill_number: str) -> int | None:
    """Fallback: try to match a CSV row to document_families by title or cite."""
    if bill_number:
        result = session.execute(
            text(
                "SELECT id FROM document_families "
                "WHERE short_cite ILIKE :cite LIMIT 1"
            ),
            {"cite": f"%{bill_number}%"},
        ).scalar()
        if result:
            return result

    if title:
        result = session.execute(
            text(
                "SELECT id FROM document_families "
                "WHERE canonical_title ILIKE :title LIMIT 1"
            ),
            {"title": f"%{title[:60]}%"},
        ).scalar()
        if result:
            return result

    return None


def backfill_urls(
    csv_path: str,
    dry_run: bool = False,
) -> dict:
    """Parse CSV and update document_families with structured URLs.

    Args:
        csv_path: Path to policy_navigator_urls.csv.
        dry_run: If True, print changes without writing to DB.

    Returns:
        Summary dict with counts.
    """
    parsed = parse_csv(csv_path)
    print(f"Parsed {len(parsed)} rows from CSV")

    # Stats
    summary = {
        "total_csv_rows": len(parsed),
        "jurisdiction_resolved": 0,
        "jurisdiction_unresolved": 0,
        "has_primary_url": 0,
        "stub_urls": 0,
        "matched_to_db": 0,
        "unmatched": 0,
        "updated": 0,
        "skipped_dry_run": 0,
    }

    for row in parsed:
        if row["state_name"]:
            summary["jurisdiction_resolved"] += 1
        else:
            summary["jurisdiction_unresolved"] += 1
        if row["primary_source_url"]:
            summary["has_primary_url"] += 1
        else:
            summary["stub_urls"] += 1

    print(
        f"  Jurisdictions resolved: {summary['jurisdiction_resolved']}/{len(parsed)}\n"
        f"  Primary URLs found: {summary['has_primary_url']}\n"
        f"  Stub/missing URLs: {summary['stub_urls']}"
    )

    # Connect to databases
    db_url = settings.database_url
    regs_engine = create_engine(db_url)
    regs_session = sessionmaker(bind=regs_engine)()

    # Try to load bridge from Policy Navigator
    bridge: dict[int, int] = {}
    if settings.policy_navigator_url:
        try:
            pn_engine = create_engine(settings.policy_navigator_url)
            pn_session = sessionmaker(bind=pn_engine)()
            bridge = _load_reverse_bridge(pn_session)
            print(f"  Bridge loaded: {len(bridge)} law_id → doc_family_id mappings")
            pn_session.close()
        except Exception as e:
            print(f"  Warning: Could not load bridge table: {e}")
    else:
        print("  No Policy Navigator URL configured — using fallback matching")

    # Process each row
    print(f"\n{'DRY RUN — ' if dry_run else ''}Updating document_families...")

    for row in parsed:
        law_id = row["law_id"]

        # Resolve law_id → document_families.id
        doc_family_id = bridge.get(law_id)

        if doc_family_id is None:
            # Fallback: match by title/bill_number
            doc_family_id = _fallback_match(
                regs_session, row["title"], row["bill_number"]
            )

        if doc_family_id is None:
            summary["unmatched"] += 1
            logger.debug(
                "csv_row_unmatched",
                law_id=law_id,
                title=row["title"][:60],
            )
            continue

        summary["matched_to_db"] += 1

        if dry_run:
            state_label = row["state_name"] or "?"
            primary = row["primary_source_url"] or "(empty — needs fallback)"
            print(
                f"  [law_id={law_id}] → doc_family_id={doc_family_id} "
                f"[{state_label}] primary={primary[:80]}"
            )
            summary["skipped_dry_run"] += 1
            continue

        # Execute UPDATE
        regs_session.execute(
            text(
                "UPDATE document_families SET "
                "  primary_source_url = :primary, "
                "  orrick_reference_url = :orrick, "
                "  iapp_reference_url = :iapp "
                "WHERE id = :id"
            ),
            {
                "primary": row["primary_source_url"],
                "orrick": row["orrick_reference_url"],
                "iapp": row["iapp_reference_url"],
                "id": doc_family_id,
            },
        )
        summary["updated"] += 1

        if summary["updated"] % 50 == 0:
            regs_session.commit()
            print(f"  ... {summary['updated']} rows updated")

    if not dry_run:
        regs_session.commit()

    regs_session.close()

    # Final report
    print(
        f"\nBackfill complete:"
        f"\n  CSV rows processed:    {summary['total_csv_rows']}"
        f"\n  Matched to DB:         {summary['matched_to_db']}"
        f"\n  Unmatched (no bridge): {summary['unmatched']}"
        f"\n  Updated:               {summary['updated']}"
        f"\n  Dry-run skipped:       {summary['skipped_dry_run']}"
    )

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill structured URLs from policy_navigator_urls.csv"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default="archive/policy_navigator_urls.csv",
        help="Path to the CSV file (default: archive/policy_navigator_urls.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database",
    )
    args = parser.parse_args()

    if not Path(args.csv).exists():
        print(f"Error: CSV file not found: {args.csv}")
        sys.exit(1)

    backfill_urls(csv_path=args.csv, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
