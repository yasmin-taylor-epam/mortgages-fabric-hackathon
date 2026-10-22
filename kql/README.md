# KQL

Eventhouse table definitions, ingestion mappings and journey queries.

**Owner:** Data and replay work package.

## Proposed contents

- Table definitions for renewal-portal events, account context copies and score summaries.
- Ingestion mappings for the Eventstream replay.
- Three journey queries from the blueprint (`site/index.html`, section 03, step 6):
  - each product-transfer request's stages and portal errors in time order
  - days that a request remains at one stage
  - retries after a renewal-portal software error.

## Rules

- Each event keeps its original ID and timestamps.
- Journey results that feed model inputs use only events at or before the cutoff (INV-2).
- An unknown account or a conflicting borrower mapping goes to quarantine (T02).
