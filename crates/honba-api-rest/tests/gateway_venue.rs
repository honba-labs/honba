//! `GatewayVenue` bridges an async `honba_ports::ExecutionGateway` to the sync
//! `ExecutionEngine` the REST order routes drive (ADR 0030, decision-log entry on the async
//! gateway bridge). The sync calls never wait on the gateway; its answers come back as
//! `ExecutionEvent`s through `drain_events`.

use std::time::Duration;

use honba_api_rest::GatewayVenue;
use honba_engine::ExecutionEngine;
use honba_entities::ExecutionEvent;
use honba_messages::{Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos};
use honba_testing::fixtures::any_instrument;
use honba_testing::ScriptedGateway;

const POLL: Duration = Duration::from_millis(5);

fn order(id: &str, qty: f64) -> Order {
    Order::new(
        OrderId::new(id),
        any_instrument(),
        OrderSide::Buy,
        OrderType::Market,
        qty,
        None,
        TimeInForce::Day,
        UnixNanos::from_u64(10),
        UnixNanos::from_u64(10),
    )
}

/// Drains until `want` events have arrived (or 2 s pass).
async fn events(venue: &mut GatewayVenue, want: usize) -> Vec<ExecutionEvent> {
    let mut got = Vec::new();
    for _ in 0..400 {
        got.extend(venue.drain_events().unwrap());
        if got.len() >= want {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    got
}

#[tokio::test]
async fn submit_returns_at_once_and_fills_come_back_under_the_client_id() {
    let gw = ScriptedGateway::new();
    let mut venue = GatewayVenue::spawn_with_poll(gw.clone(), POLL);
    venue.submit(order("C-1", 10.0)).unwrap();

    // Wait until the gateway has accepted it, then script a partial and a final fill.
    for _ in 0..400 {
        if !gw.venue_ids().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    let venue_id = gw.venue_ids()[0].clone();
    gw.script_fill(&venue_id, 4.0, 101.0);
    gw.script_fill(&venue_id, 6.0, 102.0);

    let evs = events(&mut venue, 2).await;
    let fills: Vec<_> = evs
        .iter()
        .filter_map(|e| match e {
            ExecutionEvent::Fill {
                trade,
                cum_qty,
                complete,
                ..
            } => Some((
                trade.order_id().as_str().to_owned(),
                trade.quantity(),
                *cum_qty,
                *complete,
            )),
            _ => None,
        })
        .collect();
    assert_eq!(
        fills,
        vec![
            ("C-1".to_owned(), 4.0, 4.0, false),
            ("C-1".to_owned(), 6.0, 10.0, true)
        ]
    );
}
