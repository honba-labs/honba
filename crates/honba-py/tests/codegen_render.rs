//! The Python-facing codegen entry point renders byte-for-byte what the Rust
//! CLI writes, so `honba schema export` (Python) and `honba schema export`
//! (Rust binary) cannot drift apart. Runs without a Python interpreter.

use honba::pyclasses::codegen::{artifact_names, render};
use honba_codegen::{Artifact, Codegen};

#[test]
fn every_artifact_matches_the_rust_generator() {
    let codegen = Codegen::new();
    for name in artifact_names() {
        let artifact = Artifact::from_name(name).expect("known name");
        let (file, text) = render(name).expect("renders");
        assert_eq!(file, artifact.file_name());
        assert_eq!(text, codegen.render(artifact), "{name}");
    }
}
