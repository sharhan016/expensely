"""Calculation checks on her real statements. Local only: skipped when reference-docs is absent.

Nothing here is committed with data: the folder is git-ignored and this test prints counts only.
The oracle reads each PDF's own balance column (never our parsers) and every figure the app
reports is compared with it.
"""

import csv
import io
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models import ImportRow, Transaction

FOLDER = Path(__file__).resolve().parents[2] / "reference-docs"
pytestmark = pytest.mark.skipif(
    not (FOLDER / "doc-passphrase.md").exists(), reason="reference-docs is not present"
)
_DATE = re.compile(r"^\d{1,2}[-/](?:\d{1,2}|[A-Za-z]{3})[-/]\d{4}$")


def _secrets() -> list[str]:
    text = (FOLDER / "doc-passphrase.md").read_text(errors="ignore")
    return [line.split("=", 1)[1].strip() for line in text.splitlines() if "=" in line]


def _money(cell) -> Decimal | None:
    text = re.sub(r"[^\d.\-]", "", str(cell or "").replace(",", ""))
    return Decimal(text) if re.search(r"\d", text) else None


def _day(cell: str) -> date:
    for pattern in ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(cell.strip(), pattern).date()
        except ValueError:
            continue
    raise ValueError("unreadable date")


def oracle(path: Path, passwords: list[str]) -> dict:
    """Signed amounts from balance deltas: independent of any bank parser in the app."""
    for password in passwords:
        try:
            with pdfplumber.open(path, password=password) as pdf:
                tables = [t for page in pdf.pages for t in (page.extract_tables() or [])]
            break
        except Exception:  # wrong password: try the next one
            continue
    else:
        raise AssertionError("none of the passphrases opened the file")
    rows, collected, opening, previous, bad = [], [], None, None, 0
    layout = None
    for table in tables:
        header = next(
            (
                r
                for r in table
                if any("date" in (c or "").lower() for c in r)
                and any("balance" in (c or "").lower() for c in r)
            ),
            None,
        )
        if header is not None:
            names = [re.sub(r"[^a-z]", "", (c or "").lower()) for c in header]

            def col(*keys, names=names):
                return next(
                    (i for i, n in enumerate(names) if any(n.endswith(k) or n == k for k in keys)),
                    None,
                )

            layout = (
                col("balance"),
                col("withdrawals", "withdrawal", "debit"),
                col("deposits", "deposit", "credit"),
                col("amountinr", "amount"),
                col("type"),
                len(header),
            )
        if layout is None or not table or len(table[0]) != layout[5]:
            continue  # later pages reuse the first page's columns
        bal, wd, dep, amt, kind, _width = layout
        for r in table:
            first = (r[0] or "").strip()
            if not _DATE.match(first):
                if previous is None and "opening" in " ".join(str(c or "") for c in r).lower():
                    opening = previous = _money(r[bal])  # an explicit opening-balance row
                continue
            if wd is not None and dep is not None:
                raw = _money(r[wd]) or _money(r[dep])
                sign = -1 if _money(r[wd]) else 1
            else:
                raw = _money(r[amt])
                sign = -1 if (r[kind] or "").strip().upper() == "DR" else 1
            collected.append((_day(first), sign * abs(raw), _money(r[bal])))
    if collected and collected[0][0] > collected[-1][0]:
        collected.reverse()  # a newest-first listing: balances only chain in date order
    for day, listed, balance in collected:
        if previous is not None:
            bad += abs(balance - previous) != abs(listed)
            signed = balance - previous
        else:
            signed = listed
        if opening is None:
            opening = balance - signed
        rows.append({"date": day, "amount": signed, "balance": balance})
        previous = balance
    return {"rows": rows, "opening": opening, "closing": rows[-1]["balance"], "inconsistent": bad}


@pytest.fixture
def real(workspace, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    secrets = _secrets()
    files = {"federal": next(FOLDER.glob("Account*.pdf")), "pnb": next(FOLDER.glob("PNB*.pdf"))}
    oracles = {name: oracle(path, secrets) for name, path in files.items()}
    upload = workspace.client.post(
        "/imports",
        files=[("files", (p.name, p.read_bytes(), "application/pdf")) for p in files.values()],
    )
    assert upload.status_code == 201
    imports = {}
    for item in upload.json():
        assert item["status"] == "password_required"
        for password in secrets:
            unlocked = workspace.client.post(
                f"/imports/{item['id']}/unlock", json={"password": password}
            )
            if unlocked.status_code == 200:
                imports[unlocked.json()["bank_slug"]] = unlocked.json()
                break
        else:
            raise AssertionError("no passphrase unlocked an import")
    assert set(imports) == {"federal", "pnb"}
    return workspace, files, oracles, imports, secrets


def _confirm(workspace, imports):
    out = {}
    for slug, item in imports.items():
        response = workspace.client.post(f"/imports/{item['id']}/confirm", json={})
        assert response.status_code == 200, (slug, response.text)
        out[slug] = response.json()
    return out


def test_the_oracle_itself_is_consistent_with_each_pdf(real):
    _, _, oracles, _, _ = real
    for name, data in oracles.items():
        assert data["inconsistent"] == 0, name  # every amount equals its balance change
        assert len(data["rows"]) > 20


def test_parsed_rows_match_the_oracle_row_for_row(real):
    workspace, _, oracles, imports, _ = real
    for slug, item in imports.items():
        rows = sorted(
            workspace.db.scalars(
                select(ImportRow).where(ImportRow.statement_import_id == uuid.UUID(item["id"]))
            ).all(),
            key=lambda r: r.id,
        )
        expected = oracles[slug]["rows"]
        assert len(rows) == len(expected)
        for got, want in zip(rows, expected, strict=True):
            assert (got.txn_date, got.amount, got.balance) == (
                want["date"],
                want["amount"],
                want["balance"],
            )
        assert item["reconciliation_status"] == "balanced"
        assert Decimal(item["opening_balance"]) == oracles[slug]["opening"]
        assert Decimal(item["closing_balance"]) == oracles[slug]["closing"]


def test_every_ledger_balance_total_and_export_matches_the_oracle(real):
    workspace, _, oracles, imports, _ = real
    confirmed = _confirm(workspace, imports)
    assert all(c["skipped_duplicates"] == 0 for c in confirmed.values())

    # 1. exact ledger: row count, per-row running balance
    expected_rows = sum(len(o["rows"]) for o in oracles.values())
    assert workspace.db.scalar(select(func.count(Transaction.id))) == expected_rows
    ledger = []
    for offset in range(0, expected_rows, 200):
        ledger += workspace.client.get(
            "/transactions", params={"limit": 200, "offset": offset}
        ).json()["items"]
    by_account = defaultdict(list)
    for item in ledger:
        by_account[item["account_id"]].append(item)
    accounts = {a["id"]: a for a in workspace.client.get("/accounts").json()}
    for slug, data in oracles.items():
        account = next(a for a in accounts.values() if a["bank_slug"] == slug)
        rows = sorted(by_account[account["id"]], key=lambda r: (r["txn_date"], r["id"]))
        assert [Decimal(r["running_balance"]) for r in rows] == [r["balance"] for r in data["rows"]]
        assert [Decimal(r["amount"]) for r in rows] == [r["amount"] for r in data["rows"]]
        # 2. account balance and its as-of date
        assert Decimal(account["current_balance"]) == data["closing"]
        assert account["statement_end"] >= data["rows"][-1]["date"].isoformat()
        age = (date.today() - date.fromisoformat(account["statement_end"])).days
        assert account["age_days"] == age and account["is_stale"] == (age > 35)

    # 3. report totals and every day, across both accounts
    all_rows = [r for o in oracles.values() for r in o["rows"]]
    start, end = min(r["date"] for r in all_rows), max(r["date"] for r in all_rows)
    params = {"from": start.isoformat(), "to": end.isoformat()}
    report = workspace.client.get("/reports/period", params=params).json()
    out = -sum((r["amount"] for r in all_rows if r["amount"] < 0), Decimal(0))
    inn = sum((r["amount"] for r in all_rows if r["amount"] > 0), Decimal(0))
    assert report["movement_count"] == len(all_rows)
    assert sum(Decimal(b["out"]) for b in report["buckets"].values()) == out
    assert sum(Decimal(b["in"]) for b in report["buckets"].values()) == inn
    daily = defaultdict(lambda: [Decimal(0), Decimal(0), 0])
    for r in all_rows:
        daily[r["date"].isoformat()][0 if r["amount"] < 0 else 1] += abs(r["amount"])
        daily[r["date"].isoformat()][2] += 1
    assert {
        d["date"]: (Decimal(d["out"]), Decimal(d["in"]), d["count"]) for d in report["days"]
    } == {k: tuple(v) for k, v in daily.items()}

    # 4. drill-down sums equal each bucket; exports equal the oracle
    for name, info in report["buckets"].items():
        rows, offset = [], 0
        while offset < max(info["count"], 1):
            page = workspace.client.get(
                "/reports/period/rows",
                params={**params, "bucket": name, "limit": 200, "offset": offset},
            ).json()
            rows += page["items"]
            offset += max(page["showing"], 1)
            if page["showing"] == 0:
                break
        assert len(rows) == info["count"]
        assert -sum(
            (Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) < 0), Decimal(0)
        ) == Decimal(info["out"])
        assert sum(
            (Decimal(r["amount"]) for r in rows if Decimal(r["amount"]) > 0), Decimal(0)
        ) == Decimal(info["in"])
    for path, delimiter in (("/reports/period.csv", ","), ("/reports/period.tsv", "\t")):
        table = list(
            csv.reader(
                io.StringIO(workspace.client.get(path, params=params).text), delimiter=delimiter
            )
        )
        assert len(table) - 1 == len(all_rows)
        assert sum((Decimal(r[3]) for r in table[1:]), Decimal(0)) == inn - out

    # 5. dashboard shares the same numbers; bank balance is the sum of closings
    dashboard = workspace.client.get("/dashboard", params=params).json()
    assert dashboard["period"]["buckets"] == report["buckets"]
    assert Decimal(dashboard["bank_balance"]) == sum(
        (o["closing"] for o in oracles.values()), Decimal(0)
    )


def test_coverage_recency_and_idempotent_reupload(real):
    workspace, files, _oracles, imports, secrets = real
    _confirm(workspace, imports)
    coverage = workspace.client.get("/coverage").json()
    for account in coverage["accounts"]:
        assert account["reconciled_through"] is not None and account["first_problem"] is None
    through = {
        a["bank_slug"] if "bank_slug" in a else a["name"]: a["reconciled_through"]
        for a in coverage["accounts"]
    }
    assert min(through.values()) == coverage["overall"]["reconciled_through"]
    assert "Credit cards are not checked yet" in coverage["overall"]["banner"]

    before = (
        workspace.db.scalar(select(func.count(Transaction.id))),
        workspace.client.get(
            "/reports/period", params={"from": "2026-06-01", "to": "2026-10-31"}
        ).json()["buckets"],
    )
    again = workspace.client.post(
        "/imports",
        files=[("files", (p.name, p.read_bytes(), "application/pdf")) for p in files.values()],
    ).json()
    for item in again:
        for password in secrets:
            unlocked = workspace.client.post(
                f"/imports/{item['id']}/unlock", json={"password": password}
            )
            if unlocked.status_code == 200:
                response = workspace.client.post(
                    f"/imports/{item['id']}/confirm",
                    json={"account_id": unlocked.json()["account_id"]},
                )
                assert response.status_code == 200 and response.json()["imported"] == 0
                break
    after = (
        workspace.db.scalar(select(func.count(Transaction.id))),
        workspace.client.get(
            "/reports/period", params={"from": "2026-06-01", "to": "2026-10-31"}
        ).json()["buckets"],
    )
    assert after == before


def test_investigation_finds_exactly_the_rows_the_oracle_says(real):
    workspace, _, oracles, imports, _ = real
    _confirm(workspace, imports)
    amounts = Counter(abs(r["amount"]) for o in oracles.values() for r in o["rows"])
    picks = (
        [a for a, _ in amounts.most_common(3)]
        + sorted(amounts)[-3:]
        + sorted(amounts)[len(amounts) // 2 :][:2]
    )
    for amount in picks:
        found = workspace.client.post(
            "/investigate/tools/find_by_amount", json={"amount": f"{amount:.2f}"}
        ).json()
        assert found["total"] == amounts[amount], amount
        answer = workspace.client.post(
            "/investigate", json={"message": f"where did my {amount:.2f} go"}
        ).json()
        for claim in answer["claims"]:
            if claim["kind"] in {"exact", "possible", "fact", "gap", "close", "none"}:
                assert claim["evidence"]
        refs = [e["ref"] for e in answer["evidence"]]
        if refs:
            assert (
                workspace.client.post("/investigate/evidence", json={"refs": refs}).status_code
                == 200
            )


def test_aggregates_for_the_classification_review(real, capsys):
    """Prints counts only (no narrations, names or amounts) for the manual UX review."""
    workspace, _, oracles, imports, _ = real
    _confirm(workspace, imports)
    state = workspace.client.get("/motion").json()
    cash = workspace.client.get("/cash").json()
    report = workspace.client.get(
        "/reports/period", params={"from": "2026-06-01", "to": "2026-10-31"}
    ).json()
    rails = Counter(
        re.split(r"[/ ]", t.description.strip(), maxsplit=1)[0].upper()[:8]
        for t in workspace.db.scalars(select(Transaction))
    )
    summary = {
        "rows": sum(len(o["rows"]) for o in oracles.values()),
        "buckets": {k: v["count"] for k, v in report["buckets"].items() if v["count"]},
        "questions": len(state["questions"]),
        "questions_rows": sum(q["count"] for q in state["questions"]),
        "transfer_proposals": len(state["transfer_proposals"]),
        "cash_withdrawals": cash["withdrawal_count"],
        "rails": rails.most_common(8),
        "own_transfer_links": workspace.client.get("/transactions", params={"limit": 1}).json()[
            "total"
        ],
    }
    with capsys.disabled():
        print("\nREAL-STATEMENT AGGREGATES", summary)
    assert summary["rows"] > 100
