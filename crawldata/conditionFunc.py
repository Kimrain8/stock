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
        返回字典: 解禁开始日期，解禁结束日期
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
        返回df: 还没有基础数据的个股清单
    """
    db = MySQLHelper(db='jhd')
    sql = (f"select symbol from qq_hsj AS qh "
        f"where qh.trade_date = (select max(qh2.trade_date) from qq_hsj AS qh2) "
        f"and not EXISTS ( SELECT 1 FROM ak_stock_basic AS asb WHERE asb.symbol = qh.symbol)"
    )
    df = db.qry2df(sql=sql)
    # l = df['symbol'].to_list()
    
    return {"df2":df}

def get_ak_dividend_params():
    """ 在配置文件中使用："params":"conditionFunc.get_ak_dividend_params() (a=1, b='x')",
        用于报告期分红，股东人数
        返回字典：具体的报告日期。每个季度的最后一天。
    """
    if pd.Timestamp.now().date().strftime('%m%d') <= '0331':
        rdate = str(pd.Timestamp.now().year - 1) + '1231'
    elif pd.Timestamp.now().date().strftime('%m%d') <= '0630':
        rdate = str(pd.Timestamp.now().year ) + '0331'
    elif pd.Timestamp.now().date().strftime('%m%d') <= '0930':
        rdate = str(pd.Timestamp.now().year ) + '0630'
    elif pd.Timestamp.now().date().strftime('%m%d') <= '1231':
        rdate = str(pd.Timestamp.now().year ) + '0930'
    return {"rdate":rdate}

def get_pfmc_frcst_params(cdate=None):
    """ 在配置文件中使用："params":"conditionFunc.get_pfmc_frcst_params() (a=1, b='x')",
        用于业绩预告，
        返回字典：具体的报告日期。每个季度的最后一天。
        cdate: 日期类型
    """
    if cdate is None:
        cdate = pd.Timestamp.now().date()
        
    if cdate.strftime('%m%d') <= '0331':
        rdate = str(cdate.year) + '0331'
    elif cdate.strftime('%m%d') <= '0630':
        rdate = str(cdate.year ) + '0630'
    elif cdate.strftime('%m%d') <= '0930':
        rdate = str(cdate.year ) + '0930'
    elif cdate.strftime('%m%d') <= '1231':
        rdate = str(cdate.year ) + '1231'
    return {"rdate":rdate}


def always_true():
    return True

if __name__=="__main__":
    # print(get_share_float_params())
    print("没有基本信息的代码：",get_ak_company_params())
    print("上一报告期：",get_ak_dividend_params())
    print("下一报告期：",get_pfmc_frcst_params(cdate=None))


        