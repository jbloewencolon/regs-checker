"""One-off re-sync: Regs Checker Supabase → Policy Navigator synced_extractions.

Uses REST API (no DB passwords needed). Fetches extractions with joins via
MCP-style pagination, applies payload_adapter, and POSTs to Policy Navigator.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import httpx

from src.core.payload_adapter import adapt_payload_for_sync
from src.core.sync_exclusions import is_excluded

# -- Config --
SOURCE_URL = "https://wjxlimjpaijdogyrqtxc.supabase.co"
SOURCE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndqeGxpbWpwYWlqZG9neXJxdHhjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzMwMTc5MjksImV4cCI6MjA4ODU5MzkyOX0.k5dB87D01PFoqjshGar78kiIAtvyFTJYvce5G-CA88A"

TARGET_URL = "https://aaxxunfarlhmydvohsrm.supabase.co"
TARGET_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFheHh1bmZhcmxobXlkdm9oc3JtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE3MTQwOTUsImV4cCI6MjA4NzI5MDA5NX0.DV072RGj8f4M66GBmd3BBodzh98wHPiyQadi3cN8ZSY"

BATCH_SIZE = 500


def _headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def main():
    source = httpx.Client(timeout=60.0, headers=_headers(SOURCE_KEY))
    target = httpx.Client(timeout=60.0, headers=_headers(TARGET_KEY))

    # Step 1: Load bridge from Policy Navigator
    print("Loading bridge mapping...")
    resp = target.get(
        f"{TARGET_URL}/rest/v1/law_document_bridge",
        params={"select": "system_a_doc_family_id,law_id", "limit": "10000"},
    )
    resp.raise_for_status()
    bridge = {r["system_a_doc_family_id"]: r["law_id"] for r in resp.json()}
    print(f"  Bridge: {len(bridge)} mappings")

    # Step 2: Fetch extractions from source in pages
    print("Fetching extractions from Regs Checker...")
    all_extractions = []
    offset = 0
    while True:
        resp = source.get(
            f"{SOURCE_URL}/rest/v1/extractions",
            params={
                "select": "id,extraction_type,payload,evidence_spans,confidence_score,confidence_tier,review_status,created_at,source_record_id",
                "order": "id.asc",
                "limit": str(BATCH_SIZE),
                "offset": str(offset),
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        all_extractions.extend(batch)
        offset += len(batch)
        print(f"  Fetched {len(all_extractions)} extractions...")

    print(f"  Total: {len(all_extractions)} extractions")

    # Step 3: Fetch source_record → document_version mapping
    print("Fetching source record → document family mappings...")
    nsr_to_family = {}
    nsr_section = {}
    nsr_text = {}
    offset = 0
    while True:
        resp = source.get(
            f"{SOURCE_URL}/rest/v1/normalized_source_records",
            params={
                "select": "id,document_version_id,section_path,text_content",
                "order": "id.asc",
                "limit": str(BATCH_SIZE),
                "offset": str(offset),
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for r in batch:
            nsr_to_family[r["id"]] = r["document_version_id"]
            nsr_section[r["id"]] = r.get("section_path")
            # Truncate text to avoid huge payloads
            txt = r.get("text_content") or ""
            nsr_text[r["id"]] = txt[:500] if txt else None
        offset += len(batch)
    print(f"  {len(nsr_to_family)} source records loaded")

    # Step 4: Fetch document_version → family_id mapping
    print("Fetching document version → family mappings...")
    dv_to_family = {}
    offset = 0
    while True:
        resp = source.get(
            f"{SOURCE_URL}/rest/v1/document_versions",
            params={
                "select": "id,family_id",
                "order": "id.asc",
                "limit": str(BATCH_SIZE),
                "offset": str(offset),
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        for r in batch:
            dv_to_family[r["id"]] = r["family_id"]
        offset += len(batch)
    print(f"  {len(dv_to_family)} document versions loaded")

    # Step 5: Load law_id → jurisdiction_code from Policy Navigator
    print("Loading jurisdiction codes...")
    law_jurisdiction = {}
    resp = target.get(
        f"{TARGET_URL}/rest/v1/rpc/get_law_jurisdictions",
        params={},
    )
    # Fallback: fetch via MCP-populated dict (hardcoded from fact_laws join)
    # We'll fetch it via the anon-accessible view or direct query
    # Since fact_laws may not be anon-accessible, use a simple approach:
    resp = target.get(
        f"{TARGET_URL}/rest/v1/fact_laws",
        params={"select": "law_id,dim_jurisdictions(state_abbrev)", "limit": "10000"},
    )
    if resp.status_code == 200 and resp.json():
        for r in resp.json():
            jur = r.get("dim_jurisdictions")
            if jur and isinstance(jur, dict):
                law_jurisdiction[r["law_id"]] = jur["state_abbrev"]

    # If RLS blocked fact_laws, use the bridge law_ids with a fallback
    if not law_jurisdiction:
        print("  fact_laws not accessible via anon, using direct SQL fallback...")
        # Hardcode from the MCP query results
        _jur_map_raw = {1:"AL",2:"AL",3:"AZ",4:"AZ",5:"AZ",6:"AR",7:"AR",8:"AR",9:"AR",10:"AR",13:"CA",14:"CA",15:"CA",16:"CA",17:"CA",18:"CA",19:"CA",20:"CA",21:"CA",22:"CA",23:"CA",24:"CA",25:"CA",26:"CA",28:"CA",29:"CA",31:"CA",32:"CA",33:"CA",34:"CA",35:"CA",36:"CA",37:"CA",38:"CA",39:"CA",40:"CA",42:"CA",43:"CA",44:"CA",45:"CA",48:"CO",49:"CO",50:"CO",51:"CO",52:"CO",54:"CO",56:"CO",57:"CT",58:"CT",59:"CT",60:"CT",61:"CT",62:"DE",63:"DE",64:"DE",65:"FL",66:"FL",67:"FL",68:"FL",69:"GA",70:"GA",71:"HI",72:"HI",73:"HI",74:"ID",75:"ID",76:"ID",77:"ID",78:"ID",79:"IL",80:"IL",82:"IL",83:"IL",85:"IL",86:"IL",87:"IN",88:"IN",89:"IA",90:"IA",91:"IA",92:"IA",93:"KS",94:"KY",95:"KY",96:"KY",97:"KY",100:"LA",101:"LA",102:"ME",103:"MD",104:"MD",105:"MD",106:"MD",107:"MD",108:"MD",109:"MA",110:"MA",111:"MI",112:"MI",113:"MN",114:"MN",115:"MN",116:"MN",117:"MN",118:"MS",119:"MO",120:"MT",121:"MT",122:"MT",123:"MT",124:"MT",125:"MT",126:"MT",127:"MT",128:"NE",129:"NE",130:"NE",131:"NV",132:"NV",133:"NV",134:"NV",135:"NV",136:"NV",137:"NV",138:"NV",139:"NV",140:"NH",141:"NH",142:"NH",143:"NH",144:"NH",145:"NJ",146:"NJ",147:"NJ",148:"NM",149:"NM",150:"NY",151:"NY",152:"NY",153:"NY",154:"NY",155:"NY",156:"NY",157:"NY",158:"NY",159:"NY",160:"NY",161:"NY",162:"NY",163:"NY",164:"NY",165:"NY",166:"NY",167:"NM",168:"NC",170:"ND",171:"ND",172:"ND",173:"ND",174:"ND",175:"ND",176:"OK",177:"OK",178:"OK",179:"OR",180:"OR",181:"PA",183:"RI",184:"RI",185:"RI",186:"RI",187:"RI",188:"SC",189:"SD",190:"SD",191:"TN",192:"TN",193:"TN",194:"TX",195:"TX",196:"TX",197:"TX",198:"TX",199:"TX",200:"TX",201:"TX",202:"TX",205:"TX",208:"TX",209:"TX",210:"TX",211:"UT",212:"UT",213:"UT",214:"UT",215:"UT",217:"UT",218:"UT",220:"UT",221:"UT",222:"VT",223:"VT",224:"VT",225:"VT",226:"VT",227:"VA",228:"VA",229:"VA",230:"VA",231:"VA",232:"VA",233:"VA",234:"VA",235:"WA",236:"WA",237:"WA",238:"WV",239:"WV",240:"WI",241:"WI",242:"WI",243:"WY",244:"WY",732:"CA",733:"CA",734:"TX",735:"TX"}
        law_jurisdiction = _jur_map_raw

    print(f"  {len(law_jurisdiction)} law → jurisdiction mappings")

    # Step 6: Build synced rows
    print("Building synced_extractions rows...")
    synced_rows = []
    skipped_no_bridge = 0
    skipped_excluded = 0
    skipped_no_nsr = 0
    skipped_no_jurisdiction = 0

    for ext in all_extractions:
        src_record_id = ext["source_record_id"]
        dv_id = nsr_to_family.get(src_record_id)
        if dv_id is None:
            skipped_no_nsr += 1
            continue

        family_id = dv_to_family.get(dv_id)
        if family_id is None:
            skipped_no_nsr += 1
            continue

        law_id = bridge.get(family_id)
        if law_id is None:
            skipped_no_bridge += 1
            continue

        if is_excluded(law_id):
            skipped_excluded += 1
            continue

        jurisdiction_code = law_jurisdiction.get(law_id)
        if not jurisdiction_code:
            skipped_no_jurisdiction += 1
            continue

        # Adapt payload
        raw_payload = ext["payload"] or {}
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        adapted = adapt_payload_for_sync(ext["extraction_type"], raw_payload)

        synced_rows.append({
            "system_a_extraction_id": ext["id"],
            "law_id": law_id,
            "jurisdiction_code": jurisdiction_code,
            "extraction_type": ext["extraction_type"],
            "payload": adapted,
            "evidence_spans": ext["evidence_spans"],
            "confidence_score": float(ext["confidence_score"]) if ext["confidence_score"] is not None else None,
            "confidence_tier": ext["confidence_tier"],
            "section_reference": nsr_section.get(src_record_id),
            "source_text_excerpt": nsr_text.get(src_record_id),
            "system_a_created_at": ext["created_at"],
            "review_status": ext["review_status"],
            "synced_at": datetime.now(timezone.utc).isoformat(),
        })

    print(f"  Ready to sync: {len(synced_rows)}")
    print(f"  Skipped (no bridge):        {skipped_no_bridge}")
    print(f"  Skipped (excluded):         {skipped_excluded}")
    print(f"  Skipped (no NSR):           {skipped_no_nsr}")
    print(f"  Skipped (no jurisdiction):  {skipped_no_jurisdiction}")

    # Step 6: POST to Policy Navigator in batches
    print(f"\nPushing {len(synced_rows)} rows to Policy Navigator...")
    total_pushed = 0
    errors = 0

    for i in range(0, len(synced_rows), BATCH_SIZE):
        batch = synced_rows[i : i + BATCH_SIZE]
        resp = target.post(
            f"{TARGET_URL}/rest/v1/synced_extractions",
            json=batch,
            headers={"Prefer": "resolution=ignore-duplicates,return=minimal"},
        )
        if resp.status_code in (200, 201):
            total_pushed += len(batch)
            print(f"  Pushed {total_pushed}/{len(synced_rows)}...")
        else:
            errors += 1
            print(f"  ERROR batch {i//BATCH_SIZE}: {resp.status_code} {resp.text[:200]}")

    print(f"\n{'='*60}")
    print(f"Synced:   {total_pushed} rows")
    print(f"Errors:   {errors} batches")
    print("Done.")


if __name__ == "__main__":
    main()
