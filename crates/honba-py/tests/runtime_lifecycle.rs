//! The explicit runtime end to end: lifecycle, no leaked threads, and the in-process REST
//! path answering identically with and without a started runtime. One test function, because
//! the runtime slot and the process thread count are global.

use std::path::PathBuf;

use honba::pyclasses::api::request;
use honba::runtime::{info, is_running, start, stop};

fn thread_count() -> Option<usize> {
    std::fs::read_dir("/proc/self/task").ok().map(|d| d.count())
}

fn data_dir() -> String {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("honba-py-runtime-lifecycle");
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.to_str().unwrap().to_owned()
}

fn answers(dir: &str) -> Vec<(u16, String)> {
    ["/health", "/instruments", "/nope"]
        .iter()
        .map(|p| request(dir, "GET", p, None, None).unwrap())
        .collect()
}

#[test]
fn lifecycle_leaks_no_threads_and_rest_is_identical_inside_and_outside() {
    let dir = data_dir();
    let outside = answers(&dir);
    let before = thread_count();

    for _ in 0..3 {
        let started = start(Some(3)).unwrap();
        assert!(is_running());
        assert_eq!(info(), Some(started));
        assert_eq!(
            answers(&dir),
            outside,
            "REST answers differ inside the runtime"
        );
        assert!(stop());
    }

    assert!(!is_running());
    assert_eq!(answers(&dir), outside);
    if let (Some(b), Some(a)) = (before, thread_count()) {
        assert_eq!(a, b, "runtime threads leaked across start/stop cycles");
    }
}
