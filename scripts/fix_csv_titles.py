"""Fix fact_laws.csv title column using correct titles from legacy document_families.

The CSV was seeded from a corrupted PDF extraction that truncated/mangled law titles.
The legacy document_families (ID 1-186, no canonical_law_id) have the correct titles
from the original Orrick seeding.

This script:
1. Reads legacy family data from Supabase (hardcoded from SQL export)
2. Fuzzy-matches CSV Orrick rows to legacy families by jurisdiction
3. Replaces mangled CSV titles with correct legacy titles
4. Reports IAPP rows (no legacy match expected — they're bill-number-only)
"""

import csv
import re
from difflib import SequenceMatcher
from pathlib import Path

# Legacy families with correct titles (from Supabase SQL export)
# Format: (legacy_id, jurisdiction, correct_title)
LEGACY_FAMILIES = [
    (1, "Alabama", "Alabama Child Protection Act of 2024"),
    (2, "Alabama", "Alabama Materially Deceptive Election Media Law"),
    (3, "Arizona", "Arizona General Deepfake Law"),
    (4, "Arizona", "Arizona Political Deepfake Law"),
    (5, "Arizona", "Amendment of Arizona Intimate Images Law"),
    (6, "Arkansas", "Ownership of Model Training and Generated Content"),
    (7, "Arkansas", "Amendment of Arkansas CSAM Laws"),
    (8, "California", "California Bot Act"),
    (9, "California", "California Government AI Inventory Law"),
    (10, "California", "Deceptive Media in Election Advertisements"),
    (11, "California", "Digital Identity Theft Act"),
    (12, "California", "Amendment of California Law Governing Distribution of Intimate Images"),
    (13, "California", "Amendment of California CSAM Laws"),
    (14, "California", "AI Definition Bill"),
    (15, "California", "Artificial Intelligence in Health Care Services"),
    (16, "California", "AI Healthcare Utilization Law"),
    (17, "California", "Generative Artificial Intelligence Accountability Act"),
    (18, "California", "California Consumer Privacy Act"),
    (19, "California", "AI Call Disclosures Law"),
    (20, "California", "Amendment to the Political Reform Act"),
    (21, "California", "Defending Democracy from Deepfake"),
    (22, "California", "Deception Act of 2024"),
    (23, "California", "Amendment to Deceased Personality Protections"),
    (24, "California", "Replica of Voice or Likeness Law"),
    (25, "California", "Employment Regulations Regarding Automated-Decision Systems"),
    (26, "California", "Transparency in Frontier Artificial Intelligence Act"),
    (27, "California", "Artificial Intelligence Training Data Transparency Act"),
    (28, "California", "Health Advice From Artificial Intelligence"),
    (29, "California", "Civil Actions"),
    (30, "California", "California Companion Chatbot"),
    (31, "California", "Law Enforcement Usage of Artificial Intelligence"),
    (32, "California", "Data Broker Registration AI Disclosures"),
    (33, "California", "Real Estate Digitally Altered Images Disclosures"),
    (34, "California", "Cartwright Act Common Pricing Algorithm Amendment"),
    (35, "California", "California AI Transparency Act"),
    (36, "California", "California Consumer Privacy Act Regulations"),
    (37, "Colorado", "Colorado Protecting Consumers from Unfair Discrimination in Insurance Practices"),
    (38, "Colorado", "Colorado Privacy Act"),
    (39, "Colorado", "Colorado Candidate Election Deepfake Disclosures Law"),
    (40, "Colorado", "Preventing Unauthorized Disclosure of Intimate Digital Depictions Act"),
    (41, "Colorado", "Colorado AI Act"),
    (42, "Connecticut", "Transportation Network Company Dynamic Pricing"),
    (43, "Connecticut", "Connecticut Act Concerning AI, Automated Decision-Making and Personal Data Privacy"),
    (44, "Connecticut", "Connecticut Data Privacy Act"),
    (45, "Delaware", "Delaware Artificial Intelligence Commission Act"),
    (46, "Delaware", "Amendment to the Delaware Code Relating to Deepfakes"),
    (47, "Delaware", "Delaware Personal Data Privacy Act"),
    (48, "Florida", "Florida Act Relating to AI Use in Political Advertising"),
    (49, "Florida", "Florida Digital Bill of Rights"),
    (50, "Florida", "Amendment of Florida CSAM Laws"),
    (51, "Georgia", "Prohibition on Nude or Sexually Explicit Electronic Transmissions"),
    (52, "Georgia", "Amendment of Georgia CSAM Law"),
    (53, "Hawaii", "Amendment to Intimate Image Law"),
    (54, "Hawaii", "Deceptive Media in Election Advertisements"),
    (55, "Idaho", "Relating to Personhood"),
    (56, "Idaho", "Idaho Explicit Synthetic Media law"),
    (57, "Illinois", "Artificial Intelligence Video Interview Act"),
    (58, "Illinois", "Amendment to Right of Publicity Act"),
    (59, "Illinois", "Digital Voice and Likeness Protection Act"),
    (60, "Illinois", "The Wellness and Oversight for Psychological Resources Act"),
    (61, "Illinois", "Amendment to the Illinois Human Rights Act"),
    (62, "Indiana", "Amendment of Indiana Law Governing Distribution of Intimate Images"),
    (63, "Indiana", "Indiana Consumer Data Protection Act"),
    (64, "Iowa", "Sexual Exploitation of a Minor"),
    (65, "Iowa", "Harassment"),
    (66, "Kansas", "AI Platforms of Concern"),
    (67, "Kentucky", "Amendment to CSAM Law"),
    (68, "Kentucky", "Amendment to Intimate Images law"),
    (69, "Kentucky", "Government Use of AI Law"),
    (70, "Kentucky", "AI Electioneering Communications"),
    (71, "Kentucky", "Kentucky Consumer Data Protection Act"),
    (72, "Louisiana", "Louisiana Deepfake Law"),
    (73, "Louisiana", "Louisiana AI Intimate Image Law"),
    (74, "Maine", "Communications with Consumers via AI"),
    (75, "Maryland", "Amendment to the Maryland CSAM Statute"),
    (76, "Maryland", "Maryland AI Governance Act of 2024"),
    (77, "Maryland", "Maryland Online Data Privacy Act"),
    (78, "Maryland", "AI Utilization Review"),
    (79, "Massachusetts", "Amendment to the Massachusetts Intimate Images Law"),
    (80, "Michigan", "AI Political Disclaimer Law"),
    (81, "Michigan", "AI Political Deepfake Law"),
    (82, "Minnesota", "Non-Consensual Dissemination of a Deepfake"),
    (83, "Minnesota", "Prohibiting Social Media Manipulation Act"),
    (84, "Minnesota", "Minnesota Consumer Data Privacy Act"),
    (85, "Minnesota", "Amendment to the Minnesota CSAM Statute"),
    (86, "Mississippi", "AI Political Deepfake Law"),
    (87, "Missouri", "Pornography and Related Offenses"),
    (88, "Montana", "Montana Consumer Data Privacy Act"),
    (89, "Montana", "Right to Compute Act"),
    (90, "Montana", "Montana Explicit Synthetic Media Law"),
    (91, "Montana", "Privacy in Communications Law"),
    (92, "Montana", "Montana Government AI Use Law"),
    (93, "Montana", "AI Deepfakes in Elections"),
    (94, "Montana", "Property Right in Use of Names, Voices, and Visual Likenesses"),
    (95, "Nebraska", "Nebraska Data Privacy Act"),
    (96, "Nebraska", "Amendment of Nebraska CSAM Laws"),
    (97, "Nevada", "AI for Mental and Behavioral Health Care"),
    (98, "Nevada", "AI for School Counseling"),
    (99, "Nevada", "General AI Chatbots and Mental Health Services"),
    (100, "Nevada", "Nevada AI Political Advertising Law"),
    (101, "New Hampshire", "New Hampshire AI Political Advertising Law"),
    (102, "New Hampshire", "New Hampshire State Agency AI Bill"),
    (103, "New Hampshire", "New Hampshire Deepfake Act"),
    (104, "New Hampshire", "New Hampshire Privacy Act"),
    (105, "New Hampshire", "Responsive Generative Communication Law"),
    (106, "New Jersey", "New Jersey Data Privacy Act"),
    (107, "New Jersey", 'Establishes criminal penalties for production or dissemination of "deepfakes."'),
    (108, "New Jersey", "Rules Pertaining to Disparate Impact Discrimination (N.J.A.C. 13:16)"),
    (109, "New Mexico", "New Mexico Campaign Reporting Act Amendment"),
    (110, "New York", "NYC AI Employment Law"),
    (111, "New York", "Amendment to the New York Statute Prohibiting Unlawful Dissemination or Publication of Intimate Images"),
    (112, "New York", "Artificial Intelligence Deceptive Practices Act"),
    (113, "New York", "The LOADinG Act: Legislative Oversight Of Automated Decision-Making in Government Act"),
    (114, "New York", "Contracts for the Creation and Use of Digital Replicas"),
    (115, "New York", "New York State Fashion Workers Act"),
    (116, "New York", "Automated Employment Decision-Making in State Government"),
    (117, "New York", "Artificial Intelligence Companion Models"),
    (118, "New York", "New York Algorithmic Pricing Disclosure Act"),
    (119, "New York", "Amendment to Deceased Personality Protections"),
    (120, "New York", "New York Landlord Algorithmic Pricing Law"),
    (121, "New York", "Government-Related AI Employment Protections"),
    (122, "New York", "Synthetic Performer Disclosures"),
    (123, "New York", "The Responsible AI Safety and Education (RAISE) Act"),
    (124, "North Carolina", "Amendment of North Carolina CSAM Laws"),
    (125, "North Carolina", "North Carolina Intimate Images Laws"),
    (126, "North Dakota", "North Dakota Unmanned Aerial Vehicle and Robot Law"),
    (127, "North Dakota", "North Dakota Harassment Law"),
    (128, "North Dakota", "North Dakota Stalking Law"),
    (129, "North Dakota", "AI Political Advertising Disclaimer Law"),
    (130, "North Dakota", "AI CSAM Amendments"),
    (131, "North Dakota", "An Act Relating to Sexually Expressive Images"),
    (132, "Oklahoma", "Amendment to Oklahoma CSAM Laws"),
    (133, "Oklahoma", "Amendment of Oklahoma Law Governing Distribution of Intimate Images"),
    (134, "Oregon", "Use of AI in Campaign Communications Law"),
    (135, "Oregon", "Oregon Consumer Privacy Act"),
    (136, "Pennsylvania", "Amendment of Pennsylvania CSAM Laws"),
    (137, "Pennsylvania", "Amendment of Pennsylvania Intimate Images Laws"),
    (138, "Rhode Island", "Deceptive and Fraudulent Synthetic Media in Election Communications"),
    (139, "Rhode Island", "Rhode Island Data Transparency and Privacy Protection Act"),
    (140, "South Carolina", "Real Estate AI Responsibility Law"),
    (141, "South Dakota", "Amendment of South Dakota CSAM Laws"),
    (142, "South Dakota", "An Act to Prohibit the Use of a Deepfake to Influence an Election"),
    (143, "Tennessee", "Ensuring Likeness, Voice, and Image Security (ELVIS) Act of 2024"),
    (144, "Tennessee", "Amendment of Tennessee CSAM Laws"),
    (145, "Tennessee", "Tennessee Information Protection Act"),
    (146, "Texas", "Amendment of Texas CSAM Laws"),
    (147, "Texas", "Unlawful Distribution of Sexually Explicit Videos"),
    (148, "Texas", "Texas Data Privacy and Security Act"),
    (149, "Texas", "Act Relating to the Regulation and Use of AI by Governmental Entities"),
    (150, "Texas", "Amendment to the CSAM Statutes"),
    (151, "Texas", "Visual Material Appearing to Depict a Child"),
    (152, "Texas", "Financial Abuse Using Artificially Generated Media or Phishing"),
    (153, "Texas", "Artificial Intelligence in Electronic Health Record"),
    (154, "Texas", "Use of Automated Decision System for Adverse Determinations"),
    (155, "Texas", "Unlawful Production or Distribution of"),
    (156, "Texas", "Certain Sexually Explicit Material"),
    (157, "Texas", "Unlawful Production or Distribution of Certain Sexually Explicit Material"),
    (158, "Texas", "AI Sexual Material Harmful to Minors"),
    (159, "Texas", "Texas Responsible Artificial Intelligence Governance Act (TRAIGA)"),
    (160, "Utah", "Utah Artificial Intelligence Policy Act"),
    (161, "Utah", "Artificial Pornographic Images Amendments"),
    (162, "Utah", "Sexually Explicit Minor Amendments"),
    (163, "Utah", "Utah Information Technology Act"),
    (164, "Utah", "Artificial Intelligence Consumer Protection Amendments"),
    (165, "Utah", "AI Applications Related to Mental Health"),
    (166, "Utah", "Law Enforcement Usage of Artificial Intelligence"),
    (167, "Utah", "Unauthorized Artificial Intelligence Impersonation Amendments"),
    (168, "Vermont", "An Act Relating to the Use and Oversight of AI in State Government"),
    (169, "Vermont", "Amendment of non-consensual sexual image dissemination statute"),
    (170, "Virginia", "Amendment to the Unlawful Dissemination of Images of Another Statute"),
    (171, "Virginia", "Hospital / Nursing Home Virtual Assistant Law"),
    (172, "Virginia", "Virginia Consumer Data Protection Act"),
    (173, "Virginia", "Amendment to CSAM statute"),
    (174, "Virginia", "Artificial Intelligence-Based Tools"),
    (175, "Washington", "Amendment of Washington CSAM Laws"),
    (176, "Washington", "Amendment of Washington Intimate Image Laws"),
    (177, "West Virginia", "Crimes Against Chastity, Morality and Decency - CSAM"),
    (178, "West Virginia", "Crimes Against Chastity, Morality and Decency - Intimate Images"),
    (179, "Wisconsin", "2023 Wisconsin Act 123"),
    (180, "Wisconsin", "Amendment to the CSAM Statute"),
    (181, "Wisconsin", "Advertising Enhanced by Technology Law"),
    (182, "Wyoming", "Sexual Exploitation of Children"),
    (183, "Wyoming", "Amendment to Intimate Image Law"),
    # Near-duplicates in legacy set
    (184, "California", "Defending Democracy from Deepfake Deception Act of 2024"),
    (185, "New Jersey", "Establishes criminal penalties for production or dissemination of deepfakes."),
    (186, "New York", "NYC AI Employment Law (Local Law 144)"),
]

# Jurisdiction ID -> name mapping (from dim_jurisdictions.csv)
JURISDICTION_MAP = {}


def _tokenize(s: str) -> set[str]:
    """Extract meaningful tokens from a title for comparison."""
    # Remove bill numbers, statute refs, and noise
    s = re.sub(r'\b[A-Z]{1,2}\s*\d+[A-Za-z]*\b', '', s)  # HB 123, SB 456
    s = re.sub(r'§.*', '', s)  # statute references
    s = re.sub(r'\d{4}', '', s)  # years
    s = re.sub(r'[^\w\s]', ' ', s)  # punctuation
    tokens = {t.lower() for t in s.split() if len(t) >= 3}
    # Remove very common words
    tokens -= {"the", "and", "for", "act", "law", "code", "rev", "stat", "ann"}
    return tokens


def _similarity(csv_title: str, legacy_title: str) -> float:
    """Combined token overlap + sequence similarity."""
    csv_tokens = _tokenize(csv_title)
    legacy_tokens = _tokenize(legacy_title)

    if not csv_tokens or not legacy_tokens:
        return 0.0

    # Token Jaccard
    overlap = len(csv_tokens & legacy_tokens)
    union = len(csv_tokens | legacy_tokens)
    jaccard = overlap / union if union else 0.0

    # Sequence similarity on the raw strings (lowercase)
    seq = SequenceMatcher(None, csv_title.lower(), legacy_title.lower()).ratio()

    return 0.4 * jaccard + 0.6 * seq


def load_jurisdiction_map(csv_path: Path) -> dict[str, str]:
    """Load jurisdiction_id -> jurisdiction_name mapping."""
    jmap = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            jmap[row["jurisdiction_id"]] = row["name"]
    return jmap


def main():
    data_dir = Path(__file__).resolve().parents[1] / "data"
    csv_path = data_dir / "fact_laws.csv"
    jurisdictions = load_jurisdiction_map(data_dir / "dim_jurisdictions.csv")

    # Build legacy lookup by jurisdiction
    legacy_by_jurisdiction: dict[str, list[tuple[int, str]]] = {}
    for lid, jname, title in LEGACY_FAMILIES:
        legacy_by_jurisdiction.setdefault(jname, []).append((lid, title))

    # Read CSV
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    matched = 0
    unmatched_orrick = []
    iapp_rows = []
    fixes = []

    for row in rows:
        jid = row.get("jurisdiction_id", "")
        jname = jurisdictions.get(jid, "")
        source = row.get("source_id", "")
        csv_title = row.get("title", "")
        law_id = row.get("law_id", "")

        if source == "2":
            # IAPP row — no legacy match expected
            iapp_rows.append(row)
            continue

        if not jname:
            print(f"  WARNING: law_id={law_id} has unknown jurisdiction_id={jid}")
            continue

        # Find best legacy match within same jurisdiction
        candidates = legacy_by_jurisdiction.get(jname, [])
        if not candidates:
            unmatched_orrick.append((law_id, jname, csv_title))
            continue

        best_score = 0.0
        best_match = None
        for lid, legacy_title in candidates:
            score = _similarity(csv_title, legacy_title)
            if score > best_score:
                best_score = score
                best_match = (lid, legacy_title)

        if best_match and best_score >= 0.25:
            lid, correct_title = best_match
            if csv_title != correct_title:
                fixes.append({
                    "law_id": law_id,
                    "jurisdiction": jname,
                    "old_title": csv_title,
                    "new_title": correct_title,
                    "legacy_id": lid,
                    "score": best_score,
                })
                row["title"] = correct_title
            matched += 1
        else:
            unmatched_orrick.append((law_id, jname, csv_title))

    # Write updated CSV
    output_path = csv_path  # overwrite in place
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Report
    print(f"\n{'='*70}")
    print(f"TITLE FIX REPORT")
    print(f"{'='*70}")
    print(f"Total CSV rows:      {len(rows)}")
    print(f"Orrick rows matched: {matched}")
    print(f"Titles corrected:    {len(fixes)}")
    print(f"IAPP rows (skipped): {len(iapp_rows)}")
    print(f"Unmatched Orrick:    {len(unmatched_orrick)}")

    if fixes:
        print(f"\n--- Title corrections ---")
        for fix in fixes[:30]:
            print(f"  law_id={fix['law_id']:>3} ({fix['jurisdiction']}) score={fix['score']:.2f}")
            print(f"    OLD: {fix['old_title'][:80]}")
            print(f"    NEW: {fix['new_title'][:80]}")

    if unmatched_orrick:
        print(f"\n--- Unmatched Orrick rows (need manual review) ---")
        for law_id, jname, title in unmatched_orrick:
            print(f"  law_id={law_id}: {jname} - {title[:70]}")

    print(f"\n--- IAPP rows (bill numbers only, no title fix needed) ---")
    for row in iapp_rows[:10]:
        print(f"  law_id={row['law_id']}: {row['title']}")
    if len(iapp_rows) > 10:
        print(f"  ... and {len(iapp_rows) - 10} more")

    return fixes, unmatched_orrick, iapp_rows


if __name__ == "__main__":
    main()
