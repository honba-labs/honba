//! Unit tests for `crate::fitness`.

use super::{no_equity_report, report};
use crate::{Fitness, SharpeFitness};

#[test]
fn the_score_is_the_sharpe_ratio() {
    assert_eq!(SharpeFitness.score(&report(0, Some(1.75))), 1.75);
    assert_eq!(SharpeFitness.score(&report(0, Some(-0.5))), -0.5);
    assert_eq!(SharpeFitness.score(&report(0, Some(0.0))), 0.0);
}

#[test]
fn a_trial_that_produced_no_returns_scores_zero() {
    assert_eq!(SharpeFitness.score(&no_equity_report(0)), 0.0);
}

#[test]
fn an_undefined_sharpe_scores_zero() {
    assert_eq!(SharpeFitness.score(&report(0, None)), 0.0);
}

#[test]
fn two_trials_with_the_same_equity_score_the_same_whatever_their_id() {
    assert_eq!(
        SharpeFitness.score(&report(0, Some(0.3))),
        SharpeFitness.score(&report(9, Some(0.3)))
    );
}

#[test]
fn scoring_is_a_pure_function_of_the_report() {
    let trial = report(4, Some(0.42));
    let before = trial.clone();
    assert_eq!(SharpeFitness.score(&trial), 0.42);
    assert_eq!(trial, before, "scoring must not touch the report");
    assert_eq!(SharpeFitness.score(&trial), 0.42);
}

#[test]
fn the_fitness_is_object_safe_and_shareable() {
    fn assert_send_sync<T: Send + Sync + 'static>() {}
    assert_send_sync::<SharpeFitness>();
    assert_send_sync::<&'static dyn Fitness>();
    let boxed: Box<dyn Fitness> = Box::new(SharpeFitness);
    assert_eq!(boxed.score(&report(1, Some(2.0))), 2.0);
}
