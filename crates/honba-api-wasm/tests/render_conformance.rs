//! E6-S5: the chart consumes each indicator's render contract through the wasm catalog.
//!
//! The catalog `JSON` embeds the contract owned by `honba-indicators::render`; this test proves
//! the two cannot drift and that the contract validates against the outputs the catalog declares.

use honba_api_wasm::indicators::list_indicators_json;
use honba_indicators::render::{self, Pane};
use serde_json::Value;

fn outputs_of(name: &str) -> Vec<&'static str> {
    match name {
        "sma" | "ema" | "rsi" | "atr" | "williams_r" => vec!["value"],
        "macd" => vec!["macd", "signal", "histogram"],
        "bollinger" => vec!["middle", "upper", "lower"],
        "stochastic" => vec!["k", "d"],
        other => panic!("unknown catalog indicator {other}"),
    }
}

#[test]
fn chart_consumes_a_valid_presentation_for_every_indicator() {
    let catalog: Value = serde_json::from_str(&list_indicators_json()).unwrap();
    let indicators = catalog["indicators"].as_array().unwrap();
    assert_eq!(
        indicators.len(),
        render::catalog_names().len(),
        "wasm catalog and render catalog disagree on the indicator set"
    );

    for ind in indicators {
        let name = ind["name"].as_str().unwrap();
        let got = &ind["presentation"];
        assert!(got.is_object(), "{name}: presentation missing");

        // The wasm catalog carries exactly the contract `honba-indicators` defines.
        let expected = serde_json::to_value(render::presentation(name).unwrap()).unwrap();
        assert_eq!(&expected, got, "{name}: catalog presentation drifted");

        // It validates against the outputs the same catalog entry declares.
        let outputs = outputs_of(name);
        let declared: Vec<&str> = ind["outputs"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| o.as_str().unwrap())
            .collect();
        assert_eq!(declared, outputs, "{name}: declared outputs changed");
        render::validate(&render::presentation(name).unwrap(), &outputs)
            .unwrap_or_else(|e| panic!("{name}: {e}"));

        // Colour roles stay theme tokens on the wire; the chart never gets a hex literal.
        for plot in got["plots"].as_array().unwrap() {
            let color = plot["color"].as_str().unwrap();
            assert!(!color.contains('#'), "{name}: {color} is a hex literal");
        }
        let pane = got["pane"].as_str().unwrap();
        assert!(matches!(pane, "overlay" | "separate"), "{name}: {pane}");
    }
}

#[test]
fn price_scale_indicators_are_overlays() {
    let catalog: Value = serde_json::from_str(&list_indicators_json()).unwrap();
    for ind in catalog["indicators"].as_array().unwrap() {
        let name = ind["name"].as_str().unwrap();
        let expected = match render::presentation(name).unwrap().pane {
            Pane::Overlay => "overlay",
            Pane::Separate => "separate",
        };
        assert_eq!(
            ind["presentation"]["pane"].as_str().unwrap(),
            expected,
            "{name}"
        );
    }
}
