use std::sync::Arc;
pub use honba_data::Dataset;

#[derive(Clone, Debug)]
pub struct TrialParams {
    pub seed: u64,
}

#[derive(Clone, Debug)]
pub struct SweepPlan {
    pub trials: Vec<TrialParams>,
}

#[derive(Clone, Debug)]
pub struct SweepReport {
    pub results: Vec<String>,
}

pub async fn run(_pool: &tokio::runtime::Handle, plan: &SweepPlan, _data: Arc<Dataset>) -> anyhow::Result<SweepReport> {
    let mut results = Vec::with_capacity(plan.trials.len());
    for (i, _trial) in plan.trials.iter().enumerate() {
        results.push(format!("trial_{}", i));
    }
    Ok(SweepReport { results })
}
