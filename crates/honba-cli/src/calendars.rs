use anyhow::Result;

pub fn show(year: i32) -> Result<()> {
    // TODO: use honba-india NseCalendar to list trading days for `year`.
    println!("Trading calendar for year {year}");
    Ok(())
}
