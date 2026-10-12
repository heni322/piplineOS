from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config_sync import ChecklistFile, CountryConfig, TaxFile, TransfersFile

CONFIG = Path(__file__).resolve().parents[3] / "config"


def test_every_country_file_is_valid_and_unverified() -> None:
    files = sorted((CONFIG / "countries").glob("*.yaml"))
    assert len(files) >= 14
    for f in files:
        c = CountryConfig.model_validate(yaml.safe_load(f.read_text()))
        assert c.country_code == f.stem
        assert c.verified_by_counsel is False


def test_seeded_modes_follow_the_rollout_plan() -> None:
    modes = {f.stem: yaml.safe_load(f.read_text())["mode"] for f in (CONFIG / "countries").glob("*.yaml")}
    assert modes["GB"] == "permitted_with_opt_out"
    assert modes["DE"] == modes["AT"] == "consent_required"
    assert all(m == "restricted_review" for c, m in modes.items() if c not in {"GB", "DE", "AT"})
    gb = yaml.safe_load((CONFIG / "countries" / "GB.yaml").read_text())
    assert gb["subscriber_rules"] == {
        "corporate": "permitted_with_opt_out",
        "individual": "consent_required",
        "unknown": "restricted_review",
    }


def test_file_cannot_claim_counsel_verification() -> None:
    data = yaml.safe_load((CONFIG / "countries" / "GB.yaml").read_text())
    with pytest.raises(ValidationError):
        CountryConfig.model_validate({**data, "verified_by_counsel": True})


def test_tax_transfers_checklist_files_validate() -> None:
    tax = TaxFile.model_validate(yaml.safe_load((CONFIG / "tax_rules.yaml").read_text()))
    assert all(not r.verified_by_accountant for r in tax.rules)
    tr = TransfersFile.model_validate(yaml.safe_load((CONFIG / "transfers.yaml").read_text()))
    adequacy = next(m for m in tr.mechanisms if m.kind == "eu_uk_adequacy")
    assert str(adequacy.valid_until) == "2031-12-27"
    cl = ChecklistFile.model_validate(yaml.safe_load((CONFIG / "golive_checklist.yaml").read_text()))
    assert {i.key for i in cl.items} == {
        "uk.ico_fee",
        "uk.solicitor_review",
        "uk.transfer_documents",
        "uk.vat_setup",
        "uk.deliverability",
    }
