from utils.dbManager import MySQLHelper

def checkDateOpen():
    db = MySQLHelper(db='jhd')
    df = db.qry2df(sql="select * from ts_trading_dates AS ttd where trade_date = CURDATE() and is_open = '1'")
    return True if len(df)>=1 else False

        