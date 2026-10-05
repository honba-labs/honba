import datetime as dt

from honba.entities.instrument import InstrumentId
from honba.research.data_loader.nse import NseBhavcopyProvider, parse_bhavcopy_csv
from honba.screener.coverage import DateInterval

UDIFF_SAMPLE = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2024-10-01,2024-10-01,CM,NSE,STK,1,INE002A01018,RELIANCE,EQ,,,,,,2900.00,2950.00,2890.00,2940.00,2940.00,2895.00,,,,,5000000,14700000000,120000,1,1,,,,
2024-10-01,2024-10-01,CM,NSE,STK,2,INE467B01029,TCS,EQ,,,,,,4200.00,4250.00,4180.00,4230.00,4230.00,4190.00,,,,,3000000,12690000000,90000,1,1,,,,
"""

SEC_SAMPLE = """SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER
RELIANCE, EQ, 01-Oct-2024, 2895.00, 2900.00, 2950.00, 2890.00, 2940.00, 2940.00, 2920.00, 5000000, 146000.00, 120000, 2500000, 50.00
TCS, EQ, 01-Oct-2024, 4190.00, 4200.00, 4250.00, 4180.00, 4230.00, 4230.00, 4210.00, 3000000, 126300.00, 90000, 1500000, 50.00
"""


def test_parse_udiff_bhavcopy():
    bars = parse_bhavcopy_csv(UDIFF_SAMPLE)
    assert "RELIANCE" in bars
    assert "TCS" in bars

    rel = bars["RELIANCE"]
    assert rel.open == 2900.0
    assert rel.high == 2950.0
    assert rel.low == 2890.0
    assert rel.close == 2940.0
    assert rel.volume == 5000000.0


def test_parse_sec_bhavcopy():
    bars = parse_bhavcopy_csv(SEC_SAMPLE)
    assert "RELIANCE" in bars
    assert "TCS" in bars

    tcs = bars["TCS"]
    assert tcs.open == 4200.0
    assert tcs.high == 4250.0
    assert tcs.low == 4180.0
    assert tcs.close == 4230.0
    assert tcs.volume == 3000000.0


def test_nse_bhavcopy_provider_cached(tmp_path):
    provider = NseBhavcopyProvider(cache_dir=tmp_path)
    # Write sample to cache file for 2024-10-01
    cache_file = tmp_path / "bhavcopy_20241001.csv"
    cache_file.write_text(UDIFF_SAMPLE, encoding="utf-8")

    inst = InstrumentId("RELIANCE", "NSE")
    interval = DateInterval(dt.date(2024, 10, 1), dt.date(2024, 10, 2))
    bars = provider.fetch(inst, "1D", interval)

    assert len(bars) == 1
    assert bars[0].close == 2940.0
    assert bars[0].volume == 5000000.0
