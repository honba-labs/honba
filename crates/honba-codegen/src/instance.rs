//! A small JSON Schema instance validator for the generated domain schema.
//!
//! It exists so tests can check wire vectors against `domain_schema.json`
//! without a validator dependency (ADR 0014). It implements only the keywords
//! `schemars` emits for the wire types and reports any other validating
//! keyword as an error, so a schema change that introduces one fails loudly
//! instead of being silently accepted.

use serde_json::Value;

/// Keywords that carry no validation semantics.
const ANNOTATIONS: &[&str] = &[
    "$schema",
    "$id",
    "$comment",
    "$defs",
    "title",
    "description",
    "default",
    "format",
    "examples",
];

/// Validates `instance` against the definition `type_name` in `root["$defs"]`.
///
/// Returns every violation as `"<json pointer>: <message>"`; empty means valid.
pub fn validate_instance(root: &Value, type_name: &str, instance: &Value) -> Vec<String> {
    let mut errors = Vec::new();
    match root.get("$defs").and_then(|d| d.get(type_name)) {
        Some(schema) => check(root, schema, instance, "", &mut errors),
        None => errors.push(format!("unknown type `{type_name}`")),
    }
    errors
}

fn errs(root: &Value, schema: &Value, instance: &Value, path: &str) -> Vec<String> {
    let mut out = Vec::new();
    check(root, schema, instance, path, &mut out);
    out
}

fn type_matches(name: &str, v: &Value) -> bool {
    match name {
        "null" => v.is_null(),
        "boolean" => v.is_boolean(),
        "string" => v.is_string(),
        "array" => v.is_array(),
        "object" => v.is_object(),
        "number" => v.is_number(),
        "integer" => v.is_i64() || v.is_u64() || v.as_f64().is_some_and(|f| f.fract() == 0.0),
        _ => false,
    }
}

fn check(root: &Value, schema: &Value, inst: &Value, path: &str, out: &mut Vec<String>) {
    let at = |m: String| format!("{}: {m}", if path.is_empty() { "/" } else { path });
    let Some(obj) = schema.as_object() else {
        // `true` / `{}` accept everything; `false` accepts nothing.
        if schema == &Value::Bool(false) {
            out.push(at("schema is `false`".into()));
        }
        return;
    };
    for (key, kw) in obj {
        match key.as_str() {
            k if ANNOTATIONS.contains(&k) => {}
            "$ref" => {
                let name = kw.as_str().unwrap_or_default().strip_prefix("#/$defs/");
                match name.and_then(|n| root.get("$defs").and_then(|d| d.get(n))) {
                    Some(target) => check(root, target, inst, path, out),
                    None => out.push(at(format!("unresolved $ref {kw}"))),
                }
            }
            "type" => {
                let names: Vec<&str> = match kw {
                    Value::String(s) => vec![s.as_str()],
                    Value::Array(a) => a.iter().filter_map(Value::as_str).collect(),
                    _ => vec![],
                };
                if !names.iter().any(|n| type_matches(n, inst)) {
                    out.push(at(format!("expected type {kw}, got {inst}")));
                }
            }
            "enum" => {
                if !kw.as_array().is_some_and(|a| a.contains(inst)) {
                    out.push(at(format!("{inst} is not one of {kw}")));
                }
            }
            "const" => {
                if kw != inst {
                    out.push(at(format!("{inst} != const {kw}")));
                }
            }
            "minimum" => {
                if let (Some(v), Some(min)) = (inst.as_f64(), kw.as_f64()) {
                    if v < min {
                        out.push(at(format!("{v} < minimum {min}")));
                    }
                }
            }
            "minItems" | "maxItems" => {
                if let (Some(a), Some(n)) = (inst.as_array(), kw.as_u64()) {
                    let len = a.len() as u64;
                    if (key == "minItems" && len < n) || (key == "maxItems" && len > n) {
                        out.push(at(format!("{key} {n} violated by {len} items")));
                    }
                }
            }
            "allOf" => {
                for s in kw.as_array().into_iter().flatten() {
                    check(root, s, inst, path, out);
                }
            }
            "anyOf" => {
                let subs = kw.as_array().cloned().unwrap_or_default();
                if !subs.iter().any(|s| errs(root, s, inst, path).is_empty()) {
                    out.push(at("matches none of anyOf".into()));
                }
            }
            "oneOf" => {
                let subs = kw.as_array().cloned().unwrap_or_default();
                let n = subs
                    .iter()
                    .filter(|s| errs(root, s, inst, path).is_empty())
                    .count();
                if n != 1 {
                    out.push(at(format!("matches {n} of oneOf, expected exactly 1")));
                }
            }
            "required" => {
                if let Some(map) = inst.as_object() {
                    for r in kw
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                    {
                        if !map.contains_key(r) {
                            out.push(at(format!("missing required property `{r}`")));
                        }
                    }
                }
            }
            "properties" => {
                if let (Some(map), Some(props)) = (inst.as_object(), kw.as_object()) {
                    for (name, sub) in props {
                        if let Some(v) = map.get(name) {
                            check(root, sub, v, &format!("{path}/{name}"), out);
                        }
                    }
                }
            }
            "additionalProperties" => {
                if let Some(map) = inst.as_object() {
                    let known = obj.get("properties").and_then(Value::as_object);
                    for (name, v) in map
                        .iter()
                        .filter(|(n, _)| !known.is_some_and(|k| k.contains_key(*n)))
                    {
                        match kw {
                            Value::Bool(false) => {
                                out.push(at(format!("unknown property `{name}`")))
                            }
                            Value::Bool(true) => {}
                            sub => check(root, sub, v, &format!("{path}/{name}"), out),
                        }
                    }
                }
            }
            "items" => {
                if let Some(a) = inst.as_array() {
                    for (i, v) in a.iter().enumerate() {
                        check(root, kw, v, &format!("{path}/{i}"), out);
                    }
                }
            }
            other => out.push(at(format!("unsupported schema keyword `{other}`"))),
        }
    }
}
