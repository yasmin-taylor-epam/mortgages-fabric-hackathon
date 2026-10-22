-- Hand-written synthetic Gold rows for the report logic tests. Fictional HomeBridge accounts.
-- Report time in the tests: 2026-11-03 10:00 UTC.

INSERT INTO mortgage VALUES
    ('MTG-ALEX', 'fixed_5y', 'CYC-2027-01'),
    ('MTG-SAM',  'fixed_2y', 'CYC-2027-01'),
    ('MTG-JO',   'fixed_2y', 'CYC-2027-02');

INSERT INTO mortgage_account VALUES
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', TIMESTAMP '2027-01-31 00:00:00', 182000.00, 1105.40, 'fixed_5y', 'ADV-011'),
    ('MTG-SAM|CYC-2027-01',  'MTG-SAM',  'CYC-2027-01', TIMESTAMP '2027-01-15 00:00:00',  96000.00,  712.10, 'fixed_2y', 'ADV-011'),
    ('MTG-JO|CYC-2027-02',   'MTG-JO',   'CYC-2027-02', TIMESTAMP '2027-02-28 00:00:00', 143500.00,  950.00, 'fixed_2y', 'ADV-017');

-- RUN-0003 failed after writing a partial row. Reports must ignore it.
INSERT INTO score_runs VALUES
    ('RUN-0001', 'scheduled', 'completed', TIMESTAMP '2026-11-03 08:45:00', TIMESTAMP '2026-11-03 09:00:00'),
    ('RUN-0002', 'scheduled', 'completed', TIMESTAMP '2026-11-03 09:15:00', TIMESTAMP '2026-11-03 09:30:00'),
    ('RUN-0003', 'scheduled', 'failed',    TIMESTAMP '2026-11-03 09:45:00', NULL);

INSERT INTO account_scores VALUES
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0001', 'SNP-A1', 'clf-0.1', 0.70, TIMESTAMP '2026-11-03 09:00:00', TIMESTAMP '2026-11-03 08:45:00'),
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0002', 'SNP-A2', 'clf-0.1', 0.81, TIMESTAMP '2026-11-03 09:30:00', TIMESTAMP '2026-11-03 09:15:00'),
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0003', 'SNP-A3', 'clf-0.1', 0.95, TIMESTAMP '2026-11-03 09:50:00', TIMESTAMP '2026-11-03 09:45:00'),
    ('MTG-SAM|CYC-2027-01',  'MTG-SAM',  'CYC-2027-01', 'RUN-0002', 'SNP-S2', 'clf-0.1', 0.12, TIMESTAMP '2026-11-03 09:30:00', TIMESTAMP '2026-11-03 09:15:00');

INSERT INTO segment_membership VALUES
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0001', 'SEG-1', 'clu-0.1', TIMESTAMP '2026-11-03 09:00:00', TIMESTAMP '2026-11-03 09:00:00', TIMESTAMP '2026-11-03 09:30:00'),
    ('MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'RUN-0002', 'SEG-2', 'clu-0.1', TIMESTAMP '2026-11-03 09:30:00', TIMESTAMP '2026-11-03 09:30:00', NULL),
    ('MTG-SAM|CYC-2027-01',  'MTG-SAM',  'CYC-2027-01', 'RUN-0002', 'SEG-3', 'clu-0.1', TIMESTAMP '2026-11-03 09:30:00', TIMESTAMP '2026-11-03 09:30:00', NULL);

INSERT INTO feed_watermark VALUES
    ('payment', TIMESTAMP '2026-11-03 09:55:00', TIMESTAMP '2026-11-03 09:00:00', TIMESTAMP '2026-11-03 08:58:00', 5, 6, FALSE);

-- Sam: one reversed collection (T04). Jo: no payment rows.
INSERT INTO payment VALUES
    ('PAY-A1', 'scheduled_collection', 'MTG-ALEX', TIMESTAMP '2026-10-01 00:00:00', TIMESTAMP '2026-10-01 06:00:00', TIMESTAMP '2026-10-01 07:00:00', 1105.40, 1105.40, NULL),
    ('PAY-A2', 'scheduled_collection', 'MTG-ALEX', TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-01 06:00:00', TIMESTAMP '2026-11-01 07:00:00', 1105.40,  500.00, NULL),
    ('PAY-S1', 'scheduled_collection', 'MTG-SAM',  TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-01 06:00:00', TIMESTAMP '2026-11-01 07:00:00',  712.10,  712.10, NULL),
    ('PAY-S2', 'reversal',   'MTG-SAM',  TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-03 06:00:00', TIMESTAMP '2026-11-03 07:00:00',    0.00, -712.10, 'PAY-S1'),
    ('PAY-S3', 'scheduled_collection', 'MTG-SAM',  TIMESTAMP '2026-11-01 00:00:00', TIMESTAMP '2026-11-03 06:00:00', TIMESTAMP '2026-11-03 11:00:00',    0.00,  712.10, NULL);

-- Alex: request stalls at documents_outstanding with two portal software faults.
INSERT INTO transfer_request_event VALUES
    ('TR-1', 'APP-ALEX-1', 'MTG-ALEX', TIMESTAMP '2026-10-20 10:00:00', TIMESTAMP '2026-10-20 10:01:00', 'started',               'in_progress',     NULL,       NULL,             NULL),
    ('TR-2', 'APP-ALEX-1', 'MTG-ALEX', TIMESTAMP '2026-10-22 10:00:00', TIMESTAMP '2026-10-22 10:01:00', 'documents_outstanding', 'in_progress',      NULL,       NULL,             NULL),
    ('TR-3', 'APP-ALEX-1', 'MTG-ALEX', TIMESTAMP '2026-10-25 10:00:00', TIMESTAMP '2026-10-25 10:01:00', 'documents_outstanding', 'error',        'SYS-503',  'software_fault', TRUE),
    ('TR-4', 'APP-ALEX-1', 'MTG-ALEX', TIMESTAMP '2026-10-26 10:00:00', TIMESTAMP '2026-10-26 10:01:00', 'documents_outstanding', 'error',        'SYS-503',  'software_fault', FALSE),
    ('TR-5', 'APP-SAM-1',  'MTG-SAM',  TIMESTAMP '2026-10-10 10:00:00', TIMESTAMP '2026-10-10 10:01:00', 'started',               'in_progress',     NULL,       NULL,             NULL),
    ('TR-6', 'APP-SAM-1',  'MTG-SAM',  TIMESTAMP '2026-10-18 10:00:00', TIMESTAMP '2026-10-18 10:01:00', 'completed',             'completed',    NULL,       NULL,             NULL),
    ('TR-7', 'APP-JO-1',   'MTG-JO',   TIMESTAMP '2026-10-28 10:00:00', TIMESTAMP '2026-10-28 10:01:00', 'offer_in_review',       'rejected',     'ELG-DOC',  'eligibility',      NULL);

-- T07: Alex has one unresolved complaint with three contacts and three separate resolved enquiries.
-- C-A4 resolution is recorded after the report time, so it is unresolved at 10:00.
-- Sam has a payment-support case. Only authorised advisers see its routing.
INSERT INTO servicing_case VALUES
    ('C-A1', 'MTG-ALEX', 'complaint',        'standard',        TIMESTAMP '2026-10-20 09:00:00', 'in_progress', NULL),
    ('C-A2', 'MTG-ALEX', 'general_enquiry',  'standard',        TIMESTAMP '2026-10-05 09:00:00', 'resolved',    TIMESTAMP '2026-10-05 12:00:00'),
    ('C-A3', 'MTG-ALEX', 'billing_enquiry',  'standard',        TIMESTAMP '2026-10-08 09:00:00', 'resolved',    TIMESTAMP '2026-10-08 12:00:00'),
    ('C-A4', 'MTG-ALEX', 'general_enquiry',  'standard',        TIMESTAMP '2026-10-12 09:00:00', 'resolved',    TIMESTAMP '2026-11-03 09:00:00'),
    ('C-S1', 'MTG-SAM',  'payment_support',  'payment_support', TIMESTAMP '2026-11-02 09:00:00', 'opened',      NULL);

INSERT INTO servicing_case_event VALUES
    ('C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-20 09:00:00', TIMESTAMP '2026-10-20 09:01:00', 'complaint',       'opened',      'standard'),
    ('C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-21 09:00:00', TIMESTAMP '2026-10-21 09:01:00', 'complaint',       'in_progress', 'standard'),
    ('C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-27 09:00:00', TIMESTAMP '2026-10-27 09:01:00', 'complaint',       'resolved',    'standard'),
    ('C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-29 09:00:00', TIMESTAMP '2026-10-29 09:01:00', 'complaint',       'reopened',    'standard'),
    ('C-A2', 'MTG-ALEX', TIMESTAMP '2026-10-05 09:00:00', TIMESTAMP '2026-10-05 09:01:00', 'general_enquiry', 'opened',      'standard'),
    ('C-A2', 'MTG-ALEX', TIMESTAMP '2026-10-05 12:00:00', TIMESTAMP '2026-10-05 12:01:00', 'general_enquiry', 'resolved',    'standard'),
    ('C-A3', 'MTG-ALEX', TIMESTAMP '2026-10-08 09:00:00', TIMESTAMP '2026-10-08 09:01:00', 'billing_enquiry', 'opened',      'standard'),
    ('C-A3', 'MTG-ALEX', TIMESTAMP '2026-10-08 12:00:00', TIMESTAMP '2026-10-08 12:01:00', 'billing_enquiry', 'resolved',    'standard'),
    ('C-A4', 'MTG-ALEX', TIMESTAMP '2026-10-12 09:00:00', TIMESTAMP '2026-10-12 09:01:00', 'general_enquiry', 'opened',      'standard'),
    ('C-A4', 'MTG-ALEX', TIMESTAMP '2026-11-03 09:00:00', TIMESTAMP '2026-11-03 11:00:00', 'general_enquiry', 'resolved',    'standard'),
    ('C-S1', 'MTG-SAM',  TIMESTAMP '2026-11-02 09:00:00', TIMESTAMP '2026-11-02 09:01:00', 'payment_support', 'opened',      'payment_support');

INSERT INTO servicing_contact VALUES
    ('K-1', 'C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-20 09:00:00', TIMESTAMP '2026-10-20 09:05:00'),
    ('K-2', 'C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-24 09:00:00', TIMESTAMP '2026-10-24 09:05:00'),
    ('K-3', 'C-A1', 'MTG-ALEX', TIMESTAMP '2026-10-30 09:00:00', TIMESTAMP '2026-10-30 09:05:00'),
    ('K-4', 'C-A2', 'MTG-ALEX', TIMESTAMP '2026-10-05 09:00:00', TIMESTAMP '2026-10-05 09:05:00'),
    ('K-5', 'C-A3', 'MTG-ALEX', TIMESTAMP '2026-10-08 09:00:00', TIMESTAMP '2026-10-08 09:05:00'),
    ('K-6', 'C-A4', 'MTG-ALEX', TIMESTAMP '2026-10-12 09:00:00', TIMESTAMP '2026-10-12 09:05:00'),
    ('K-7', 'C-S1', 'MTG-SAM',  TIMESTAMP '2026-11-02 09:00:00', TIMESTAMP '2026-11-02 09:05:00');

INSERT INTO review_cases VALUES
    ('MTG-ALEX|CYC-2027-01|support', 'MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'support', 'open',    'payment-support-team', TIMESTAMP '2026-11-03 09:35:00'),
    ('MTG-ALEX|CYC-2027-01|renewal', 'MTG-ALEX|CYC-2027-01', 'MTG-ALEX', 'CYC-2027-01', 'renewal', 'on_hold', 'ADV-011',              TIMESTAMP '2026-11-03 09:32:00');
