"""Phase 5 billing on real PostgreSQL: drafts with an evidence chain, the issue guard, gapless numbering, immutability
of issued invoices, disputes and credits, sign-offs, onboarding and reporting. Synthetic data only."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import DbContext, transaction
from app.demo.conversations import run_conversations
from app.demo.dataset import DEMO_CONFIG
from app.demo.seed import providers, seed_demo
from app.domain.billing import service as billing
from app.domain.reporting.metrics import metrics
from app.domain.tenants import onboarding, signoffs
from app.domain.tenants.config import save_config
from app.workflows.pipeline import agent_context, run_campaign
from tests.factories import make_tenant_user

pytestmark = pytest.mark.db
OCT = date(2026, 10, 1)
TODAY = date(2026, 11, 2)


@pytest.fixture
def world(clean_db, settings, cipher):  # type: ignore[no-untyped-def]
    demo = seed_demo(clean_db, settings)  # also creates the demo client admin, who signs criteria + pricing
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run_campaign(ctx, providers())
    run_conversations(ctx, providers(), demo.operator_id)  # Contoso: qualified -> booked -> attended
    return demo


def op(world, engine):  # type: ignore[no-untyped-def]
    return transaction(DbContext(role="operator", user_id=world.operator_id), engine)


def q(engine, sql: str, **p):  # type: ignore[no-untyped-def]
    with transaction(DbContext(role="operator"), engine) as conn:
        return conn.execute(text(sql), p).all()


def draft(world, engine, period=OCT):  # type: ignore[no-untyped-def]
    with op(world, engine) as conn:
        return billing.refresh_draft(conn, tenant_id=world.tenant_id, period_start=period, today=TODAY)


def make_issuable(world, engine) -> None:  # type: ignore[no-untyped-def]
    """Test database only: what the OWNER does before go-live (sign-offs, accountant review, real supplier data)."""
    with op(world, engine) as conn:
        for item in conn.execute(text("SELECT id FROM golive_checklist_items WHERE signed_off_at IS NULL")).scalars():
            conn.execute(text("SELECT golive_sign_off(:i, 'https://evidence.example/test')"), {"i": item})
        billing.record_accountant_verification(
            conn,
            code="uk_not_vat_registered",
            operator_id=world.operator_id,
            accountant="Test Accountant, Example LLP",
            reviewed_on=TODAY,
        )
        conn.execute(
            text("""INSERT INTO supplier_profiles (version, content_hash, legal_name, company_number,
          registered_address, country_code, billing_email, invoice_prefix, payment_details, placeholder)
          SELECT max(version) + 1, 'test', 'Example Supplier Ltd', '12345678', '1 Test Street, Leeds', 'GB',
          'billing@supplier.example', 'INV', 'Sort code 00-00-00, account 00000000', false FROM supplier_profiles""")
        )


def contoso_meeting(engine):  # type: ignore[no-untyped-def]
    return q(
        engine,
        """SELECT m.id, m.lead_id FROM meetings m JOIN leads l ON l.id = m.lead_id
      JOIN companies c ON c.id = l.company_id WHERE c.name LIKE 'Contoso%'""",
    )[0]


def client_ctx(world, engine):  # type: ignore[no-untyped-def]
    uid = q(engine, "SELECT id FROM users WHERE role = 'tenant_admin' AND tenant_id = :t", t=world.tenant_id)[0].id
    return uid, transaction(DbContext(tenant_id=world.tenant_id, role="tenant_admin", user_id=uid), engine)


# --------------------------------------------------------------------------------------------- drafts
def test_draft_bills_setup_retainer_and_the_attended_meeting_with_evidence(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    res = draft(world, clean_db)
    assert [(x["kind"], x["amount_minor"]) for x in res.lines] == [
        ("setup", 25_000),
        ("retainer", 50_000),
        ("meeting", 30_000),
    ]
    assert res.excluded == [] and res.total_minor == 105_000  # not VAT-registered: 0% (rule unverified)
    assert res.blocked_reasons == [
        "GO_LIVE_NOT_SIGNED_OFF",
        "SUPPLIER_DETAILS_INCOMPLETE",
        "TAX_RULE_NOT_VERIFIED_BY_ACCOUNTANT",
    ]
    ev = q(clean_db, "SELECT evidence FROM invoice_lines WHERE kind = 'meeting'")[0].evidence
    m = contoso_meeting(clean_db)
    assert ev["meeting_id"] == str(m.id) and ev["lead_id"] == str(m.lead_id)
    for key in ("signal_id", "qualification_id", "attendance_event_id"):
        assert ev[key]
    assert len(ev["message_ids"]) >= 2 and len(ev["reply_ids"]) == 2  # opener + slot offer; two replies
    inv = q(clean_db, "SELECT number, status, supplier_snapshot, customer_snapshot, tax_wording FROM invoices")[0]
    assert inv.number.startswith("DRAFT-") and inv.status == "draft" and inv.tax_wording == "Not registered for VAT."
    assert inv.customer_snapshot["legal_name"] == "Northwind Demo Services Ltd"
    assert "@" not in str(ev)  # the evidence chain holds ids only, never personal data


def test_refreshing_a_draft_replaces_it_without_double_billing(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    first = draft(world, clean_db)
    second = draft(world, clean_db)
    assert first.invoice_id != second.invoice_id and second.total_minor == first.total_minor
    rows = q(clean_db, "SELECT status FROM invoices ORDER BY created_at")
    assert [r.status for r in rows] == ["void", "draft"]
    assert len(q(clean_db, "SELECT 1 FROM invoice_lines WHERE kind = 'meeting' AND voided_at IS NULL")) == 1


def test_nothing_billed_when_pricing_changed_and_not_resigned(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    with op(world, clean_db) as conn:
        cfg = DEMO_CONFIG.model_copy(
            update={
                "pricing": DEMO_CONFIG.pricing.model_copy(  # type: ignore[union-attr]
                    update={"per_meeting_minor": 40_000}
                )
            }
        )
        save_config(conn, world.tenant_id, cfg, world.operator_id)
    res = draft(world, clean_db)
    assert res.excluded and "PRICING_NOT_SIGNED" in res.excluded[0]["reasons"]
    assert "PRICING_NOT_SIGNED" in res.blocked_reasons


def test_operator_recorded_attendance_is_not_billable(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    m = contoso_meeting(clean_db)
    with op(world, clean_db) as conn:
        conn.execute(
            text("""INSERT INTO attendance_events (tenant_id, meeting_id, source, status, recorded_at)
          VALUES (:t, :m, 'operator', 'attended', now() + interval '1 minute')"""),
            {"t": world.tenant_id, "m": m.id},
        )
    res = draft(world, clean_db)
    assert [x["kind"] for x in res.lines] == ["setup", "retainer"]
    assert res.excluded[0]["reasons"] == ["ATTENDANCE_NOT_CONFIRMED_BY_CLIENT_OR_CALENDAR"]


# --------------------------------------------------------------------------------------------- issue
def test_issue_is_blocked_until_the_owner_has_signed_everything_off(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    res = draft(world, clean_db)
    with pytest.raises(billing.BillingError) as e, op(world, clean_db) as conn:
        billing.issue(conn, invoice_id=res.invoice_id, user_id=world.operator_id, today=TODAY)  # type: ignore[arg-type]
    assert e.value.code == "ISSUE_BLOCKED" and "TAX_RULE_NOT_VERIFIED_BY_ACCOUNTANT" in e.value.reasons
    assert q(clean_db, "SELECT status FROM invoices WHERE id = :i", i=res.invoice_id)[0].status == "draft"
    assert not q(clean_db, "SELECT 1 FROM invoice_counters WHERE last_number > 0")  # no number consumed


def test_issue_assigns_gapless_numbers_bills_the_lead_and_freezes_the_invoice(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    make_issuable(world, clean_db)
    res = draft(world, clean_db)
    assert res.blocked_reasons == []
    with op(world, clean_db) as conn:
        number = billing.issue(conn, invoice_id=res.invoice_id, user_id=world.operator_id, today=TODAY)  # type: ignore[arg-type]
    assert number == "INV-2026-0001"
    notices = q(clean_db, "SELECT idempotency_key FROM dry_run_log WHERE idempotency_key LIKE 'system:invoice:%'")
    assert len(notices) == 1  # the client's admin is told (dry-run) that the invoice is ready
    m = contoso_meeting(clean_db)
    assert q(clean_db, "SELECT state FROM leads WHERE id = :l", l=m.lead_id)[0].state == "billed"
    inv = q(clean_db, "SELECT supplier_snapshot, due_date, issue_date FROM invoices WHERE id = :i", i=res.invoice_id)[0]
    assert inv.supplier_snapshot["legal_name"] == "Example Supplier Ltd" and inv.due_date == TODAY + timedelta(days=14)
    for sql in (
        "UPDATE invoices SET total_minor = 1, subtotal_minor = 1 WHERE id = :i",
        "UPDATE invoices SET number = 'INV-2026-0099' WHERE id = :i",
        "DELETE FROM invoices WHERE id = :i",
        "UPDATE invoice_lines SET amount_minor = 1 WHERE invoice_id = :i",
        "DELETE FROM invoice_lines WHERE invoice_id = :i",
    ):
        with pytest.raises(DBAPIError), op(world, clean_db) as conn:
            conn.execute(text(sql), {"i": res.invoice_id})
    with pytest.raises(billing.BillingError) as e:
        draft(world, clean_db)  # the month is closed; later changes go on the next invoice
    assert e.value.code == "ALREADY_ISSUED"
    # next month: only the retainer (setup and the meeting are already billed) -> number 0002
    nov = draft(world, clean_db, date(2026, 11, 1))
    assert [x["kind"] for x in nov.lines] == ["retainer"]
    with op(world, clean_db) as conn:
        assert (
            billing.issue(
                conn,
                invoice_id=nov.invoice_id,
                user_id=world.operator_id,  # type: ignore[arg-type]
                today=TODAY,
            )
            == "INV-2026-0002"
        )


def test_client_cannot_write_invoices_and_tenants_are_isolated(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    draft(world, clean_db)
    _, ctx = client_ctx(world, clean_db)
    with ctx as conn:  # the client can read its invoice but no row is writable for it
        assert conn.execute(text("SELECT count(*) FROM invoices")).scalar() == 1
        assert conn.execute(text("UPDATE invoices SET total_minor = 0, subtotal_minor = 0")).rowcount == 0
    _, ctx = client_ctx(world, clean_db)
    with pytest.raises(DBAPIError), ctx as conn:
        conn.execute(
            text("""INSERT INTO invoices (tenant_id, number, issue_date, due_date, currency, supplier_snapshot,
          customer_snapshot, place_of_supply, subtotal_minor, tax_minor, total_minor)
          VALUES (:t, 'X-1', now(), now(), 'GBP', '{}', '{}', 'GB', 0, 0, 0)"""),
            {"t": world.tenant_id},
        )
    other = uuid.uuid4()
    with op(world, clean_db) as conn:
        conn.execute(
            text("INSERT INTO tenants (id, slug, legal_name) VALUES (:i, 'other-client', 'Other Ltd')"), {"i": other}
        )
    uid, _ = make_tenant_user(clean_db, settings, cipher, other)
    with transaction(DbContext(tenant_id=other, role="tenant_admin", user_id=uid), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM invoices")).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM tenant_signoffs")).scalar() == 0


# --------------------------------------------------------------------------------------------- disputes
def test_upheld_dispute_before_issue_removes_the_line(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    res = draft(world, clean_db)
    m = contoso_meeting(clean_db)
    uid, ctx = client_ctx(world, clean_db)
    with ctx as conn:
        did = billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="qualification",
            reason="They told us they had no budget this year.",
            now=datetime(2026, 10, 20, tzinfo=UTC),
        )
    assert q(clean_db, "SELECT state FROM leads WHERE id = :l", l=m.lead_id)[0].state == "disputed"
    with op(world, clean_db) as conn:
        out = billing.resolve_dispute(
            conn, dispute_id=did, user_id=world.operator_id, outcome="upheld", note="Agreed: budget not confirmed."
        )
    assert out["effect"] == "removed_from_draft"
    inv = q(clean_db, "SELECT subtotal_minor, total_minor FROM invoices WHERE id = :i", i=res.invoice_id)[0]
    assert (inv.subtotal_minor, inv.total_minor) == (75_000, 75_000)
    assert q(clean_db, "SELECT state FROM leads WHERE id = :l", l=m.lead_id)[0].state == "attended"
    again = draft(world, clean_db)  # an upheld meeting never comes back
    assert "DISPUTE_UPHELD" in again.excluded[0]["reasons"]


def test_upheld_dispute_after_issue_credits_the_next_invoice(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    make_issuable(world, clean_db)
    res = draft(world, clean_db)
    with op(world, clean_db) as conn:
        number = billing.issue(conn, invoice_id=res.invoice_id, user_id=world.operator_id, today=TODAY)  # type: ignore[arg-type]
    m = contoso_meeting(clean_db)
    uid, ctx = client_ctx(world, clean_db)
    with ctx as conn:
        did = billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="attendance",
            reason="The prospect left after two minutes; not a real meeting.",
            now=datetime(2026, 10, 20, tzinfo=UTC),
        )
    with op(world, clean_db) as conn:
        assert (
            billing.resolve_dispute(
                conn,
                dispute_id=did,
                user_id=world.operator_id,
                outcome="upheld",
                note="Calendar shows a 2-minute call.",
            )["effect"]
            == "credit_on_next_invoice"
        )
    nov = draft(world, clean_db, date(2026, 11, 1))
    credit = next(x for x in nov.lines if x["kind"] == "credit")
    assert credit["amount_minor"] == -30_000 and number in credit["description"]
    assert nov.total_minor == 50_000 - 30_000
    assert len([x for x in draft(world, clean_db, date(2026, 11, 1)).lines if x["kind"] == "credit"]) == 1


def test_rejected_dispute_after_issue_keeps_the_meeting_billed(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    make_issuable(world, clean_db)
    res = draft(world, clean_db)
    with op(world, clean_db) as conn:
        billing.issue(conn, invoice_id=res.invoice_id, user_id=world.operator_id, today=TODAY)  # type: ignore[arg-type]
    m = contoso_meeting(clean_db)
    uid, ctx = client_ctx(world, clean_db)
    with ctx as conn:
        did = billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="other",
            reason="Not sure this one counts, please check.",
            now=datetime(2026, 10, 20, tzinfo=UTC),
        )
    with op(world, clean_db) as conn:
        billing.resolve_dispute(
            conn,
            dispute_id=did,
            user_id=world.operator_id,
            outcome="rejected",
            note="Qualified with quotes; attended 30 minutes.",
        )
    assert q(clean_db, "SELECT state FROM leads WHERE id = :l", l=m.lead_id)[0].state == "billed"


def test_dispute_window_and_duplicates(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    m = contoso_meeting(clean_db)
    # the window runs from when attendance was recorded (the database's real clock), so dates are relative to it
    attended = q(clean_db, "SELECT max(recorded_at) AS at FROM attendance_events WHERE meeting_id = :m", m=m.id)[0].at
    uid, ctx = client_ctx(world, clean_db)
    with pytest.raises(billing.BillingError) as e, ctx as conn:
        billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="other",
            reason="Far too late to dispute this.",
            now=attended + timedelta(days=90),
        )
    assert e.value.code == "DISPUTE_WINDOW_CLOSED"
    uid, ctx = client_ctx(world, clean_db)
    with ctx as conn:
        billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="other",
            reason="First dispute on this meeting.",
            now=attended + timedelta(days=1),
        )
    uid, ctx = client_ctx(world, clean_db)
    with pytest.raises(billing.BillingError) as e, ctx as conn:
        billing.raise_dispute(
            conn,
            tenant_id=world.tenant_id,
            meeting_id=m.id,
            user_id=uid,
            kind="other",
            reason="Second dispute on the same meeting.",
            now=attended + timedelta(days=2),
        )
    assert e.value.code == "ALREADY_DISPUTED"


# --------------------------------------------------------------------------------------------- sign-offs
def test_signoffs_are_immutable_and_only_signable_as_yourself(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(DBAPIError), op(world, clean_db) as conn:
        conn.execute(text("UPDATE tenant_signoffs SET content_hash = 'x'"))
    viewer, _ = make_tenant_user(clean_db, settings, cipher, world.tenant_id, role="tenant_viewer")
    with (
        pytest.raises(DBAPIError),
        transaction(DbContext(tenant_id=world.tenant_id, role="tenant_viewer", user_id=viewer), clean_db) as conn,
    ):
        signoffs.sign(conn, tenant_id=world.tenant_id, kind="pricing", user_id=viewer, method="portal")
    _uid, ctx = client_ctx(world, clean_db)
    with pytest.raises(DBAPIError), ctx as conn:  # admin cannot sign in someone else's name
        signoffs.sign(conn, tenant_id=world.tenant_id, kind="pricing", user_id=world.operator_id, method="portal")
    with pytest.raises(DBAPIError), op(world, clean_db) as conn:  # operator must record evidence
        signoffs.sign(
            conn, tenant_id=world.tenant_id, kind="pricing", user_id=world.operator_id, method="recorded_by_operator"
        )
    with op(world, clean_db) as conn:
        signoffs.sign(
            conn,
            tenant_id=world.tenant_id,
            kind="pricing",
            user_id=world.operator_id,
            method="recorded_by_operator",
            evidence_ref="Order form NW-001 signed 1 Oct 2026",
        )


# --------------------------------------------------------------------------------------------- onboarding & reports
def test_onboarding_checklist_gates_activation(world, clean_db, settings, cipher) -> None:  # type: ignore[no-untyped-def]
    with op(world, clean_db) as conn:
        tid = onboarding.create_client(
            conn,
            operator_id=world.operator_id,
            slug="fabrikam-msp",
            legal_name="Fabrikam MSP Ltd",
            company_number="11111111",
            registered_address="2 Example Road, York",
            country_code="GB",
            billing_currency="GBP",
            vat_number=None,
        )
        assert {i.key for i in onboarding.checklist(conn, tid) if not i.done} >= {"lia", "dpa", "config"}
    with pytest.raises(onboarding.OnboardingError) as e, op(world, clean_db) as conn:
        onboarding.activate(conn, tenant_id=tid, operator_id=world.operator_id)
    assert "criteria_signed" in e.value.missing
    with op(world, clean_db) as conn:
        onboarding.set_config(
            conn, tenant_id=tid, operator_id=world.operator_id, config=DEMO_CONFIG.model_dump(mode="json")
        )
        onboarding.record_lia(
            conn,
            tenant_id=tid,
            operator_id=world.operator_id,
            reference="LIA-FAB-1",
            document_url=None,
            approved_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        onboarding.record_dpa(
            conn,
            tenant_id=tid,
            operator_id=world.operator_id,
            version="v1",
            signed_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
        conn.execute(
            text("""INSERT INTO sending_domains (tenant_id, domain, spf_ok, dkim_ok, dmarc_ok, status)
          VALUES (:t, 'mail.fabrikam.example', true, true, true, 'active')"""),
            {"t": tid},
        )
    uid, _ = make_tenant_user(clean_db, settings, cipher, tid)
    with transaction(DbContext(tenant_id=tid, role="tenant_admin", user_id=uid), clean_db) as conn:
        signoffs.sign(conn, tenant_id=tid, kind="qualification_criteria", user_id=uid, method="portal")
        signoffs.sign(conn, tenant_id=tid, kind="pricing", user_id=uid, method="portal")
    with op(world, clean_db) as conn:
        onboarding.activate(conn, tenant_id=tid, operator_id=world.operator_id)
        assert conn.execute(text("SELECT status FROM tenants WHERE id = :t"), {"t": tid}).scalar() == "active"


def test_metrics_for_the_demo_month(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    with op(world, clean_db) as conn:
        r = metrics(conn, world.tenant_id, date(2020, 1, 1), date(2030, 12, 31))
    assert r["leads_discovered"] == 13 and r["contacted"] == 6  # 5 openers + the referral
    assert r["replied"] == 5 and r["positive_replies"] == 1
    assert r["meetings_booked"] == 1 and r["meetings_attended"] == 1 and r["show_rate"] == 1.0
    assert r["ai_cost_status"] == "prices_not_configured" and r["ai_cost_gbp"] is None
    assert r["compliance_blocks"] and "SUPPRESSED_GLOBAL" in r["compliance_blocks"]
    assert r["escalations"] == {"data_protection": 1}


def test_vat_threshold_monitor_warns_before_the_limit(world, clean_db) -> None:  # type: ignore[no-untyped-def]
    with op(world, clean_db) as conn:
        s = billing.vat_threshold_status(conn, TODAY)
    assert s["threshold_minor"] == 9_000_000 and s["warning"] is None and s["pct"] == 0.0
    make_issuable(world, clean_db)
    res = draft(world, clean_db)
    with op(world, clean_db) as conn:
        billing.issue(conn, invoice_id=res.invoice_id, user_id=world.operator_id, today=TODAY)  # type: ignore[arg-type]
        s = billing.vat_threshold_status(conn, TODAY)
    assert s["turnover_minor"] == 105_000 and s["pct"] == round(100 * 105_000 / 9_000_000, 1)
