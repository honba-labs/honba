//! Unit tests for `crate::data`.

#[test]
fn load_rejects_non_parquet_sources_before_touching_the_file_system() {
    let err = crate::data::load("does/not/exist.csv", "TCS").unwrap_err();
    assert!(
        err.to_string().contains("unsupported source extension"),
        "{err}"
    );
}
