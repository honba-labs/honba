//! Per-user profile store: one JSON file per user id in a directory.
//!
//! A profile holds the workbench, layouts, modules, watchlists and preferences of one user.
//! Every profile carries a `revision`; [`ProfileStore::put`] is a compare-and-swap on it, so two
//! tabs of the same user cannot silently overwrite each other. User ids come from bearer
//! `token_id`s and are therefore untrusted: they are validated before they touch a path.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub use honba_api::{Module, Profile, Source};

/// The user id used when authentication is off.
pub const DEFAULT_USER_ID: &str = "default";

/// Longest accepted user id, in bytes.
const MAX_USER_ID_LEN: usize = 64;

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

    /// Read-modify-write the user's profile under the store lock. `change` returns whether it
    /// altered the profile; if so the revision is bumped once and the file rewritten atomically,
    /// otherwise nothing is written. Returns the profile as stored afterwards.
    pub fn update(
        &self,
        user_id: &str,
        change: impl FnOnce(&mut Profile) -> bool,
    ) -> Result<Profile> {
        let path = self.path_for(user_id)?;
        let _guard = self
            .write_lock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut profile = read_profile(&path)?;
        if !change(&mut profile) {
            return Ok(profile);
        }
        profile.revision += 1;
        fs::create_dir_all(&self.dir)?;
        write_atomic(&path, &serde_json::to_vec_pretty(&profile)?)?;
        Ok(profile)
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
