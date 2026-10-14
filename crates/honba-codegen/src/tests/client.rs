//! Unit tests for `crate::client`.

use honba_messages::ENDPOINTS;

use crate::client;
use crate::endpoints::{is_stream, operation_id, request_type_for, response_type_for};

#[test]
fn the_client_covers_every_registered_endpoint() {
    let text = client::render();
    for (method, path) in ENDPOINTS {
        if is_stream(method, path) {
            continue;
        }
        let name = camelize(&operation_id(method, path));
        assert!(
            text.contains(&format!("  async {name}(")),
            "{method} {path} ({name}) is missing from client.ts"
        );
    }
}

#[test]
fn every_request_and_response_type_is_imported_from_domain() {
    let text = client::render();
    assert!(
        text.contains("from '../../types/generated/domain'"),
        "{text}"
    );
    for (method, path) in ENDPOINTS {
        for name in [
            response_type_for(method, path),
            request_type_for(method, path),
        ]
        .into_iter()
        .flatten()
        {
            assert!(text.contains(&format!("  {name},")), "{name} not imported");
        }
    }
}

#[test]
fn the_client_carries_a_do_not_edit_header() {
    assert!(client::render().contains("DO NOT EDIT"));
}

#[test]
fn the_streaming_route_gets_a_typed_helper() {
    let text = client::render();
    assert!(
        text.contains("export async function* streamBacktestEvents("),
        "{text}"
    );
    assert!(
        text.contains("AsyncGenerator<StreamFrame, void, unknown>"),
        "{text}"
    );
    assert!(text.contains("text/event-stream"), "{text}");
}

#[test]
fn rendering_is_deterministic() {
    assert_eq!(client::render(), client::render());
}

/// Mirrors the generator's own camelization, so the test names match the output.
fn camelize(snake: &str) -> String {
    let mut out = String::new();
    for (i, part) in snake.split('_').enumerate() {
        if i == 0 {
            out.push_str(part);
        } else {
            let mut chars = part.chars();
            if let Some(first) = chars.next() {
                out.extend(first.to_uppercase());
                out.push_str(chars.as_str());
            }
        }
    }
    out
}
