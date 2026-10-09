use crate::serve::{non_loopback_warning, parse_trading_state};
use honba_messages::TradingState;

#[test]
fn loopback_addresses_do_not_warn() {
    assert!(non_loopback_warning("127.0.0.1:8080".parse().unwrap()).is_none());
    assert!(non_loopback_warning("[::1]:8080".parse().unwrap()).is_none());
}

#[test]
fn other_addresses_warn_about_missing_auth_and_tls() {
    for addr in ["0.0.0.0:8080", "192.168.1.5:80", "[::]:80"] {
        let warning = non_loopback_warning(addr.parse().unwrap()).unwrap();
        assert!(warning.contains("auth"), "{warning}");
        assert!(warning.contains("TLS"), "{warning}");
        assert!(warning.contains(addr), "{warning}");
    }
}

#[test]
fn trading_state_parses_the_wire_spellings_and_refuses_others() {
    assert_eq!(parse_trading_state("active").unwrap(), TradingState::Active);
    assert_eq!(
        parse_trading_state("reducing").unwrap(),
        TradingState::Reducing
    );
    assert_eq!(parse_trading_state("halted").unwrap(), TradingState::Halted);
    let err = parse_trading_state("paused").unwrap_err();
    assert!(err.contains("paused"), "{err}");
}
