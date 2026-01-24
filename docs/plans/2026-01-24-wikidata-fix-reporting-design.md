# Wikidata Fix Reporting Design

**Status:** Approved, not yet implemented
**Issue:** career-images-eih
**Date:** 2026-01-24

## Problem

When reviewers mark items as "not a career", it's often due to Wikidata data quality issues:
- Wrong Q-ID being used as P106 (e.g., Q43412 'Apostles in the New Testament' instead of Q15699988 'apostle' as a title)
- Items that aren't really occupations being used as P106 values
- Disambiguation issues

We want to capture this information and generate reports to fix Wikidata upstream.

## Solution Overview

1. **UI**: When marking "not a career", collect why (category + details)
2. **Script**: Use Claude API to propose specific Wikidata fixes
3. **Report**: Generate formatted reports for Wikidata editors

Key principle: Don't burden reviewers with finding Q-IDs. They describe the problem; Claude proposes the technical fix.

## Design

### Database Changes

New table to track "not a career" reports:

```sql
CREATE TABLE not_a_career_reports (
    id INTEGER PRIMARY KEY,
    wikidata_id TEXT NOT NULL,
    reason_category TEXT NOT NULL
        CHECK(reason_category IN ('wrong_qid', 'not_occupation', 'disambiguation', 'other')),
    reason_details TEXT,           -- Free text explanation from reviewer
    proposed_fix TEXT,             -- Claude-generated fix (filled later)
    fix_status TEXT DEFAULT 'pending'
        CHECK(fix_status IN ('pending', 'proposed', 'reported', 'fixed', 'wont_fix')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT,
    FOREIGN KEY (wikidata_id) REFERENCES careers(wikidata_id)
);
```

Status lifecycle: `pending` → `proposed` → `reported` → `fixed`

### UI Changes

When "Not A Career" is selected, show additional fields:

```
Status: ○ Unreviewed ○ Needs Diverse ○ Has Diverse ● Not A Career ○ Gender-Specific

▼ Why is this not a career? (shown only when "Not A Career" selected)

  ○ Wrong Q-ID - This is the wrong Wikidata item
  ○ Not an occupation - Shouldn't be used as P106
  ○ Disambiguation - Too vague or disambiguation page
  ○ Other

  Details (optional): [____________________]
  "Help us fix Wikidata - describe what's wrong"
```

- Category is required when selecting "Not A Career"
- Details are optional but encouraged
- Applies to both career_detail.html and quick_review.html
- JavaScript shows/hides fields based on status selection

### Claude API Script (`propose_fixes.py`)

Process pending reports and propose fixes:

```bash
uv run python propose_fixes.py          # Process all pending
uv run python propose_fixes.py --dry-run # Preview without saving
```

For each pending report:
1. Fetch the career name, current Q-ID, reason category, and details
2. Fetch Wikidata item description via Wikidata API
3. Call Claude API with this context
4. Claude proposes a fix:
   - "Remove P106 usage - this is not an occupation"
   - "Replace Q43412 with Q15699988 (apostle as religious title)"
   - "Merge into Q123456 (disambiguation resolution)"
5. Store proposed fix, update status to 'proposed'

Requires: `ANTHROPIC_API_KEY` environment variable.

### Report Generation (`generate_wikidata_report.py`)

Generate reports for Wikidata editors:

```bash
uv run python generate_wikidata_report.py --format=csv > wikidata_fixes.csv
uv run python generate_wikidata_report.py --format=wiki  # Wiki markup for talk pages
```

Report includes:
- Item name and Q-ID (with links)
- Problem category
- Reviewer details
- Proposed fix

After reporting upstream, manually update status to 'reported', then 'fixed' when confirmed.

## Implementation Order

1. Database schema (add table, update db.py)
2. UI changes (career_detail.html, quick_review.html, JavaScript)
3. Backend endpoint updates (app.py)
4. propose_fixes.py script
5. generate_wikidata_report.py script
6. Add ANTHROPIC_API_KEY to fly.io secrets (if running propose_fixes there)

## Future Improvements

- Auto-lookup candidate Q-IDs from Wikidata search
- Track which items have been reported to which Wikidata talk pages
- Integration with Wikidata's batch editing tools
- Dashboard showing fix pipeline status
