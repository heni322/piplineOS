"""Tax rule selection and arithmetic. Pure. Rules are configuration marked verified_by_accountant=false until an
accountant's sign-off is recorded; an unknown or ambiguous case blocks rather than guesses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

EU27 = frozenset(
    {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE"}
    | {"IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
)


@dataclass(frozen=True)
class TaxRuleRow:
    id: Any
    code: str
    applies_when: dict[str, Any]
    rate_bps: int
    wording: str | None
    verified: bool


class TaxUnresolved(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def tax_facts(supplier_vat_registered: bool, customer_country: str) -> dict[str, Any]:
    return {
        "supplier_vat_registered": supplier_vat_registered,
        "customer_country": customer_country,
        "customer_in_eu": customer_country in EU27,
        "customer_is_business": True,  # PipelineOS only sells B2B
    }


def select_rule(rules: list[TaxRuleRow], facts: dict[str, Any]) -> TaxRuleRow:
    """The most specific rule whose every condition matches. No match or a tie -> TaxUnresolved."""
    matches = [r for r in rules if all(facts.get(k, object()) == v for k, v in r.applies_when.items())]
    if not matches:
        raise TaxUnresolved("NO_TAX_RULE")
    top = max(len(r.applies_when) for r in matches)
    best = [r for r in matches if len(r.applies_when) == top]
    if len(best) > 1:
        raise TaxUnresolved("AMBIGUOUS_TAX_RULE")
    return best[0]


def tax_minor(subtotal_minor: int, rate_bps: int) -> int:
    """Half-up rounding to the minor unit, symmetric for credits."""
    sign = -1 if subtotal_minor < 0 else 1
    return sign * ((abs(subtotal_minor) * rate_bps + 5000) // 10000)
