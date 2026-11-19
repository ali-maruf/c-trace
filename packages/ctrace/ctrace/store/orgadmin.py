"""Organisation administration: members and invitations, data export, deleting a factory's data."""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import UTC, datetime

from .db import Conn
from .service import NotFound, audit, clean, get_factory, ledger, list_documents, records_frame
from .storage import LocalStorage

ROLES = ("owner", "manager", "viewer", "reviewer", "buyer", "lender")


def members(conn: Conn) -> dict:
    rows = conn.execute("select m.user_id, m.role::text, m.created_at, u.email, u.display_name from memberships m "
                        "join users u on u.id = m.user_id where m.org_id = ctrace_current_org() order by m.created_at").fetchall()
    invites = conn.execute("select id, email, role::text, created_at, accepted_at from org_invites "
                           "where accepted_at is null order by created_at").fetchall()
    return {"members": [clean(r) for r in rows], "invites": [clean(r) for r in invites]}


def invite(conn: Conn, user_id: str, email: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    email = email.strip().lower()
    row = conn.execute("insert into org_invites (org_id, email, role, created_by) values (ctrace_current_org(), %s, %s, %s) "
                       "on conflict (org_id, email) do update set role = excluded.role, accepted_at = null returning *",
                       (email, role, user_id)).fetchone()
    assert row is not None
    audit(conn, user_id, "member.invite", "invite", row["id"], None, {"email": email, "role": role})
    return clean(row)


def _owners(conn: Conn) -> int:
    row = conn.execute("select count(*) n from memberships where org_id = ctrace_current_org() and role = 'owner'").fetchone()
    return int(row["n"]) if row else 0


def set_role(conn: Conn, user_id: str, member_id: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    before = conn.execute("select role::text from memberships where org_id = ctrace_current_org() and user_id = %s", (member_id,)).fetchone()
    if before is None:
        raise NotFound("member")
    if before["role"] == "owner" and role != "owner" and _owners(conn) <= 1:
        raise ValueError("An organisation needs at least one owner")
    conn.execute("update memberships set role = %s where org_id = ctrace_current_org() and user_id = %s", (role, member_id))
    audit(conn, user_id, "member.role", "member", member_id, before, {"role": role})
    return {"user_id": member_id, "role": role}


def remove(conn: Conn, user_id: str, member_id: str) -> None:
    before = conn.execute("select role::text from memberships where org_id = ctrace_current_org() and user_id = %s", (member_id,)).fetchone()
    if before is None:
        raise NotFound("member")
    if before["role"] == "owner" and _owners(conn) <= 1:
        raise ValueError("An organisation needs at least one owner")
    conn.execute("delete from memberships where org_id = ctrace_current_org() and user_id = %s", (member_id,))
    audit(conn, user_id, "member.remove", "member", member_id, before, None)


def accept_invites(owner_conn: Conn, user_id: str, email: str, display_name: str | None = None) -> int:
    """Owner connection. A verified identity whose email was invited joins those organisations on first sign-in."""
    invites = owner_conn.execute("select * from org_invites where lower(email) = lower(%s) and accepted_at is null", (email,)).fetchall()
    if not invites:
        return 0
    with owner_conn.transaction():
        owner_conn.execute("insert into users (id, email, display_name) values (%s, %s, %s) on conflict (id) do nothing",
                           (user_id, email, display_name))
        for inv in invites:
            owner_conn.execute("insert into memberships (org_id, user_id, role) values (%s, %s, %s) on conflict do nothing",
                               (inv["org_id"], user_id, inv["role"]))
            owner_conn.execute("update org_invites set accepted_at = now() where id = %s", (inv["id"],))
            owner_conn.execute("insert into audit_log (org_id, user_id, action, entity, entity_id, after) values (%s, %s, 'member.join', 'invite', %s, %s)",
                               (inv["org_id"], user_id, str(inv["id"]), json.dumps({"role": inv["role"]})))
    return len(invites)


# ------------------------------------------------------------------ export and deletion
def _csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]), extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return buf.getvalue()


def export_zip(conn: Conn, user_id: str, factory_id: str) -> bytes:
    """Everything C-Trace holds about a factory, in open formats (original bills are listed by hash, not included)."""
    f = get_factory(conn, factory_id)
    recs = records_frame(conn, factory_id, include_rejected=True)
    recs["issues"] = recs["issues"].map(lambda x: "; ".join(x) if isinstance(x, list) else x)
    recs["evidence"] = recs["evidence"].map(lambda x: json.dumps(x, ensure_ascii=False, default=str) if x is not None else "")
    def q(sql: str) -> list[dict]:
        return [clean(r) for r in conn.execute(sql, (factory_id,))]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("factory.json", json.dumps(f, indent=1, default=str))
        z.writestr("ledger.csv", _csv(ledger(conn, factory_id)))
        z.writestr("activity_records.csv", recs.to_csv(index=False))
        z.writestr("documents.csv", _csv([{k: d[k] for k in ("id", "file_name", "media_type", "sha256", "size_bytes", "uploaded_at")}
                                          for d in list_documents(conn, factory_id)]))
        z.writestr("production.csv", _csv(q("select period, pieces, source from production_records where factory_id = %s order by period")))
        z.writestr("verification_runs.json", json.dumps(q("select r.*, (select json_agg(v order by v.period) from verification_results v "
                                                         "where v.run_id = r.id) results from verification_runs r where factory_id = %s "
                                                         "order by created_at"), indent=1, default=str))
        z.writestr("reductions.json", json.dumps(q("select * from reductions where factory_id = %s order by created_at"), indent=1, default=str))
        z.writestr("audit_log.json", json.dumps([clean(r) for r in conn.execute(
            "select * from audit_log where org_id = ctrace_current_org() order by id")], indent=1, default=str))
        z.writestr("README.txt", f"C-Trace export of {f['name']} created {datetime.now(UTC).isoformat(timespec='seconds')}.\n"
                                 "Every ledger row's doc_sha256 identifies the original bill (documents.csv).\n")
    audit(conn, user_id, "factory.export", "factory", factory_id, None, {"bytes": buf.tell()})
    return buf.getvalue()


def delete_factory(conn: Conn, user_id: str, factory_id: str, confirm_name: str) -> None:
    f = get_factory(conn, factory_id)
    if confirm_name.strip() != f["name"]:
        raise ValueError("Type the factory's exact name to confirm deletion")
    n_docs = conn.execute("select count(*) n from documents where factory_id = %s", (factory_id,)).fetchone()
    conn.execute("delete from document_blobs where storage_key like %s", (f"{f['org_id']}/{factory_id}/%",))
    if os.getenv("CTRACE_STORAGE", "local") != "db":
        LocalStorage().delete_prefix(f"{f['org_id']}/{factory_id}")
    conn.execute("delete from factories where id = %s", (factory_id,))
    audit(conn, user_id, "factory.delete", "factory", factory_id, {"name": f["name"], "documents": n_docs["n"] if n_docs else 0}, None)
