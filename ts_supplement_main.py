from ts import supplement_trade_date
from ts import supplement_daily_his
from ts import update_ma_price_auto
from ts import update_ma_vol_auto
from ts import init_macd_all

if __name__ == "__main__":
    supplement_trade_date.main()
    supplement_daily_his.main()
    update_ma_price_auto.main()
    update_ma_vol_auto.main()
    init_macd_all.main()
