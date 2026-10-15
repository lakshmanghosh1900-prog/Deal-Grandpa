from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import typer

from deal_grandpa.adapters.sqlite import SQLiteStore
from deal_grandpa.orchestrator import DealGrandpaService, decision_to_dict
from deal_grandpa.domain.policy import parse_timestamp

app = typer.Typer(help="Deal Grandpa: honest, evidence-first shopping decisions.", no_args_is_help=True)
DEFAULT_QUERY = "I need a phone under 30000 with 8GB RAM 128GB storage"


def _service(db: str, fixture: str) -> DealGrandpaService:
    return DealGrandpaService(SQLiteStore(db), fixture)


def _print_decision(decision) -> None:
    data = decision_to_dict(decision)
    typer.echo(f"FIXTURE mode | run_id: {data['run_id']}")
    typer.echo(f"Outcome: {data['outcome']}")
    for reason in data["reasons"]:
        typer.echo(f"Grandpa says: {reason}")
    if data["recommendation"]:
        card = data["recommendation"]
        typer.echo(f"Buy: {card['merchant']} — {card['variant']} — total {card['effective_cost_display']}")
        typer.echo(f"Why: {card['explanation']}")
        typer.echo("Evidence Card:")
        for gate in card["gates"]:
            typer.echo(f"  {gate['gate']} {gate['status']} {gate['reason_code']}: {gate['detail']}")
    else:
        typer.echo("No verified recommendation.")
    if data["ranked"]:
        typer.echo("Ranked exact-variant offers:")
        for rank, card in enumerate(data["ranked"], 1):
            typer.echo(f"  {rank}. {card['merchant']} total={card['effective_cost_display']} trust={card['trust_score']}")
    typer.echo(f"Excluded offers: {len(data['excluded'])}")


def _now_from_env() -> datetime | None:
    value = os.getenv("DEAL_GRANDPA_NOW")
    return parse_timestamp(value) if value else None


@app.command()
def demo(
    fixture: str = typer.Option("tests/fixtures/golden_offers.json", help="FIXTURE path"),
    db: str = typer.Option("var/deal_grandpa.sqlite3", help="SQLite path"),
    query: str = typer.Option(DEFAULT_QUERY, help="Plain-language request"),
) -> None:
    """Run the safe offline FIXTURE demonstration."""
    service = _service(db, fixture)
    _print_decision(service.run(query, now=_now_from_env()))


@app.command()
def search(
    query: str = typer.Argument(..., help="Plain-language shopping request"),
    fixture: str = typer.Option("tests/fixtures/golden_offers.json", help="FIXTURE path"),
    db: str = typer.Option("var/deal_grandpa.sqlite3", help="SQLite path"),
) -> None:
    """Evaluate one request through the same service used by the API."""
    service = _service(db, fixture)
    _print_decision(service.run(query, now=_now_from_env()))


@app.command()
def replay(
    run_id: str = typer.Argument(...),
    db: str = typer.Option("var/deal_grandpa.sqlite3"),
    fixture: str = typer.Option("tests/fixtures/golden_offers.json"),
) -> None:
    """Replay the immutable input snapshot without fetching."""
    result = _service(db, fixture).replay(run_id)
    typer.echo(result)


@app.command("verify-log")
def verify_log(db: str = typer.Option("var/deal_grandpa.sqlite3")) -> None:
    """Verify the hash-chained audit log."""
    ok, message = _service(db, "tests/fixtures/golden_offers.json").verify_log()
    typer.echo(("PASS: " if ok else "FAIL: ") + message)
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def purge(db: str = typer.Option("var/deal_grandpa.sqlite3")) -> None:
    """Run the retention deletion workflow."""
    count = _service(db, "tests/fixtures/golden_offers.json").purge()
    typer.echo(f"FIXTURE retention workflow redacted {count} snapshot(s).")


if __name__ == "__main__":
    app()
