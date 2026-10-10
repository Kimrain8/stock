from utils.dbManager import MySQLHelper
from utils.JSONConfig import ConfigHandler
import pandas as pd

def checkDateOpen():
    """使用在配置文件中："condition": {"func": "conditionFunc:checkDateOpen","params": {}},
        返回True | False
    """
    db = MySQLHelper(db='jhd')
    df = db.qry2df(sql="select * from ts_trading_dates AS ttd where trade_date = CURDATE() and is_open = '1'")
    return True if len(df)>=1 else False

def get_share_float_params():
    """ 在配置文件中使用："params":"conditionFunc.get_share_float_params()",
        返回字典
    """
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

def get_ak_company_params():
    """ 在配置文件中使用："params":"conditionFunc.get_ak_company_params() (a=1, b='x')",
        返回字典
    """
    db = MySQLHelper(db='jhd')
    sql = (f"select symbol from qq_hsj AS qh "
        f"where qh.trade_date = (select max(qh2.trade_date) from qq_hsj AS qh2) "
        f"and not EXISTS ( SELECT 1 FROM ak_stock_basic AS asb WHERE asb.symbol = qh.symbol)"
    )
    df = db.qry2df(sql=sql)
    # l = df['symbol'].to_list()
    
    return {"df2":df}


def always_true():
    return True

if __name__=="__main__":
    # print(get_share_float_params())
    print(get_ak_company_params())


        