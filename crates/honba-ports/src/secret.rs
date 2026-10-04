//! The credentials port.

use async_trait::async_trait;

use crate::error::PortResult;

/// Secrets, addressed by key.
///
/// Broker logins, API keys and webhook tokens come from wherever the deployment keeps them — an
/// environment variable, a secrets manager, a keyring — so the lookup is a port rather than a
/// call the engine makes directly. Values are returned as plain strings; implementations read
/// only.
///
/// Implementations must never log a secret, must never echo one into a
/// [`PortError`](crate::PortError), and must not include the requested key's value in an error
/// message. A missing key is `Ok(None)`, not an error.
///
/// Values are immutable once returned, so the port is `Send + Sync` and one store can back every
/// task.
#[async_trait]
pub trait SecretStore: Send + Sync {
    /// Returns the secret stored under `key`, or `Ok(None)` if there is none.
    async fn get_secret(&self, key: &str) -> PortResult<Option<String>>;
}
