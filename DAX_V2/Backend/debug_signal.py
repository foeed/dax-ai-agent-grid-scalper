import sys
sys.path.insert(0, '.')
from app.services.backtest_engine import generate_signal
import MetaTrader5 as mt5

mt5.initialize()
mid = 4100
dhigh = 4120
dlow = 4090
daily_range = dhigh - dlow

for vol in [0.002, 0.003, 0.005, 0.008]:
    sig = generate_signal('XAUUSD.m', mid-5, mid+5, 20, dhigh, dlow, 10000, 'M5')
    print('vol=%.3f: sl=%d tp=%d grid=%d buy=%d sell=%d sig=%s' % (
        vol, sig['sl_pts'], sig['tp_pts'], sig['grid_spacing_pts'],
        sig['buy_orders'], sig['sell_orders'], sig['signal']))
mt5.shutdown()
