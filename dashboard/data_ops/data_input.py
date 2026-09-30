import sys
from pathlib import Path
from utils.dbManager import MySQLHelper
# print(str(Path(__file__).parent.parent))
sys.path.append(str(Path(__file__).parent.parent))
from config import settings
from data_ops.growth_patterns import find_growth_patterns

db = MySQLHelper(db=settings.DB_NAME)
df = db.qry2df(sql="""
    SELECT ts_code, trade_date, close_price
    FROM ts_stock_daily_his
    WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL 1 YEAR)
    ORDER BY ts_code, trade_date
""")

hits = find_growth_patterns(df, patterns=((14, 0.4), (5, 0.2)), mode="high")
print(hits.head(20))
