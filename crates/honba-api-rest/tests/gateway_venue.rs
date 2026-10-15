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

async fn wait_accepted(gw: &ScriptedGateway, n: usize) {
    for _ in 0..400 {
        if gw.venue_ids().len() >= n {
            return;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    panic!("gateway never accepted {n} orders");
}

fn reason_of(event: &ExecutionEvent) -> (&str, &str, f64, u64) {
    match event {
        ExecutionEvent::Rejected {
            order_id,
            reason,
            quantity,
            ts,
            ..
        } => (order_id.as_str(), reason.as_str(), *quantity, ts.as_u64()),
        other => panic!("expected a rejection, got {other:?}"),
    }
}

#[tokio::test]
async fn submit_failures_become_rejections_with_stable_error_code_reasons() {
    use honba_ports::PortError as E;
    let cases = [
        (
            E::Rejected {
                code: "RMS".into(),
                message: "margin".into(),
            },
            "order_rejected",
        ),
        (E::InvalidRequest("x".into()), "validation_invalid_request"),
        (E::Unsupported("x".into()), "unsupported"),
        (E::Unavailable("x".into()), "order_execution_unavailable"),
        (E::Retriable("x".into()), "order_execution_unavailable"),
        (E::Terminal("x".into()), "order_execution_unavailable"),
        (E::Unrecoverable("x".into()), "order_execution_unavailable"),
        (E::Timeout, "timeout"),
        (E::Transport("x".into()), "transport_error"),
        (E::Internal("x".into()), "internal_error"),
    ];
    for (error, want) in cases {
        let gw = ScriptedGateway::new();
        let mut venue = GatewayVenue::spawn_with_poll(gw.clone(), POLL);
        gw.fail_next_submit(error.clone());
        venue.submit(order("C-9", 7.0)).unwrap();
        let evs = events(&mut venue, 1).await;
        assert_eq!(evs.len(), 1, "{error:?}");
        assert_eq!(reason_of(&evs[0]), ("C-9", want, 7.0, 10), "{error:?}");
        assert!(gw.venue_ids().is_empty());
    }
}

#[tokio::test]
async fn cancel_reports_the_unfilled_remainder_at_the_given_time() {
    let gw = ScriptedGateway::new();
    let mut venue = GatewayVenue::spawn_with_poll(gw.clone(), POLL);
    venue.submit(order("C-1", 10.0)).unwrap();
    wait_accepted(&gw, 1).await;
    let vid = gw.venue_ids()[0].clone();
    gw.script_fill(&vid, 4.0, 100.0);
    assert_eq!(events(&mut venue, 1).await.len(), 1);

    venue.cancel("C-1", UnixNanos::from_u64(99)).unwrap();
    let evs = events(&mut venue, 1).await;
    match &evs[..] {
        [ExecutionEvent::Cancelled {
            order_id,
            quantity,
            ts,
            ..
        }] => {
            assert_eq!(order_id.as_str(), "C-1");
            assert_eq!(*quantity, 6.0);
            assert_eq!(ts.as_u64(), 99);
        }
        other => panic!("{other:?}"),
    }
    assert_eq!(gw.cancellations(), vec![vid.clone()], "venue id is used");

    // A late fill for the cancelled order is dropped, not attributed.
    gw.script_fill(&vid, 1.0, 100.0);
    tokio::time::sleep(Duration::from_millis(40)).await;
    assert!(venue.drain_events().unwrap().is_empty());
}

#[tokio::test]
async fn refused_cancel_emits_nothing_and_unknown_cancel_never_reaches_the_gateway() {
    let gw = ScriptedGateway::new();
    let mut venue = GatewayVenue::spawn_with_poll(gw.clone(), POLL);
    venue.submit(order("C-1", 10.0)).unwrap();
    wait_accepted(&gw, 1).await;

    gw.fail_next_cancel(honba_ports::PortError::Rejected {
        code: "FILLED".into(),
        message: "already filled".into(),
    });
    venue.cancel("C-1", UnixNanos::from_u64(50)).unwrap();
    venue.cancel("nope", UnixNanos::from_u64(51)).unwrap();
    tokio::time::sleep(Duration::from_millis(60)).await;
    assert!(venue.drain_events().unwrap().is_empty());
    assert_eq!(
        gw.cancellations().len(),
        1,
        "only the known order is cancelled"
    );
}

#[tokio::test]
async fn fill_stream_errors_and_unknown_fills_do_not_stop_the_bridge() {
    let gw = ScriptedGateway::new();
    let mut venue = GatewayVenue::spawn_with_poll(gw.clone(), POLL);
    venue.submit(order("C-1", 5.0)).unwrap();
    wait_accepted(&gw, 1).await;
    let vid = gw.venue_ids()[0].clone();

    gw.script_fill_error(honba_ports::PortError::Transport("eof".into()));
    gw.script_fill(&OrderId::new("V-404"), 1.0, 100.0);
    gw.script_fill(&vid, 5.0, 100.0);
    let evs = events(&mut venue, 1).await;
    assert_eq!(evs.len(), 1);
    assert!(matches!(
        &evs[0],
        ExecutionEvent::Fill { complete: true, .. }
    ));
}
