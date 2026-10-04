use honba_engine::{Engine, EngineOutput, EventQueue, Handler};
use std::sync::mpsc;
use tokio::task::JoinHandle;

#[derive(Debug)]
pub enum Command {
    Stop,
    Resume,
}

/// Handle to a running engine task.
pub struct EngineHandle {
    tx: mpsc::Sender<Command>,
    join: Option<JoinHandle<anyhow::Result<()>>>,
}

impl EngineHandle {
    pub async fn spawn(engine: Engine, _feed: Box<dyn honba_ports::MarketDataFeed>) -> Self {
        let (tx, rx) = mpsc::channel::<Command>();
        let _ = rx;
        let mut engine = engine;
        let join = tokio::spawn(async move {
            let _engine = &mut engine;
            Ok(())
        });
        Self {
            tx,
            join: Some(join),
        }
    }

    pub async fn submit(&self, cmd: Command) -> anyhow::Result<()> {
        self.tx.send(cmd)?;
        Ok(())
    }

    pub async fn join(mut self) -> anyhow::Result<()> {
        if let Some(j) = self.join.take() {
            let _ = j.await?;
        }
        Ok(())
    }
}

/// Driver for stepping through events.
pub struct EngineDriver<H: Handler> {
    _engine: Engine,
    handler: H,
    queue: EventQueue,
}

impl<H: Handler> EngineDriver<H> {
    pub fn new(engine: Engine, handler: H, queue: EventQueue) -> Self {
        Self {
            _engine: engine,
            handler,
            queue,
        }
    }

    pub fn step(&mut self) -> anyhow::Result<EngineOutput> {
        if let Some(ev) = self.queue.pop() {
            Ok(self.handler.on_event(
                ev.event(),
                honba_engine::Clock::new(honba_messages::UnixNanos::now()).now(),
            )?)
        } else {
            Ok(EngineOutput::None)
        }
    }
}
