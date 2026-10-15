//! Per-user profile store: one JSON file per user id in a directory.
//!
//! A profile holds the workbench, layouts, modules, watchlists and preferences of one user.
//! Every profile carries a `revision`; [`ProfileStore::put`] is a compare-and-swap on it, so two
//! tabs of the same user cannot silently overwrite each other. User ids come from bearer
//! `token_id`s and are therefore untrusted: they are validated before they touch a path.

use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The user id used when authentication is off.
pub const DEFAULT_USER_ID: &str = "default";

/// Longest accepted user id, in bytes.
const MAX_USER_ID_LEN: usize = 64;

/// A source feeding a [`Module`], for example a broker adapter or a symbol list.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Source {
    /// Source kind, for example `broker`, `symbols` or `parquet`.
    pub kind: String,
    /// Kind-specific reference (an adapter name, a list name, a directory).
    pub reference: String,
}

/// A named collection of sources.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Module {
    /// Stable module id.
    pub id: String,
    /// Display name.
    pub name: String,
    /// The sources the module groups.
    pub sources: Vec<Source>,
    /// Whether the module is active.
    pub enabled: bool,
}

/// Everything stored for one user.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Profile {
    /// Monotonic revision, bumped by every successful [`ProfileStore::put`].
    pub revision: u64,
    /// Named workbench layouts (opaque to the server).
    pub layouts: BTreeMap<String, Value>,
    /// Modules by id.
    pub modules: BTreeMap<String, Module>,
    /// Named watchlists of symbols.
    pub watchlists: BTreeMap<String, Vec<String>>,
    /// Shortlisted symbols.
    pub shortlist: Vec<String>,
    /// Preferences and theme (opaque to the server).
    pub preferences: Value,
    /// Workbench grid and the symbol in each pane (opaque to the server).
    pub workbench: Value,
}

/// Why a profile operation failed.
#[derive(Debug, thiserror::Error)]
pub enum ProfileError {
    /// The user id is empty, too long, or has characters outside `[A-Za-z0-9_-]`.
    #[error("invalid user id")]
    InvalidUserId,
    /// The caller's expected revision is not the stored one.
    #[error("revision conflict: expected {expected}, current {current}")]
    RevisionConflict {
        /// Revision the caller sent.
        expected: u64,
        /// Revision currently stored.
        current: u64,
    },
    /// Reading or writing the profile file failed.
    #[error("profile io: {0}")]
    Io(#[from] std::io::Error),
    /// The stored file is not a valid profile.
    #[error("profile json: {0}")]
    Json(#[from] serde_json::Error),
}

/// Result alias for profile operations.
pub type Result<T> = std::result::Result<T, ProfileError>;

/// Directory of per-user profile files.
#[derive(Debug)]
pub struct ProfileStore {
    dir: PathBuf,
    /// Serializes writers so the read-check-write of the revision is atomic in-process.
    write_lock: Mutex<()>,
}

impl ProfileStore {
    /// A store rooted at `dir`; the directory is created on first write.
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self {
            dir: dir.into(),
            write_lock: Mutex::new(()),
        }
    }

    /// The user's profile, or a default one (revision 0) when none is stored.
    pub fn get(&self, user_id: &str) -> Result<Profile> {
        let path = self.path_for(user_id)?;
        read_profile(&path)
    }

    /// Replace the user's profile if `expected_revision` is the stored revision. The stored
    /// profile gets `expected_revision + 1`, whatever revision `new_profile` carries.
    pub fn put(
        &self,
        user_id: &str,
        expected_revision: u64,
        mut new_profile: Profile,
    ) -> Result<Profile> {
        let path = self.path_for(user_id)?;
        let _guard = self
            .write_lock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let current = read_profile(&path)?.revision;
        if current != expected_revision {
            return Err(ProfileError::RevisionConflict {
                expected: expected_revision,
                current,
            });
        }
        new_profile.revision = current + 1;
        fs::create_dir_all(&self.dir)?;
        write_atomic(&path, &serde_json::to_vec_pretty(&new_profile)?)?;
        Ok(new_profile)
    }

    fn path_for(&self, user_id: &str) -> Result<PathBuf> {
        validate_user_id(user_id)?;
        Ok(self.dir.join(format!("{user_id}.json")))
    }
}

/// Accept only `[A-Za-z0-9_-]`, 1..=64 bytes.
fn validate_user_id(user_id: &str) -> Result<()> {
    let ok = (1..=MAX_USER_ID_LEN).contains(&user_id.len())
        && user_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-');
    if ok {
        Ok(())
    } else {
        Err(ProfileError::InvalidUserId)
    }
}

fn read_profile(path: &Path) -> Result<Profile> {
    match fs::read(path) {
        Ok(bytes) => Ok(serde_json::from_slice(&bytes)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Profile::default()),
        Err(e) => Err(e.into()),
    }
}

/// Write to a temp file beside `path`, sync it, then rename over `path`.
fn write_atomic(path: &Path, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    let result = (|| {
        let mut file = fs::File::create(&tmp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        fs::rename(&tmp, path)
    })();
    if result.is_err() {
        let _ = fs::remove_file(&tmp);
    }
    result
}
