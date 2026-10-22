# Reports

The Power BI mortgage report and its semantic model.

**Owner:** Dashboard and action work package.

## Proposed contents

The blueprint (`site/index.html`, sections 02 and 06) defines the account view.

- The semantic model. It reads completed Gold outputs in Direct Lake mode.
- The mortgage report. It shows fixed-rate deal end, current terms, request status, portal faults, segment, score age and source evidence.
- Support history: enquiry type, contacts per case and whether each problem is resolved or unresolved.
- Segment history for any past date.

## Rules

- Show the score age. Mark a score as stale when the last scoring run failed (INV-5, T05).
- A missing payment feed shows as stale, not as zero arrears (T03).
- Show only routing information about support cases to authorised advisers.
- Apply a Purview sensitivity label to the semantic model and the report.
- The 60-second view target is not a Direct Lake guarantee. Measure it.
