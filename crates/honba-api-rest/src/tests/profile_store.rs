//! `ProfileStore`: isolation, revisions, id validation and atomic writes.

use serde_json::json;

use crate::{Profile, ProfileError, ProfileStore};

fn store() -> (tempfile::TempDir, ProfileStore) {
    let tmp = tempfile::tempdir().unwrap();
    let store = ProfileStore::new(tmp.path().join("profiles"));
    (tmp, store)
}

fn with_shortlist(symbols: &[&str]) -> Profile {
    Profile {
        shortlist: symbols.iter().map(|s| s.to_string()).collect(),
        ..Profile::default()
    }
}

#[test]
fn a_missing_user_gets_the_default_profile() {
    let (_tmp, store) = store();
    let profile = store.get("default").unwrap();
    assert_eq!(profile, Profile::default());
    assert_eq!(profile.revision, 0);
}

#[test]
fn put_stores_the_profile_and_bumps_the_revision() {
    let (_tmp, store) = store();
    let mut p = with_shortlist(&["INFY"]);
    p.preferences = json!({"theme": "dark"});
    let first = store.put("alice", 0, p).unwrap();
    assert_eq!(first.revision, 1);
    let second = store.put("alice", 1, with_shortlist(&["TCS"])).unwrap();
    assert_eq!(second.revision, 2);
    let got = store.get("alice").unwrap();
    assert_eq!(got, second);
    assert_eq!(got.shortlist, vec!["TCS".to_string()]);
}

#[test]
fn a_stale_revision_is_a_conflict_and_changes_nothing() {
    let (_tmp, store) = store();
    store.put("alice", 0, with_shortlist(&["A"])).unwrap();
    let err = store.put("alice", 0, with_shortlist(&["B"])).unwrap_err();
    assert!(matches!(
        err,
        ProfileError::RevisionConflict {
            expected: 0,
            current: 1
        }
    ));
    assert_eq!(store.get("alice").unwrap().shortlist, vec!["A".to_string()]);
}

#[test]
fn users_are_isolated() {
    let (_tmp, store) = store();
    store.put("alice", 0, with_shortlist(&["A"])).unwrap();
    store.put("bob", 0, with_shortlist(&["B"])).unwrap();
    assert_eq!(store.get("alice").unwrap().shortlist, vec!["A".to_string()]);
    assert_eq!(store.get("bob").unwrap().shortlist, vec!["B".to_string()]);
    assert_eq!(store.get("carol").unwrap().revision, 0);
}

#[test]
fn hostile_or_malformed_user_ids_are_rejected() {
    let (tmp, store) = store();
    let too_long = "a".repeat(65);
    for bad in [
        "../x",
        "",
        "a/b",
        "a\\b",
        "a.b",
        "..",
        "a b",
        "é",
        too_long.as_str(),
    ] {
        assert!(
            matches!(store.get(bad), Err(ProfileError::InvalidUserId)),
            "get {bad:?}"
        );
        assert!(
            matches!(
                store.put(bad, 0, Profile::default()),
                Err(ProfileError::InvalidUserId)
            ),
            "put {bad:?}"
        );
    }
    assert!(!tmp.path().join("profiles").exists());
    let longest = "a".repeat(64);
    store.put(&longest, 0, Profile::default()).unwrap();
    store.put("Ab_9-z", 0, Profile::default()).unwrap();
}

#[test]
fn put_leaves_one_file_and_no_temp_files() {
    let (tmp, store) = store();
    store.put("alice", 0, Profile::default()).unwrap();
    store.put("alice", 1, Profile::default()).unwrap();
    let names: Vec<String> = std::fs::read_dir(tmp.path().join("profiles"))
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(names, vec!["alice.json".to_string()]);
}
