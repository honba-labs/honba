//! `crate::pyclasses::api`: state caching and argument rejection (no interpreter).

use crate::pyclasses::api::{request, ApiRequestError};

fn empty_dir(name: &str) -> String {
    let dir = std::env::temp_dir()
        .join(format!("honba-py-api-{}", std::process::id()))
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.to_str().unwrap().to_owned()
}

#[test]
fn a_missing_data_dir_is_a_data_dir_error() {
    let err = request("/nonexistent/honba-py-api", "GET", "/health", None, None).unwrap_err();
    assert!(matches!(err, ApiRequestError::DataDir(_)), "{err}");
}

#[test]
fn a_failed_load_is_not_cached() {
    let dir = empty_dir("late");
    let missing = format!("{dir}/later");
    assert!(request(&missing, "GET", "/health", None, None).is_err());
    std::fs::create_dir_all(&missing).unwrap();
    let (status, _) = request(&missing, "GET", "/health", None, None).unwrap();
    assert_eq!(status, 200);
}

#[test]
fn the_state_is_loaded_once_per_data_dir() {
    let dir = empty_dir("cached");
    let (status, _) = request(&dir, "GET", "/instruments", None, None).unwrap();
    assert_eq!(status, 200);
    // A file added after the first request is not seen: the snapshot is cached, like `serve`.
    std::fs::write(format!("{dir}/not-a-parquet-name.parquet"), b"junk").unwrap();
    let (status, _) = request(&dir, "GET", "/instruments", None, None).unwrap();
    assert_eq!(status, 200);
}

#[test]
fn bad_arguments_are_dispatch_errors() {
    let dir = empty_dir("args");
    let err = request(&dir, "GET", "/instruments", Some("[1]"), None).unwrap_err();
    assert!(matches!(err, ApiRequestError::Dispatch(_)), "{err}");
    let err = request(&dir, "BAD METHOD", "/health", None, None).unwrap_err();
    assert!(matches!(err, ApiRequestError::Dispatch(_)), "{err}");
}
