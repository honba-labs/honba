//! Round-trip tests for the declarative per-adapter mapping tables (E1-S4).
//!
//! The tables are data (`schema/adapter_mappings/*.json`); these tests are the
//! property that keeps them honest: whatever an adapter accepts going out must
//! come back to the same canonical value, and whatever it accepts coming in
//! must resolve to the canonical encoding. A combo the adapter does not
//! support must be a typed error, never a silent default.

use std::collections::BTreeSet;

use serde::Serialize;

use crate::orders::mapping::{bundled_mappings, AdapterMapping, MappingError, WireMapping};
use crate::orders::order::{OrderType, Product, TimeInForce};

/// The canonical wire spelling of a wire enum, via its serde form.
fn canonical<T: Serialize>(value: &T) -> String {
    serde_json::to_value(value)
        .unwrap()
        .as_str()
        .expect("wire enum serializes as a string")
        .to_string()
}

/// Every mapping file the crate embeds, parsed.
fn mappings() -> Vec<AdapterMapping> {
    bundled_mappings().expect("bundled mapping files parse")
}

/// One canonical wire enum as a mapping domain, so the property is written once
/// and run against `Product`, `OrderType` and `TimeInForce`.
trait Domain: Copy + PartialEq + std::fmt::Debug + 'static {
    const NAME: &'static str;
    fn all() -> &'static [Self];
    fn wire(self) -> String;
    fn table(mapping: &AdapterMapping) -> &WireMapping;
    fn to_broker(self, mapping: &AdapterMapping) -> Result<String, MappingError>;
    fn from_broker(mapping: &AdapterMapping, broker: &str) -> Result<Self, MappingError>;
}

impl Domain for Product {
    const NAME: &'static str = "product";
    fn all() -> &'static [Self] {
        Product::ALL
    }
    fn wire(self) -> String {
        canonical(&self)
    }
    fn table(mapping: &AdapterMapping) -> &WireMapping {
        mapping.products()
    }
    fn to_broker(self, mapping: &AdapterMapping) -> Result<String, MappingError> {
        mapping.product_to_broker(self).map(str::to_string)
    }
    fn from_broker(mapping: &AdapterMapping, broker: &str) -> Result<Self, MappingError> {
        mapping.product_from_broker(broker)
    }
}

impl Domain for OrderType {
    const NAME: &'static str = "order_type";
    fn all() -> &'static [Self] {
        OrderType::ALL
    }
    fn wire(self) -> String {
        canonical(&self)
    }
    fn table(mapping: &AdapterMapping) -> &WireMapping {
        mapping.order_types()
    }
    fn to_broker(self, mapping: &AdapterMapping) -> Result<String, MappingError> {
        mapping.order_type_to_broker(self).map(str::to_string)
    }
    fn from_broker(mapping: &AdapterMapping, broker: &str) -> Result<Self, MappingError> {
        mapping.order_type_from_broker(broker)
    }
}

impl Domain for TimeInForce {
    const NAME: &'static str = "time_in_force";
    fn all() -> &'static [Self] {
        TimeInForce::ALL
    }
    fn wire(self) -> String {
        canonical(&self)
    }
    fn table(mapping: &AdapterMapping) -> &WireMapping {
        mapping.time_in_force()
    }
    fn to_broker(self, mapping: &AdapterMapping) -> Result<String, MappingError> {
        mapping.time_in_force_to_broker(self).map(str::to_string)
    }
    fn from_broker(mapping: &AdapterMapping, broker: &str) -> Result<Self, MappingError> {
        mapping.time_in_force_from_broker(broker)
    }
}

fn declared(table: &WireMapping) -> BTreeSet<String> {
    table.canonical_to_broker().keys().cloned().collect()
}

/// Every supported variant encodes to a broker spelling that decodes back; an
/// unsupported variant is declared absent and reported as a typed error.
fn check_canonical_direction<D: Domain>() {
    for mapping in mappings() {
        let table = D::table(&mapping);
        let supported: BTreeSet<String> = D::all().iter().map(|v| v.wire()).collect();
        for key in declared(table) {
            assert!(
                supported.contains(&key),
                "{}/{}: canonical key {key:?} is not a variant",
                mapping.adapter(),
                D::NAME
            );
        }
        for variant in D::all() {
            let wire = variant.wire();
            match D::to_broker(*variant, &mapping) {
                Ok(broker) => {
                    let back = D::from_broker(&mapping, &broker).unwrap_or_else(|e| {
                        panic!("{}/{}: {wire} -> {broker}: {e}", mapping.adapter(), D::NAME)
                    });
                    assert_eq!(
                        back,
                        *variant,
                        "{}/{}: {wire} -> {broker}",
                        mapping.adapter(),
                        D::NAME
                    );
                    assert_eq!(
                        table.broker_to_canonical().get(&broker).map(String::as_str),
                        Some(wire.as_str()),
                        "{}/{}: {broker} is not the declared inverse of {wire}",
                        mapping.adapter(),
                        D::NAME
                    );
                }
                Err(MappingError::Unsupported { .. }) => {
                    assert!(
                        !declared(table).contains(&wire),
                        "{}/{}: {wire} is declared but reports unsupported",
                        mapping.adapter(),
                        D::NAME
                    );
                }
                Err(e) => panic!("{}/{}: {wire}: {e}", mapping.adapter(), D::NAME),
            }
        }
    }
}

/// Every broker spelling in the primary reverse table decodes to a canonical
/// variant that encodes back to exactly that spelling; aliases decode to a
/// canonical value whose primary encoding is the declared inverse.
fn check_broker_direction<D: Domain>() {
    for mapping in mappings() {
        let table = D::table(&mapping);
        let supported: BTreeSet<String> = D::all().iter().map(|v| v.wire()).collect();
        for (broker, wire) in table.broker_to_canonical() {
            assert!(
                supported.contains(wire),
                "{}/{}: {broker} decodes to unknown canonical {wire:?}",
                mapping.adapter(),
                D::NAME
            );
            let variant = D::from_broker(&mapping, broker).unwrap();
            assert_eq!(
                variant.wire(),
                *wire,
                "{}/{}: {broker}",
                mapping.adapter(),
                D::NAME
            );
            assert_eq!(
                D::to_broker(variant, &mapping).unwrap(),
                *broker,
                "{}/{}: {broker} -> {wire} -> broker differs",
                mapping.adapter(),
                D::NAME
            );
        }
        for (alias, wire) in table.broker_aliases() {
            assert!(
                supported.contains(wire),
                "{}/{}: alias {alias} decodes to unknown canonical {wire:?}",
                mapping.adapter(),
                D::NAME
            );
            let variant = D::from_broker(&mapping, alias).unwrap();
            assert_eq!(
                variant.wire(),
                *wire,
                "{}/{}: alias {alias}",
                mapping.adapter(),
                D::NAME
            );
            let primary = D::to_broker(variant, &mapping).unwrap();
            assert_eq!(
                table
                    .broker_to_canonical()
                    .get(&primary)
                    .map(String::as_str),
                Some(wire.as_str()),
                "{}/{}: alias {alias} encodes to {primary}, not the canonical inverse",
                mapping.adapter(),
                D::NAME
            );
        }
    }
}

#[test]
fn every_embedded_mapping_parses_and_names_its_adapter() {
    let names: Vec<String> = mappings().iter().map(|m| m.adapter().to_string()).collect();
    assert!(names.contains(&"zerodha".to_string()), "{names:?}");
    assert!(names.contains(&"dhan".to_string()), "{names:?}");
}

#[test]
fn canonical_to_broker_to_canonical_recovers_every_variant() {
    check_canonical_direction::<Product>();
    check_canonical_direction::<OrderType>();
    check_canonical_direction::<TimeInForce>();
}

#[test]
fn broker_to_canonical_to_broker_recovers_the_broker_string() {
    check_broker_direction::<Product>();
    check_broker_direction::<OrderType>();
    check_broker_direction::<TimeInForce>();
}

#[test]
fn unsupported_combos_are_rejected_with_a_typed_error() {
    let zerodha = mappings()
        .into_iter()
        .find(|m| m.adapter() == "zerodha")
        .expect("zerodha mapping");

    // Kite has no trailing-stop order type and no GTC/FOK/GTD validity.
    assert!(matches!(
        zerodha.order_type_to_broker(OrderType::TrailingStop),
        Err(MappingError::Unsupported { .. })
    ));
    for tif in [TimeInForce::Gtc, TimeInForce::Fok, TimeInForce::Gtd] {
        assert!(matches!(
            zerodha.time_in_force_to_broker(tif),
            Err(MappingError::Unsupported { .. })
        ));
    }

    // An unknown broker spelling is a typed error, not a default.
    assert!(matches!(
        zerodha.product_from_broker("NOPE"),
        Err(MappingError::UnknownBroker { .. })
    ));
}
