use anyhow::{bail, Result};

use honba_data::ParquetBarSource;
use honba_messages::{InstrumentId, Venue};

pub fn load(source: &str, symbol: &str) -> Result<()> {
    let instrument = InstrumentId::new(symbol, Venue::new("NSE"));

    let bars = if source.ends_with(".parquet") {
        ParquetBarSource::new(source, instrument).bars()?
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
