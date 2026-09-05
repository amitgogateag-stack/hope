from hope.domain.market_data.models import SignalState

def test_no_signal_is_not_stale_signal():
    assert SignalState.NO_SIGNAL is not SignalState.STALE_SIGNAL
    assert SignalState.NO_SIGNAL.value != SignalState.STALE_SIGNAL.value
