use anyhow::{bail, Context, Result};

use honba_data::ParquetBarSource;
use honba_messages::{Exchange, InstrumentId};

pub fn load(source: &str, symbol: &str) -> Result<()> {
    let instrument = InstrumentId::new(symbol, Exchange::new("NSE"));

    let bars = if source.to_ascii_lowercase().ends_with(".parquet") {
        ParquetBarSource::new(source, instrument)
            .bars()
            .with_context(|| format!("reading {source}"))?
    } else {
        bail!("unsupported source extension: {source} (expected .parquet)");
    };

    println!("{} bars loaded from {source}", bars.len());
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
