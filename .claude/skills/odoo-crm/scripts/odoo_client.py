#!/usr/bin/env python3
"""
Odoo CRM & Accounting Client — AI Employee Gold Tier skill script.

Wraps Odoo's external JSON-RPC API (Odoo 17/19 Community Edition).
Supports CRM lead creation, contact search, activity logging, and
draft invoice management. Never auto-confirms/posts invoices.

Ref: https://www.odoo.com/documentation/19.0/developer/reference/external_api.html

Usage:
    python odoo_client.py --operation create_lead --data '{"name":"Lead","partner_name":"Acme"}'
    python odoo_client.py --operation search_contacts --data '{"query":"Acme"}'
    python odoo_client.py --operation list_open_invoices
    python odoo_client.py --operation get_accounting_summary
    python odoo_client.py --operation create_draft_invoice --data '{...}'
    python odoo_client.py --operation log_activity --data '{...}'
    python odoo_client.py --operation create_lead --data '{}' --dry-run
"""

import argparse
import json
import os
import sys
import xmlrpc.client
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    _PROJECT_DIR = Path(__file__).resolve().parents[3]
    load_dotenv(_PROJECT_DIR / ".env")
except ImportError:
    pass  # dotenv optional — env vars may be set directly


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


class OdooClient:
    """
    Thin wrapper around Odoo's external JSON-RPC API.

    Uses xmlrpc.client (stdlib) — no extra dependencies required.
    """

    def __init__(self, url: str, db: str, username: str, password: str):
        self.url      = url.rstrip("/")
        self.db       = db
        self.username = username
        self.password = password
        self._uid: int | None = None
        self._common  = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self._models  = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")

    def authenticate(self) -> int:
        """Authenticate and return user ID. Caches uid."""
        if self._uid is None:
            self._uid = self._common.authenticate(
                self.db, self.username, self.password, {}
            )
            if not self._uid:
                raise PermissionError(
                    f"Odoo authentication failed for user '{self.username}' on db '{self.db}'"
                )
        return self._uid

    def execute(self, model: str, method: str, *args, **kwargs) -> object:
        uid = self.authenticate()
        return self._models.execute_kw(
            self.db, uid, self.password,
            model, method,
            list(args),
            kwargs,
        )

    def version(self) -> dict:
        return self._common.version()

    # ── CRM ──────────────────────────────────────────────────────────────────

    def create_lead(self, name: str, partner_name: str = "", email: str = "",
                    phone: str = "", description: str = "", stage: str = "") -> int:
        """Create a CRM lead. Returns the new record ID."""
        vals: dict = {
            "name":         name,
            "partner_name": partner_name,
            "contact_name": partner_name,
        }
        if email:
            vals["email_from"] = email
        if phone:
            vals["phone"] = phone
        if description:
            vals["description"] = description
        return self.execute("crm.lead", "create", vals)

    def search_contacts(self, query: str, limit: int = 10) -> list[dict]:
        """Search res.partner records by name/email."""
        domain = ["|", ["name", "ilike", query], ["email", "ilike", query]]
        ids = self.execute("res.partner", "search", domain, limit=limit)
        if not ids:
            return []
        return self.execute("res.partner", "read", ids,
                            fields=["name", "email", "phone", "is_company"])

    def log_activity(self, model: str, record_id: int, summary: str,
                     activity_type: str = "email", note: str = "") -> int:
        """Log a done activity on a record. Returns activity ID."""
        # Find activity type id
        type_ids = self.execute(
            "mail.activity.type", "search",
            [[["name", "ilike", activity_type]]],
            limit=1,
        )
        activity_type_id = type_ids[0] if type_ids else False
        vals: dict = {
            "res_model": model,
            "res_id":    record_id,
            "summary":   summary,
            "note":      note,
        }
        if activity_type_id:
            vals["activity_type_id"] = activity_type_id
        return self.execute("mail.activity", "create", vals)

    def get_lead(self, lead_id: int) -> dict:
        results = self.execute("crm.lead", "read", [lead_id],
                               fields=["name", "partner_name", "email_from",
                                       "phone", "stage_id", "description"])
        return results[0] if results else {}

    # ── Accounting ────────────────────────────────────────────────────────────

    def create_draft_invoice(self, partner_name: str, lines: list[dict],
                             currency_code: str = "USD", note: str = "") -> int:
        """
        Create a customer invoice in DRAFT state only. Never confirm/post.

        lines: [{"name": "Service X", "price_unit": 500.0, "quantity": 1}]
        Returns new invoice ID.
        """
        # Resolve partner
        partner_ids = self.execute("res.partner", "search",
                                   [[["name", "ilike", partner_name]]], limit=1)
        partner_id = partner_ids[0] if partner_ids else False

        invoice_lines = []
        for line in lines:
            invoice_lines.append((0, 0, {
                "name":       line.get("name", "Service"),
                "price_unit": float(line.get("price_unit", 0)),
                "quantity":   float(line.get("quantity", 1)),
            }))

        vals: dict = {
            "move_type":    "out_invoice",
            "state":        "draft",
            "invoice_line_ids": invoice_lines,
            "narration":    note,
        }
        if partner_id:
            vals["partner_id"] = partner_id

        return self.execute("account.move", "create", vals)

    def list_open_invoices(self, limit: int = 20) -> list[dict]:
        """List posted (open/overdue) customer invoices."""
        domain = [
            ["move_type", "=", "out_invoice"],
            ["state",     "=", "posted"],
            ["payment_state", "in", ["not_paid", "partial"]],
        ]
        ids = self.execute("account.move", "search", domain,
                           limit=limit, order="invoice_date_due asc")
        if not ids:
            return []
        return self.execute("account.move", "read", ids,
                            fields=["name", "partner_id", "amount_total",
                                    "amount_residual", "invoice_date_due",
                                    "payment_state"])

    def get_accounting_summary(self) -> dict:
        """Return high-level accounting metrics for the CEO briefing."""
        # Total receivables (open invoices)
        open_invoices = self.list_open_invoices(limit=100)
        total_receivable = sum(inv.get("amount_residual", 0) for inv in open_invoices)
        overdue_count = 0
        today_str = datetime.now().strftime("%Y-%m-%d")
        for inv in open_invoices:
            due = inv.get("invoice_date_due") or ""
            if due and due < today_str:
                overdue_count += 1

        # Draft invoices (not yet posted)
        draft_ids = self.execute("account.move", "search", [
            ["move_type", "=", "out_invoice"],
            ["state",     "=", "draft"],
        ])
        draft_count = len(draft_ids)

        # CRM pipeline
        lead_count = self.execute("crm.lead", "search_count", [[["active", "=", True]]])

        return {
            "open_invoices_count":    len(open_invoices),
            "total_receivable":       round(total_receivable, 2),
            "overdue_invoices_count": overdue_count,
            "draft_invoices_count":   draft_count,
            "crm_active_leads":       lead_count,
        }


# ── CLI ───────────────────────────────────────────────────────────────────────

_OPERATIONS = [
    "create_lead",
    "search_contacts",
    "log_activity",
    "create_draft_invoice",
    "list_open_invoices",
    "get_accounting_summary",
    "version",
]


def _get_client() -> OdooClient:
    url      = _env("ODOO_URL",      "http://localhost:8069")
    db       = _env("ODOO_DB",       "odoo")
    username = _env("ODOO_USERNAME", "admin")
    password = _env("ODOO_PASSWORD", "admin")
    return OdooClient(url=url, db=db, username=username, password=password)


def _run_operation(client: OdooClient, operation: str, data: dict) -> dict:
    if operation == "version":
        return {"status": "ok", "version": client.version(), "timestamp": _ts()}

    if operation == "create_lead":
        lid = client.create_lead(
            name=data.get("name", "New Lead"),
            partner_name=data.get("partner_name", ""),
            email=data.get("email", ""),
            phone=data.get("phone", ""),
            description=data.get("description", ""),
        )
        return {"status": "created", "model": "crm.lead", "id": lid, "timestamp": _ts()}

    if operation == "search_contacts":
        results = client.search_contacts(data.get("query", ""), limit=data.get("limit", 10))
        return {"status": "ok", "count": len(results), "contacts": results, "timestamp": _ts()}

    if operation == "log_activity":
        aid = client.log_activity(
            model=data.get("model", "crm.lead"),
            record_id=int(data["record_id"]),
            summary=data.get("summary", "AI Employee activity"),
            activity_type=data.get("activity_type", "email"),
            note=data.get("note", ""),
        )
        return {"status": "created", "model": "mail.activity", "id": aid, "timestamp": _ts()}

    if operation == "create_draft_invoice":
        inv_id = client.create_draft_invoice(
            partner_name=data.get("partner_name", ""),
            lines=data.get("lines", []),
            note=data.get("note", ""),
        )
        return {
            "status": "created",
            "model":  "account.move",
            "id":     inv_id,
            "state":  "draft",
            "note":   "Invoice is DRAFT — requires human approval before posting.",
            "timestamp": _ts(),
        }

    if operation == "list_open_invoices":
        invoices = client.list_open_invoices(limit=data.get("limit", 20))
        return {"status": "ok", "count": len(invoices), "invoices": invoices, "timestamp": _ts()}

    if operation == "get_accounting_summary":
        summary = client.get_accounting_summary()
        return {"status": "ok", "summary": summary, "timestamp": _ts()}

    return {"status": "error", "error": f"Unknown operation: {operation}", "timestamp": _ts()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Odoo CRM & Accounting Client — AI Employee Gold Tier."
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=_OPERATIONS,
        help="Operation to perform",
    )
    parser.add_argument(
        "--data",
        default="{}",
        help="JSON payload for the operation (default: '{}')",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without calling Odoo")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as e:
        print(json.dumps({"status": "error", "error": f"Invalid JSON in --data: {e}"}))
        sys.exit(1)

    if args.dry_run:
        result = {
            "status":    "dry_run",
            "operation": args.operation,
            "data":      data,
            "note":      "Dry run — no Odoo call made.",
            "timestamp": _ts(),
        }
        print(json.dumps(result, indent=2))
        sys.exit(0)

    client = _get_client()
    try:
        result = _run_operation(client, args.operation, data)
    except PermissionError as exc:
        result = {"status": "error", "error": str(exc), "timestamp": _ts()}
    except Exception as exc:
        result = {"status": "error", "error": str(exc), "timestamp": _ts()}

    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result.get("status") not in ("error",) else 1)


if __name__ == "__main__":
    main()
