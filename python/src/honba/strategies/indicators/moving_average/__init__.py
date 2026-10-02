"""Moving averages: SMA, EMA, Wilder's RMA, WMA, HMA, VWMA, DEMA, TEMA, KAMA, McGinley, ALMA, LSMA."""

from honba.strategies.indicators.moving_average.alma import Alma
from honba.strategies.indicators.moving_average.averages import Ema, Rma, Sma, Wma, make_ma
from honba.strategies.indicators.moving_average.dema import Dema
from honba.strategies.indicators.moving_average.hma import Hma
from honba.strategies.indicators.moving_average.kama import Kama
from honba.strategies.indicators.moving_average.lsma import Lsma, linreg_fit
from honba.strategies.indicators.moving_average.mcginley_dynamic import McGinleyDynamic
from honba.strategies.indicators.moving_average.tema import Tema
from honba.strategies.indicators.moving_average.vwma import Vwma

__all__ = [
    "Alma",
    "Dema",
    "Ema",
    "Hma",
    "Kama",
    "Lsma",
    "McGinleyDynamic",
    "Rma",
    "Sma",
    "Tema",
    "Vwma",
    "Wma",
    "linreg_fit",
    "make_ma",
]
