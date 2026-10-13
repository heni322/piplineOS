"""HubSpot CRM (the client's own account, through a private app access token they create with the scopes
crm.objects.companies.write/read, crm.objects.contacts.write/read and crm.objects.deals.write/read).

What we write for each meeting PipelineOS books:
- the company (found by its web domain, else created with name and domain)
- the contact (upserted by email: first name, last name, job title), associated with the company
- a deal in the client's chosen pipeline, associated with both, whose stage follows the meeting
  (booked, confirmed, attended, no-show, cancelled). Its name carries our reference so a retry finds it again.

What we read back: only the deal's stage, so an "attended" stage set by the client counts as attendance evidence.

API: https://developers.hubspot.com/docs/api-reference (CRM objects v3, associations v4). Token in the
Authorization header only. 429 -> RateLimited (HubSpot: 100 requests per 10 s on Free/Starter)."""

from __future__ import annotations

from typing import Any

import httpx

from app.adapters.crm.base import CrmIds, CrmMeetingRecord
from app.adapters.http import check, client

BASE_URL = "https://api.hubapi.com"
DEAL_TO_COMPANY = 5  # HubSpot-defined association type ids
DEAL_TO_CONTACT = 3


class HubSpotCrm:
    name = "crm.hubspot"

    def __init__(self, token: str, *, base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None):
        if not token:
            raise ValueError("HubSpot access token missing")
        self._http = client(base_url, transport, headers={"Authorization": f"Bearer {token}"})

    # ------------------------------------------------------------------------------------------------ helpers
    def _search(self, obj: str, prop: str, op: str, value: str) -> str | None:
        body = {
            "filterGroups": [{"filters": [{"propertyName": prop, "operator": op, "value": value}]}],
            "properties": [prop],
            "limit": 1,
        }
        results = check(self._http.post(f"/crm/v3/objects/{obj}/search", json=body)).json().get("results") or []
        return str(results[0]["id"]) if results else None

    def _create(self, obj: str, properties: dict[str, Any], associations: list[dict[str, Any]] | None = None) -> str:
        body: dict[str, Any] = {"properties": properties}
        if associations:
            body["associations"] = associations
        return str(check(self._http.post(f"/crm/v3/objects/{obj}", json=body)).json()["id"])

    @staticmethod
    def _assoc(to_id: str, type_id: int) -> dict[str, Any]:
        return {
            "to": {"id": to_id},
            "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": type_id}],
        }

    # ---------------------------------------------------------------------------------------------- interface
    def sync(self, record: CrmMeetingRecord, known: CrmIds) -> CrmIds:
        out = CrmIds(known.company_id, known.contact_id, known.deal_id)
        if out.deal_id is None:
            out.deal_id = self._search("deals", "dealname", "CONTAINS_TOKEN", record.ref)
        if record.contact is None:  # no personal data may be sent: only an existing deal's stage changes
            if out.deal_id:
                self._update_deal(out.deal_id, record)
            return out

        if out.company_id is None and record.company_domain:
            out.company_id = self._search("companies", "domain", "EQ", record.company_domain)
        if out.company_id is None and (record.company_name or record.company_domain):
            props = {k: v for k, v in (("name", record.company_name), ("domain", record.company_domain)) if v}
            out.company_id = self._create("companies", props)
            out.created.append("company")

        c = record.contact
        upsert = {
            "inputs": [
                {
                    "idProperty": "email",
                    "id": c.email,
                    "properties": {
                        "email": c.email,
                        "firstname": c.first_name,
                        "lastname": c.last_name,
                        **({"jobtitle": c.job_title} if c.job_title else {}),
                    },
                }
            ]
        }
        res = check(self._http.post("/crm/v3/objects/contacts/batch/upsert", json=upsert)).json()
        out.contact_id = str(res["results"][0]["id"])
        if out.company_id:
            check(
                self._http.put(
                    f"/crm/v4/objects/contact/{out.contact_id}/associations/default/company/{out.company_id}"
                )
            )

        if out.deal_id is None:
            assoc = [self._assoc(out.contact_id, DEAL_TO_CONTACT)]
            if out.company_id:
                assoc.append(self._assoc(out.company_id, DEAL_TO_COMPANY))
            out.deal_id = self._create(
                "deals",
                {
                    "dealname": record.deal_name,
                    "pipeline": record.pipeline_id,
                    "dealstage": record.stage_id,
                    "closedate": record.starts_at.isoformat(),
                },
                assoc,
            )
            out.created.append("deal")
        else:
            self._update_deal(out.deal_id, record)
        return out

    def _update_deal(self, deal_id: str, record: CrmMeetingRecord) -> None:
        check(
            self._http.patch(
                f"/crm/v3/objects/deals/{deal_id}",
                json={"properties": {"dealstage": record.stage_id, "closedate": record.starts_at.isoformat()}},
            )
        )

    def deal_stage(self, deal_id: str) -> str | None:
        resp = self._http.get(f"/crm/v3/objects/deals/{deal_id}", params={"properties": "dealstage"})
        if resp.status_code == 404:
            return None
        stage = (check(resp).json().get("properties") or {}).get("dealstage")
        return str(stage) if stage else None
