use anyhow::{bail, Context, Result};

use honba_data::ParquetBarSource;
use honba_market::india::exchange::resolve_instrument;

pub fn load(source: &str, symbol: &str, exchange: Option<&str>) -> Result<()> {
    let instrument = resolve_instrument(symbol, exchange)?;
    let qualified = format!("{}:{}", instrument.exchange(), instrument.symbol());

    let bars = if source.to_ascii_lowercase().ends_with(".parquet") {
        ParquetBarSource::new(source, instrument)
            .bars()
            .with_context(|| format!("reading {source}"))?
    } else {
        bail!("unsupported source extension: {source} (expected .parquet)");
    };

    println!("{} bars loaded from {source} ({qualified})", bars.len());
    if let (Some(first), Some(last)) = (bars.first(), bars.last()) {
        println!(
            "first: ts_event={} close={}",
            first.ts_event(),
            first.close()
        );
        println!("last:  ts_event={} close={}", last.ts_event(), last.close());
    }
    Ok(())
}
