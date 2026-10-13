-- Load-test data: clones the demo tenant's rows into :n synthetic leads with a realistic state mix.
-- TEST DATABASES ONLY. Run as a superuser; triggers are bypassed because these are history rows, not real sends.
SET session_replication_role = replica;
\set n 100000
CREATE TEMP TABLE tpl AS
SELECT l.tenant_id, l.campaign_id, c.id AS company_id, p.id AS prospect_id, l.signal_id, p.email_enc, p.name_enc,
       (SELECT id FROM inboxes WHERE tenant_id = l.tenant_id LIMIT 1) AS inbox_id
FROM leads l JOIN companies c ON c.id = l.company_id JOIN prospects p ON p.id = l.prospect_id
WHERE c.name LIKE 'Contoso%' LIMIT 1;

CREATE TEMP TABLE gen AS
SELECT g AS i, gen_random_uuid() AS company_id, gen_random_uuid() AS prospect_id, gen_random_uuid() AS lead_id,
       (ARRAY['contacted','contacted','contacted','replied','objected','disqualified','rejected_by_compliance',
              'alternative_channel','suppressed','pending_approval','qualified','booked','attended'])[1 + g % 13] AS state,
       now() - (g % 90) * interval '1 day' AS at
FROM generate_series(1, :n) g;

INSERT INTO companies (id, tenant_id, name, domain, domain_normalised, country_code, legal_form, created_at)
SELECT gen.company_id, tpl.tenant_id, 'Load Test Co ' || gen.i || ' Ltd', 'load' || gen.i || '.example',
       'load' || gen.i || '.example', 'GB', 'ltd', gen.at FROM gen, tpl;
INSERT INTO prospects (id, tenant_id, company_id, name_enc, title, email_enc, email_bidx, country_code, subscriber_type,
                       email_verification, role_relevance, created_at)
SELECT gen.prospect_id, tpl.tenant_id, gen.company_id, tpl.name_enc, 'Head of IT', tpl.email_enc,
       decode(md5(gen.i::text) || md5('b' || gen.i), 'hex'), 'GB', 'corporate', 'valid', 0.9, gen.at FROM gen, tpl;
INSERT INTO leads (id, tenant_id, campaign_id, company_id, prospect_id, signal_id, state, created_at, state_changed_at)
SELECT gen.lead_id, tpl.tenant_id, tpl.campaign_id, gen.company_id, gen.prospect_id, tpl.signal_id, gen.state, gen.at, gen.at
FROM gen, tpl;
INSERT INTO compliance_decisions (tenant_id, lead_id, stage, outcome, reason_codes, rules_applied, effective_country_code,
                                  suppression_checked_at, engine_version, created_at)
SELECT tpl.tenant_id, gen.lead_id, 'queue',
       CASE WHEN gen.state IN ('rejected_by_compliance','suppressed') THEN 'block'
            WHEN gen.state = 'alternative_channel' THEN 'alternative_channel' ELSE 'allow' END,
       CASE WHEN gen.state = 'suppressed' THEN ARRAY['SUPPRESSED_GLOBAL'] WHEN gen.state = 'rejected_by_compliance'
            THEN ARRAY['EMAIL_UNVERIFIED'] WHEN gen.state = 'alternative_channel' THEN ARRAY['INDIVIDUAL_SUBSCRIBER']
            ELSE ARRAY['OK'] END,
       '{"GB": "load@v1"}', 'GB', gen.at, 'load', gen.at FROM gen, tpl;
INSERT INTO messages (tenant_id, lead_id, campaign_id, inbox_id, sequence_step, recipient_hash, recipient_domain_hash,
                      status, idempotency_key, sent_at, mode_at_send, created_at, kind, subject_enc, body_enc)
SELECT tpl.tenant_id, gen.lead_id, tpl.campaign_id, tpl.inbox_id, 0, '\x00', '\x00',
       CASE WHEN gen.state = 'pending_approval' THEN 'pending_approval' ELSE 'dry_run_sent' END,
       'load:' || gen.i, CASE WHEN gen.state = 'pending_approval' THEN NULL ELSE gen.at END,
       CASE WHEN gen.state = 'pending_approval' THEN NULL ELSE 'dry_run' END, gen.at, 'outreach',
       (SELECT subject_enc FROM messages WHERE lead_id = (SELECT lead_id FROM messages LIMIT 1) LIMIT 1),
       (SELECT body_enc FROM messages WHERE lead_id = (SELECT lead_id FROM messages LIMIT 1) LIMIT 1)
FROM gen, tpl WHERE gen.state NOT IN ('rejected_by_compliance','alternative_channel','suppressed');
INSERT INTO replies (tenant_id, lead_id, received_at, classification, classified_at, handled_at)
SELECT tpl.tenant_id, gen.lead_id, gen.at + interval '1 day',
       CASE WHEN gen.state = 'objected' THEN 'unsubscribe' WHEN gen.state = 'disqualified' THEN 'negative'
            ELSE 'interested' END, gen.at + interval '1 day', gen.at + interval '1 day'
FROM gen, tpl WHERE gen.state IN ('replied','objected','disqualified','qualified','booked','attended');
INSERT INTO meetings (tenant_id, lead_id, calendar_provider, starts_at, ends_at, prospect_tz, host_tz, status,
                      booked_by_system, idempotency_key, created_at)
SELECT tpl.tenant_id, gen.lead_id, 'mock', gen.at + interval '3 days', gen.at + interval '3 days 30 minutes',
       'Europe/London', 'Europe/London', CASE WHEN gen.state = 'attended' THEN 'attended' ELSE 'booked' END, true,
       'load-mt:' || gen.i, gen.at + interval '2 days'
FROM gen, tpl WHERE gen.state IN ('booked','attended');
SET session_replication_role = origin;
ANALYZE;
SELECT state, count(*) FROM leads GROUP BY state ORDER BY 2 DESC;
