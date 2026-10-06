//! `honba._honba.codegen_render`: the Python CLI's view of honba-codegen.

use crate::pyclasses::codegen::{artifact_names, render};

#[test]
fn every_artifact_is_offered_by_name() {
    assert_eq!(
        artifact_names(),
        vec!["json_schema", "openapi", "typescript", "pyi", "mcp"]
    );
}

#[test]
fn a_known_artifact_renders_with_its_file_name() {
    let (file, text) = render("json_schema").expect("known artifact");
    assert_eq!(file, "domain_schema.json");
    assert!(text.contains("\"$schema\""), "{text}");
}

#[test]
fn an_unknown_artifact_is_an_error_naming_the_choices() {
    let err = render("yaml").unwrap_err();
    assert!(err.contains("yaml"), "{err}");
    assert!(err.contains("json_schema"), "{err}");
}
