-- Reference rules for the HomeBridge mortgage report.
-- DuckDB table macros. Each macro takes the report time `as_of` (UTC).
-- The DAX measures in reports/HomeBridgeMortgage.SemanticModel mirror these rules.
-- Tests: reports/tests/test_report_logic.py.

-- INV-5, T05: latest score from completed runs only. A failed latest run marks the score stale.
-- Accounts with no score stay in the result with score_status = 'no_score'.
CREATE OR REPLACE MACRO latest_score(as_of, max_score_age_minutes) AS TABLE
WITH completed AS (
    SELECT s.*
    FROM account_scores AS s
    JOIN score_runs AS r USING (run_id)
    WHERE r.status = 'completed' AND r.completed_at <= as_of AND s.scored_at <= as_of
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY account_key ORDER BY scored_at DESC, run_id DESC) AS rn
    FROM completed
),
last_run AS (
    SELECT max_by(status, started_at) AS last_run_status
    FROM score_runs
    WHERE started_at <= as_of
)
SELECT
    a.account_key,
    a.mortgage_id,
    a.cycle_id,
    r.run_id,
    r.snapshot_id,
    r.model_version,
    r.score,
    r.scored_at,
    date_diff('minute', r.scored_at, as_of) AS score_age_minutes,
    lr.last_run_status,
    CASE
        WHEN r.score IS NULL THEN 'no_score'
        WHEN lr.last_run_status = 'failed' THEN 'stale_last_run_failed'
        WHEN date_diff('minute', r.scored_at, as_of) > max_score_age_minutes THEN 'stale_too_old'
        ELSE 'current'
    END AS score_status
FROM mortgage_account AS a
LEFT JOIN ranked AS r ON r.account_key = a.account_key AND r.rn = 1
CROSS JOIN last_run AS lr;

-- T03: a stale or missing payment feed is not zero arrears. arrears_amount stays NULL unless the feed is current.
-- The validation step sets feed_watermark.is_stale. The report also treats an old watermark check as stale,
-- so a stopped validation step cannot make old data look current.
CREATE OR REPLACE MACRO payment_position(as_of) AS TABLE
WITH feed AS (
    SELECT
        max(max_ingested_at) AS max_ingested_at,
        bool_or(is_stale) AS is_stale,
        max(checked_at) AS checked_at,
        max(stale_threshold_hours) AS stale_threshold_hours
    FROM feed_watermark
    WHERE dataset = 'payment'
),
feed_state AS (
    SELECT
        max_ingested_at,
        CASE
            WHEN max_ingested_at IS NULL THEN 'missing'
            WHEN is_stale THEN 'stale'
            WHEN date_diff('hour', checked_at, as_of) > stale_threshold_hours THEN 'stale'
            WHEN date_diff('hour', max_ingested_at, as_of) > stale_threshold_hours THEN 'stale'
            ELSE 'current'
        END AS feed_state
    FROM feed
),
ledger AS (
    SELECT
        mortgage_id,
        sum(amount_due) AS total_due,
        sum(amount_received) AS total_received,
        count(*) AS entries,
        count(*) FILTER (WHERE entry_type = 'reversal') AS reversals
    FROM payment
    WHERE due_at <= as_of AND ingested_at <= as_of
    GROUP BY mortgage_id
)
SELECT
    m.mortgage_id,
    f.feed_state,
    f.max_ingested_at AS feed_max_ingested_at,
    CASE
        WHEN f.feed_state <> 'current' THEN f.feed_state
        WHEN l.entries IS NULL THEN 'no_records'
        ELSE 'current'
    END AS payment_state,
    CASE
        WHEN f.feed_state = 'current' AND l.entries IS NOT NULL
        THEN greatest(l.total_due - l.total_received, 0)
    END AS arrears_amount,
    coalesce(l.reversals, 0) AS reversal_count
FROM mortgage AS m
CROSS JOIN feed_state AS f
LEFT JOIN ledger AS l USING (mortgage_id);

-- T07: one row per source case at as_of. Contacts count contact_id once. State changes are not contacts.
-- A case is resolved only when its latest state at as_of is 'resolved'. 'reopened' means unresolved.
CREATE OR REPLACE MACRO support_history(as_of) AS TABLE
WITH states AS (
    SELECT *, row_number() OVER (PARTITION BY case_id ORDER BY changed_at DESC, recorded_at DESC) AS rn
    FROM servicing_case_event
    WHERE changed_at <= as_of AND recorded_at <= as_of
),
contacts AS (
    SELECT
        case_id,
        count(DISTINCT contact_id) AS contacts,
        min(occurred_at) AS first_contact_at,
        max(occurred_at) AS last_contact_at
    FROM servicing_contact
    WHERE occurred_at <= as_of AND ingested_at <= as_of
    GROUP BY case_id
)
SELECT
    s.case_id,
    s.mortgage_id,
    s.category,
    s.support_route,
    s.status,
    s.status = 'resolved' AS is_resolved,
    coalesce(c.contacts, 0) AS contacts,
    c.first_contact_at,
    c.last_contact_at,
    coalesce(c.contacts, 0) > 1 AND s.status <> 'resolved' AS repeat_contact_unresolved
FROM states AS s
LEFT JOIN contacts AS c USING (case_id)
WHERE s.rn = 1;

CREATE OR REPLACE MACRO support_summary(as_of) AS TABLE
SELECT
    mortgage_id,
    count(*) AS cases,
    count(*) FILTER (WHERE NOT is_resolved) AS unresolved_cases,
    sum(contacts) AS contacts,
    sum(CASE WHEN NOT is_resolved AND contacts > 1 THEN contacts - 1 ELSE 0 END) AS repeat_contacts_on_unresolved,
    max(CASE WHEN NOT is_resolved THEN contacts ELSE 0 END) AS max_contacts_on_one_unresolved_case,
    count(*) FILTER (WHERE contacts = 1) AS single_contact_cases
FROM support_history(as_of)
GROUP BY mortgage_id;

-- Segment history: the segment of each account on any past date.
-- Rows are append-only, so valid_to may stay NULL on earlier rows. Take the latest row that started at or before as_of.
CREATE OR REPLACE MACRO segment_as_of(as_of) AS TABLE
WITH eligible AS (
    SELECT *, row_number() OVER (PARTITION BY account_key ORDER BY valid_from DESC, assigned_at DESC) AS rn
    FROM segment_membership
    WHERE valid_from <= as_of
      AND assigned_at <= as_of
      AND (valid_to IS NULL OR valid_to > as_of)
)
SELECT account_key, mortgage_id, cycle_id, segment_id, run_id, cluster_model_version, valid_from, valid_to
FROM eligible
WHERE rn = 1;

-- Data check: two segment rows for one account must never start at the same time.
CREATE OR REPLACE MACRO segment_conflicts() AS TABLE
SELECT a.account_key, a.run_id AS run_a, b.run_id AS run_b
FROM segment_membership AS a
JOIN segment_membership AS b
  ON a.account_key = b.account_key AND a.run_id < b.run_id AND a.valid_from = b.valid_from;

-- Product-transfer request status, days at the current stage and renewal-portal software faults.
-- Rejection and abandonment are not portal faults.
CREATE OR REPLACE MACRO request_status(as_of) AS TABLE
WITH ev AS (
    SELECT * FROM transfer_request_event WHERE occurred_at <= as_of AND ingested_at <= as_of
),
latest AS (
    SELECT *, row_number() OVER (PARTITION BY application_id ORDER BY occurred_at DESC, event_id DESC) AS rn
    FROM ev
),
cur AS (
    SELECT application_id, mortgage_id, stage, status, occurred_at AS last_event_at FROM latest WHERE rn = 1
),
left_other_stage AS (
    SELECT e.application_id, max(e.occurred_at) AS last_other_stage_at
    FROM ev AS e JOIN cur AS c USING (application_id)
    WHERE e.stage <> c.stage
    GROUP BY e.application_id
),
entered AS (
    SELECT e.application_id, min(e.occurred_at) AS stage_entered_at
    FROM ev AS e
    JOIN cur AS c USING (application_id)
    LEFT JOIN left_other_stage AS o USING (application_id)
    WHERE e.stage = c.stage AND (o.last_other_stage_at IS NULL OR e.occurred_at > o.last_other_stage_at)
    GROUP BY e.application_id
),
errors AS (
    SELECT
        application_id,
        count(*) FILTER (WHERE error_class = 'software_fault') AS portal_errors,
        count(*) FILTER (WHERE error_class = 'software_fault' AND fault_verified) AS verified_portal_faults
    FROM ev
    GROUP BY application_id
)
SELECT
    c.application_id,
    c.mortgage_id,
    c.stage AS current_stage,
    c.status AS current_status,
    n.stage_entered_at,
    date_diff('day', n.stage_entered_at, as_of) AS days_at_stage,
    coalesce(x.portal_errors, 0) AS portal_errors,
    coalesce(x.verified_portal_faults, 0) AS verified_portal_faults,
    c.last_event_at
FROM cur AS c
JOIN entered AS n USING (application_id)
LEFT JOIN errors AS x USING (application_id);

-- The account view: one row per mortgage and deal cycle.
CREATE OR REPLACE MACRO account_view(as_of, max_score_age_minutes) AS TABLE
WITH req AS (
    SELECT *, row_number() OVER (PARTITION BY mortgage_id ORDER BY last_event_at DESC) AS rn
    FROM request_status(as_of)
),
support_case AS (
    SELECT account_key, assigned_to
    FROM review_cases
    WHERE action_family = 'support' AND status IN ('open', 'on_hold') AND case_created_at <= as_of
),
renewal_case AS (
    SELECT account_key, assigned_to, status
    FROM review_cases
    WHERE action_family = 'renewal' AND case_created_at <= as_of
)
SELECT
    a.account_key,
    a.mortgage_id,
    a.cycle_id,
    a.deal_end_at,
    a.deal_end_at - INTERVAL 90 DAY AS checkpoint_at,
    date_diff('day', as_of, a.deal_end_at) AS days_to_deal_end,
    a.balance,
    a.current_payment,
    a.product_type,
    s.score,
    s.scored_at,
    s.score_age_minutes,
    s.score_status,
    s.model_version,
    s.run_id,
    g.segment_id,
    r.application_id,
    r.current_stage,
    r.days_at_stage,
    r.portal_errors,
    r.verified_portal_faults,
    p.payment_state,
    p.arrears_amount,
    sc.account_key IS NOT NULL AS payment_support_priority,
    coalesce(rc.assigned_to, a.owner_id) AS review_owner,
    rc.status AS renewal_review_status
FROM mortgage_account AS a
LEFT JOIN latest_score(as_of, max_score_age_minutes) AS s ON s.account_key = a.account_key
LEFT JOIN segment_as_of(as_of) AS g ON g.account_key = a.account_key
LEFT JOIN req AS r ON r.mortgage_id = a.mortgage_id AND r.rn = 1
LEFT JOIN payment_position(as_of) AS p ON p.mortgage_id = a.mortgage_id
LEFT JOIN support_case AS sc ON sc.account_key = a.account_key
LEFT JOIN renewal_case AS rc ON rc.account_key = a.account_key;
