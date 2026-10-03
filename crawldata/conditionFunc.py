from utils.dbManager import MySQLHelper
from utils.JSONConfig import ConfigHandler
import pandas as pd

def checkDateOpen():
    db = MySQLHelper(db='jhd')
    df = db.qry2df(sql="select * from ts_trading_dates AS ttd where trade_date = CURDATE() and is_open = '1'")
    return True if len(df)>=1 else False

def get_share_float_params():
    config_file="D:/pyprj/stock2/sch_config/.sch_float62min.json"
    hdl = ConfigHandler(config_file)
    hdl.load_config()
    i = hdl.config_data["enddate"]
    start_date_str = pd.to_datetime(i) + pd.Timedelta(days=1)
    start_date_str = start_date_str.strftime('%Y-%m-%d')
    end_date_str = pd.to_datetime(i) + pd.Timedelta(days=4)
    end_date_str = end_date_str.strftime('%Y-%m-%d')
    config_data = {"enddate":end_date_str}
    hdl.upd_config(config_data)
    # print(hdl.config_data['enddate'])
    hdl.save_config()
    return {"start_date_str":start_date_str,"end_date_str":end_date_str}

if __name__=="__main__":
    print(get_share_float_params())


        