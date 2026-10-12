"""Phase 3 vertical slice, end to end on real PostgreSQL with mocked providers and the simulated LLM:
signal -> lead -> enrichment -> compliance -> draft -> approval -> dry-run send (+ follow-up scheduling)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.agents.writer.schemas import WriterDraft
from app.db.session import DbContext, transaction
from app.demo.dataset import EXPECTED
from app.demo.seed import providers, seed_demo
from app.domain.approvals import service as approvals
from app.domain.leads.state_machine import record_objection
from app.llm.backends import MockBackend
from app.llm.simulator import SimulatedBackend
from app.workflows.pipeline import agent_context, process_lead, run_campaign, send_due

pytestmark = pytest.mark.db
IN_HOURS = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


@pytest.fixture
def demo(clean_db, settings):  # type: ignore[no-untyped-def]
    return seed_demo(clean_db, settings)


@pytest.fixture
def ran(clean_db, settings, cipher, demo):  # type: ignore[no-untyped-def]
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    return ctx, run_campaign(ctx, providers())


def states(engine, tenant_id):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        rows = conn.execute(
            text(
                "SELECT coalesce(c.name, 'Sam Patel IT Consulting') AS n, l.state FROM leads l "
                "JOIN companies c ON c.id = l.company_id"
            )
        ).all()
    return {r.n: r.state for r in rows}


def test_every_demo_company_reaches_its_expected_state(clean_db, demo, ran) -> None:  # type: ignore[no-untyped-def]
    _, run = ran
    assert states(clean_db, demo.tenant_id) == EXPECTED
    assert run.hunt.fetched == 14 and run.hunt.deduplicated == 1 and run.hunt.below_threshold == 1
    assert run.hunt.discovered == 12 and run.hunt.injection_flags == 1


def test_signal_fields_and_dedupe(clean_db, demo, ran) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT s.signal_type, s.relevance_score, s.reasoning, s.source_url, c.country_code "
                    "FROM signals s JOIN companies c ON c.id = s.company_id"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 13  # 14 fetched - 1 duplicate (Contoso's second posting within the window)
        assert all(0 <= r["relevance_score"] <= 100 and r["reasoning"] and r["source_url"] for r in rows)
        contoso = conn.execute(
            text("SELECT count(*) FROM signals s JOIN companies c ON c.id = s.company_id WHERE c.name LIKE 'Contoso%'")
        ).scalar()
        assert contoso == 1


def test_prompt_injection_in_source_is_not_obeyed(clean_db, demo, ran, cipher) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        sig = conn.execute(
            text(
                "SELECT s.relevance_score, s.reasoning, s.signal_summary FROM signals s JOIN companies c "
                "ON c.id = s.company_id WHERE c.name LIKE 'Proseware%'"
            )
        ).one()
        assert "injection attempt removed" in sig.reasoning
        assert "evil" not in sig.signal_summary.lower() and "ignore" not in sig.signal_summary.lower()
        bodies = (
            conn.execute(
                text(
                    "SELECT m.body_enc FROM messages m JOIN leads l ON l.id = m.lead_id JOIN companies c "
                    "ON c.id = l.company_id WHERE c.name LIKE 'Proseware%'"
                )
            )
            .scalars()
            .all()
        )
        for b in bodies:
            body = cipher.decrypt(conn, demo.tenant_id, "messages.body", b)
            assert "evil" not in body and "http" not in body and "deposit" not in body.lower()


def test_drafts_are_grounded_and_compliant(clean_db, demo, ran, cipher) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        heads = (
            conn.execute(
                text(
                    "SELECT id, body_enc, compliance_footer, quality_score, status FROM messages "
                    "WHERE sequence_step = 0"
                )
            )
            .mappings()
            .all()
        )
        assert len(heads) == 5
        for h in heads:
            body = cipher.decrypt(conn, demo.tenant_id, "messages.body", h["body_enc"])
            assert len(body.split()) <= 90 and "?" in body and h["status"] == "pending_approval"
            assert float(h["quality_score"]) >= 8.0
            footer = h["compliance_footer"]
            for must in (
                "Northwind Demo Services Ltd",
                "00000000",
                "1 Example Street",
                "{unsubscribe_url}",
                "/privacy/northwind-demo",
                "object at any time",
            ):
                assert must in footer
            claims = conn.execute(
                text("SELECT claim_text, source_url FROM message_personalization_sources WHERE message_id = :m"),
                {"m": h["id"]},
            ).all()
            assert claims and all(
                c.source_url.startswith("https://") and c.claim_text.lower() in body.lower() for c in claims
            )
        follow_ups = conn.execute(text("SELECT count(*) FROM messages WHERE sequence_step > 0")).scalar()
        assert follow_ups == 10
        models = conn.execute(text("SELECT DISTINCT model FROM llm_calls")).scalars().all()
        assert models == ["simulated"]  # no paid model call was made


def test_alternative_tasks_for_blocked_email(clean_db, demo, ran) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM alternative_channel_tasks")).scalar() == 2
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM messages m JOIN leads l ON l.id = m.lead_id "
                    "WHERE l.state IN ('alternative_channel','rejected_by_compliance','suppressed')"
                )
            ).scalar()
            == 0
        )


def test_sole_trader_company_name_is_encrypted(clean_db, demo, ran) -> None:  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        row = conn.execute(
            text("SELECT name, name_enc, name_is_personal FROM companies WHERE legal_form = 'sole_trader'")
        ).one()
        assert row.name is None and row.name_enc is not None and row.name_is_personal


def test_rerunning_is_idempotent(clean_db, settings, cipher, demo, ran) -> None:  # type: ignore[no-untyped-def]
    ctx, _ = ran
    before = states(clean_db, demo.tenant_id)
    again = run_campaign(ctx, providers())
    assert again.hunt.discovered == 0 and again.outcomes == {}
    assert states(clean_db, demo.tenant_id) == before
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        lead = conn.execute(text("SELECT id FROM leads WHERE state = 'pending_approval' LIMIT 1")).scalar_one()
        n = conn.execute(text("SELECT count(*) FROM messages")).scalar()
    assert process_lead(ctx, lead, providers()) == "pending_approval"
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar() == n


def _head(engine, tenant_id, company):  # type: ignore[no-untyped-def]
    with transaction(DbContext.system(tenant_id), engine) as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.lead_id FROM messages m JOIN leads l ON l.id = m.lead_id JOIN companies "
                "c ON c.id = l.company_id WHERE c.name LIKE :n AND m.sequence_step = 0"
            ),
            {"n": company + "%"},
        ).one()


def test_approve_then_dry_run_send_and_schedule_follow_ups(clean_db, settings, cipher, demo, ran) -> None:  # type: ignore[no-untyped-def]
    head = _head(clean_db, demo.tenant_id, "Contoso")
    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        res = approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="approve",
            edits=[],
            reason=None,
            review_seconds=21,
        )
    assert res["status"] == "approved"
    out = send_due(clean_db, settings, cipher, demo.tenant_id, now=IN_HOURS)
    assert [o.status for o in out] == ["dry_run_sent"] and out[0].mode == "dry_run"
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": head.lead_id}).scalar() == "contacted"
        fus = conn.execute(
            text(
                "SELECT sequence_step, scheduled_for, status FROM messages WHERE lead_id = :l AND "
                "sequence_step > 0 ORDER BY 1"
            ),
            {"l": head.lead_id},
        ).all()
        assert [f.status for f in fus] == ["approved", "approved"]
        # 3 and 6 UK business days later at 09:00 Europe/London (Tue 13 Oct -> Fri 16 Oct, Wed 21 Oct)
        assert fus[0].scheduled_for == datetime(2026, 10, 16, 8, 0, tzinfo=UTC)
        assert fus[1].scheduled_for == datetime(2026, 10, 21, 8, 0, tzinfo=UTC)
        payload = conn.execute(text("SELECT payload_enc FROM dry_run_log")).scalar_one()
        p = json.loads(cipher.decrypt(conn, demo.tenant_id, "dry_run_log.payload", payload))
        assert (
            "/u/" in p["body"] and "Northwind Demo Services Ltd" in p["body"] and p["headers"]["List-Unsubscribe-Post"]
        )
        assert p["to"] == "alex.morgan@contoso-it.example"
    # follow-up 1 becomes due and is sent; then an objection cancels follow-up 2
    out2 = send_due(clean_db, settings, cipher, demo.tenant_id, now=datetime(2026, 10, 16, 9, 0, tzinfo=UTC))
    assert [o.status for o in out2] == ["dry_run_sent"]
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        record_objection(conn, head.lead_id, actor_type="system", actor_id="test", reason="replied: not interested")
        assert (
            conn.execute(
                text("SELECT status FROM messages WHERE lead_id = :l AND sequence_step = 2"), {"l": head.lead_id}
            ).scalar()
            == "cancelled"
        )
    assert send_due(clean_db, settings, cipher, demo.tenant_id, now=datetime(2026, 10, 30, 10, tzinfo=UTC)) == []


def test_follow_up_never_sent_after_reply(clean_db, settings, cipher, demo, ran) -> None:  # type: ignore[no-untyped-def]
    head = _head(clean_db, demo.tenant_id, "Woodgrove")
    with transaction(DbContext(role="operator", user_id=demo.operator_id), clean_db) as conn:
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="approve",
            edits=[],
            reason=None,
            review_seconds=10,
        )
    send_due(clean_db, settings, cipher, demo.tenant_id, now=IN_HOURS)
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:  # prospect replies (Phase 4 will do this)
        conn.execute(text("UPDATE leads SET state = 'replied' WHERE id = :l"), {"l": head.lead_id})
    out = send_due(clean_db, settings, cipher, demo.tenant_id, now=datetime(2026, 10, 30, 10, tzinfo=UTC))
    assert [o.status for o in out] == ["skipped", "skipped"]
    assert all("FOLLOW_UP_CANCELLED_LEAD_REPLIED" in o.reason_codes for o in out)


def test_reject_requires_reason_and_disqualifies(clean_db, cipher, demo, ran) -> None:  # type: ignore[no-untyped-def]
    head = _head(clean_db, demo.tenant_id, "Blue Yonder")
    with pytest.raises(approvals.ApprovalError), transaction(DbContext(role="operator"), clean_db) as conn:
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="reject",
            edits=[],
            reason="",
            review_seconds=5,
        )
    with transaction(DbContext(role="operator"), clean_db) as conn:
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="reject",
            edits=[],
            reason="not a fit: in-house SOC only",
            review_seconds=8,
        )
        assert (
            conn.execute(text("SELECT state FROM leads WHERE id = :l"), {"l": head.lead_id}).scalar() == "disqualified"
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM messages WHERE lead_id = :l AND status = 'cancelled'"), {"l": head.lead_id}
            ).scalar()
            == 3
        )


def test_edit_is_logged_with_diff_and_guarded(clean_db, cipher, demo, ran) -> None:  # type: ignore[no-untyped-def]
    head = _head(clean_db, demo.tenant_id, "Adventure Works")
    bad = [approvals.Edit(head.id, "Hi M***, quick question about the contract?")]
    with (
        pytest.raises(approvals.ApprovalError, match="masked"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="edit",
            edits=bad,
            reason=None,
            review_seconds=30,
        )
    with (
        pytest.raises(approvals.ApprovalError, match="links"),
        transaction(DbContext(role="operator"), clean_db) as conn,
    ):
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="edit",
            edits=[approvals.Edit(head.id, "Hi Morgan, see https://x.example ok?")],
            reason=None,
            review_seconds=30,
        )
    new_body = (
        "Hi Morgan,\n\nCongratulations on the health board service desk award. "
        "Would a short note on overflow cover help?\n\nJordan"
    )
    with transaction(DbContext(role="operator"), clean_db) as conn:
        approvals.decide(
            conn,
            cipher,
            tenant_id=demo.tenant_id,
            head_message_id=head.id,
            reviewer_id=demo.operator_id,
            decision="edit",
            edits=[approvals.Edit(head.id, new_body)],
            reason="tone",
            review_seconds=40,
        )
        a = conn.execute(
            text("SELECT decision, diff, edited_body_enc FROM approvals WHERE message_id = :m"), {"m": head.id}
        ).one()
        assert a.decision == "edited" and a.diff and a.edited_body_enc
        body = conn.execute(text("SELECT body_enc FROM messages WHERE id = :m"), {"m": head.id}).scalar_one()
        assert cipher.decrypt(conn, demo.tenant_id, "messages.body", body) == new_body
        # the original verbatim claim no longer appears, so it is no longer recorded as a personalisation claim
        assert (
            conn.execute(
                text("SELECT count(*) FROM message_personalization_sources WHERE message_id = :m"), {"m": head.id}
            ).scalar()
            == 0
        )


def test_sampling_mode_auto_approves_deterministically(clean_db, settings, cipher, demo) -> None:  # type: ignore[no-untyped-def]
    # The 2-week lock is enforced by the DB, so move the lock date via the owner role only in this test.
    from sqlalchemy import create_engine

    from tests.conftest import OWNER_URL

    owner = create_engine(OWNER_URL)
    with owner.begin() as c:
        c.execute(text("SELECT set_config('app.role', 'operator', true)"))
        c.execute(text("ALTER TABLE tenants DISABLE TRIGGER tenants_approval_lock"))
        c.execute(
            text(
                "UPDATE tenants SET approval_mode = 'sampling', approval_sample_pct = 0, "
                "approval_mode_locked_until = current_date - 1 WHERE id = :t"
            ),
            {"t": demo.tenant_id},
        )
        c.execute(text("ALTER TABLE tenants ENABLE TRIGGER tenants_approval_lock"))
    owner.dispose()
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id)
    run = run_campaign(ctx, providers())
    assert run.outcomes.get("approved") == 5 and "pending_approval" not in run.outcomes
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert conn.execute(text("SELECT count(*) FROM approvals WHERE sampled AND reviewer_id IS NULL")).scalar() == 15
    assert approvals.in_sample(demo.tenant_id, 100) and not approvals.in_sample(demo.tenant_id, 0)


def _flawed(body: str) -> str:
    return json.dumps(
        WriterDraft(
            language="en-GB",
            subject_lines=["a", "b", "c"],
            email_body=body,
            follow_ups=["Hi, any thoughts?", "Hi, close the loop?"],
            claims=[],
        ).model_dump()
    )


def test_writer_escalates_after_two_rewrites(clean_db, settings, cipher, demo) -> None:  # type: ignore[no-untyped-def]
    """Drafts that keep failing the checks (here: no sourced detail, unsourced figure) go to a human."""
    sim = SimulatedBackend()

    class FlawedWriter(MockBackend):
        def complete(self, **kw):  # type: ignore[no-untyped-def, override]
            if kw["json_schema"]["title"] == "WriterDraft":
                return super().complete(**kw)
            return sim.complete(**kw)

    bad = _flawed("Hi Alex,\n\n300 MSPs trust us with their service desk. Interested?\n\nJordan")
    backend = FlawedWriter([bad] * 15)
    ctx = agent_context(clean_db, settings, cipher, demo.tenant_id, demo.campaign_id, llm_backend=backend)
    run = run_campaign(ctx, providers())
    assert run.outcomes.get("escalated") == 5
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        esc = conn.execute(text("SELECT reason, context, pre_escalation_state FROM escalations")).all()
        assert len(esc) == 5 and all(e.pre_escalation_state == "compliance_checked" for e in esc)
        assert all("unsourced figure" in e.context["detail"] for e in esc)
        assert conn.execute(text("SELECT count(*) FROM messages")).scalar() == 0
        drafts = conn.execute(text("SELECT count(*) FROM llm_calls WHERE agent = 'writer'")).scalar()
        assert drafts == 15  # 1 draft + 2 rewrites per lead, then stop


def test_invalid_llm_json_escalates_as_llm_failure(clean_db, settings, cipher, demo) -> None:  # type: ignore[no-untyped-def]
    sim = SimulatedBackend()

    class BrokenWriter(MockBackend):
        def complete(self, **kw):  # type: ignore[no-untyped-def, override]
            if kw["json_schema"]["title"] == "WriterDraft":
                return super().complete(**kw)
            return sim.complete(**kw)

    ctx = agent_context(
        clean_db, settings, cipher, demo.tenant_id, demo.campaign_id, llm_backend=BrokenWriter(["not json"] * 30)
    )
    run = run_campaign(ctx, providers())
    assert run.outcomes.get("escalated") == 5
    with transaction(DbContext.system(demo.tenant_id), clean_db) as conn:
        assert set(conn.execute(text("SELECT reason FROM escalations")).scalars()) == {"llm_failure"}
