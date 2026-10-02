"""Honba CLI."""

import typer

from honba.cli.data import app as data_app
from honba.cli.indicators import app as indicators_app
from honba.cli.schema import app as schema_app
from honba.cli.screener import app as screener_app

app = typer.Typer(help="Honba - AI-native trading for Indian markets")

app.add_typer(data_app, name="data")
app.add_typer(indicators_app, name="indicators")
app.add_typer(schema_app, name="schema")
app.add_typer(screener_app, name="screener")


@app.command()
def version() -> None:
    typer.echo("honba 0.1.0")


if __name__ == "__main__":
    app()
