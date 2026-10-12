from utils.dbManager import MySQLHelper
import pandas as pd
from crawl_qq_fq import fetch_qq_one_kline_db
from crawl_stock import Myqq
from crawl_akshare import Myak
    
def fetch_wl_data():
    """获取指定股票的相关数据，日调度的数据不需要在这里"""
    db = MySQLHelper(db='jhd_rd')
    df = db.qry2df(sql=f"select mcs.symbol,mcs.ts_code,mcs.qq_code from my_choice_stock AS mcs where mcs.choice = 'Y'")
    qq = Myqq()
    aks = Myak()
    l = []
    for i in range(len(df)):
        #1  调度股东数据
        print("调度个股股东数据")
        qq.get_qq_gudong(qq_code=df.loc[i,'qq_code'])
        
        #2  调度后复权K线
        print("调度个股复权K线数据")
        fetch_qq_one_kline_db(qqcode=df.loc[i,'qq_code'],cnt=None,adjust='hfq')
        
        #3  更新公司基本信息
        print("更新公司基本信息")
        l = []
        for i in range(len(df)):
            l.append(df.loc[i,'symbol'])
            if len(l) >= 20:
                rst = aks.get_ak_companyinfo(symbol=','.join(l)) #1  调度ak的公司基本信息
                l.clear()
        if len(l) >= 1:
            rst = aks.get_ak_companyinfo(symbol=','.join(l)) #1  调度ak的公司基本信息
        
        #4 分红数据放到天调度中
         
        
        
        
def fetch_week_data():
    db = MySQLHelper(db='jhd_rd')
    df = db.qry2df(sql=f"select mcs.symbol,mcs.ts_code,mcs.qq_code from my_choice_stock AS mcs where mcs.choice = 'Y'")
    aks = Myak()
    l = []
    for i in range(len(df)):
        if len(l) >= 10:
            rst = aks.get_ak_companyinfo(symbol=','.join(l)) #1  调度ak的公司基本信息
            l.clear()
        else:
            l.append(df.loc[i,'symbol'])
    if len(l) >= 1:
        rst = aks.get_ak_companyinfo(symbol=','.join(l)) #1  调度ak的公司基本信息
        l.clear()
    pass
        
        
    
    
    return 0

if __name__ == "__main__":
    rst = fetch_wl_data()
