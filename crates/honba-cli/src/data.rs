use anyhow::Result;

pub fn load(source: &str, symbol: &str) -> Result<()> {
    // TODO: use honba-algo-import to load the source, print a summary.
    println!("Loading data from '{source}' for symbol '{symbol}'");
    Ok(())
}
