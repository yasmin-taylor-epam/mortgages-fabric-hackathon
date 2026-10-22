-- Expected Gold table shapes that the report reads (consumer view).
-- The producing work packages own the real schemas:
--   Data and replay: data/contracts/*.schema.json
--   Models and evaluation: score_runs, account_scores, feature_attributions, segment_membership
-- See action/contracts/consumer-contract.md. DuckDB dialect. Times are UTC, stored without a time zone.

CREATE TABLE IF NOT EXISTS mortgage (
    mortgage_id   VARCHAR PRIMARY KEY,
    product_type  VARCHAR,
    cycle_id      VARCHAR                 -- current deal cycle
);

CREATE TABLE IF NOT EXISTS mortgage_account (
    account_key      VARCHAR PRIMARY KEY, -- mortgage_id|cycle_id
    mortgage_id      VARCHAR NOT NULL,
    cycle_id         VARCHAR NOT NULL,
    deal_end_at      TIMESTAMP NOT NULL,
    balance          DECIMAL(14, 2),
    current_payment  DECIMAL(12, 2),
    product_type     VARCHAR,
    owner_id         VARCHAR
);

CREATE TABLE IF NOT EXISTS payment (
    entry_id         VARCHAR PRIMARY KEY,
    entry_type       VARCHAR NOT NULL,    -- reversal rows: entry_type = reversal, amount_due = 0
    mortgage_id      VARCHAR NOT NULL,
    due_at           TIMESTAMP NOT NULL,
    posted_at        TIMESTAMP NOT NULL,
    ingested_at      TIMESTAMP NOT NULL,
    amount_due       DECIMAL(12, 2) NOT NULL,
    amount_received  DECIMAL(12, 2) NOT NULL,
    reversal_of      VARCHAR
);

CREATE TABLE IF NOT EXISTS feed_watermark (
    dataset                VARCHAR PRIMARY KEY,
    checked_at             TIMESTAMP NOT NULL,
    max_ingested_at        TIMESTAMP,
    max_event_at           TIMESTAMP,
    row_count              BIGINT,
    stale_threshold_hours  INTEGER NOT NULL,
    is_stale               BOOLEAN NOT NULL  -- computed by the validation step
);

CREATE TABLE IF NOT EXISTS transfer_request_event (
    event_id        VARCHAR PRIMARY KEY,
    application_id  VARCHAR NOT NULL,
    mortgage_id     VARCHAR NOT NULL,
    occurred_at     TIMESTAMP NOT NULL,
    ingested_at     TIMESTAMP NOT NULL,
    stage           VARCHAR NOT NULL,
    status          VARCHAR NOT NULL,
    error_code      VARCHAR,
    error_class     VARCHAR,             -- software_fault marks a renewal-portal software error
    fault_verified  BOOLEAN
);

CREATE TABLE IF NOT EXISTS servicing_case (
    case_id        VARCHAR PRIMARY KEY,
    mortgage_id    VARCHAR NOT NULL,
    category       VARCHAR,
    support_route  VARCHAR,
    opened_at      TIMESTAMP,
    latest_status  VARCHAR,
    resolved_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS servicing_case_event (
    case_id        VARCHAR NOT NULL,
    mortgage_id    VARCHAR NOT NULL,
    changed_at     TIMESTAMP NOT NULL,
    recorded_at    TIMESTAMP NOT NULL,
    category       VARCHAR,
    status         VARCHAR NOT NULL,     -- opened, in_progress, resolved, reopened
    support_route  VARCHAR
);

CREATE TABLE IF NOT EXISTS servicing_contact (
    contact_id   VARCHAR PRIMARY KEY,
    case_id      VARCHAR NOT NULL,
    mortgage_id  VARCHAR NOT NULL,
    occurred_at  TIMESTAMP NOT NULL,
    ingested_at  TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS score_runs (
    run_id        VARCHAR PRIMARY KEY,
    run_kind      VARCHAR NOT NULL,
    status        VARCHAR NOT NULL,      -- running, completed, failed
    started_at    TIMESTAMP NOT NULL,
    completed_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS account_scores (
    account_key    VARCHAR NOT NULL,
    mortgage_id    VARCHAR NOT NULL,
    cycle_id       VARCHAR NOT NULL,
    run_id         VARCHAR NOT NULL,
    snapshot_id    VARCHAR NOT NULL,
    model_version  VARCHAR NOT NULL,
    score          DOUBLE NOT NULL,
    scored_at      TIMESTAMP NOT NULL,
    cutoff_at      TIMESTAMP NOT NULL,
    PRIMARY KEY (account_key, run_id)
);

CREATE TABLE IF NOT EXISTS feature_attributions (
    account_key   VARCHAR NOT NULL,
    run_id        VARCHAR NOT NULL,
    feature_name  VARCHAR NOT NULL,
    contribution  DOUBLE NOT NULL
);

CREATE TABLE IF NOT EXISTS segment_membership (
    account_key            VARCHAR NOT NULL,
    mortgage_id            VARCHAR NOT NULL,
    cycle_id               VARCHAR NOT NULL,
    run_id                 VARCHAR NOT NULL,
    segment_id             VARCHAR NOT NULL,
    cluster_model_version  VARCHAR NOT NULL,
    assigned_at            TIMESTAMP NOT NULL,
    valid_from             TIMESTAMP NOT NULL,
    valid_to               TIMESTAMP,
    PRIMARY KEY (account_key, run_id)
);

CREATE TABLE IF NOT EXISTS review_cases (
    case_key        VARCHAR PRIMARY KEY,
    account_key     VARCHAR NOT NULL,
    mortgage_id     VARCHAR NOT NULL,
    cycle_id        VARCHAR NOT NULL,
    action_family   VARCHAR NOT NULL,
    status          VARCHAR NOT NULL,
    assigned_to     VARCHAR NOT NULL,
    case_created_at TIMESTAMP NOT NULL
);
