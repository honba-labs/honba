"""Honba CLI."""
import typer

app = typer.Typer(help="Honba - AI-native trading for Indian markets")


@app.command()
def version() -> None:
    typer.echo("honba 0.1.0")


if __name__ == "__main__":
    app()
