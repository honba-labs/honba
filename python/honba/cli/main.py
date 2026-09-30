"""Honba CLI."""
import typer

from honba.cli.indicators import app as indicators_app

app = typer.Typer(help="Honba - AI-native trading for Indian markets")

app.add_typer(indicators_app, name="indicators")


@app.command()
def version() -> None:
    typer.echo("honba 0.1.0")


if __name__ == "__main__":
    app()
