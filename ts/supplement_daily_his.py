import tushare as ts
import pandas as pd
from sqlalchemy import create_engine, text
import pymysql
import time
from datetime import datetime, date

# 思路： 首先检查日线历史表(ts_stock_daily_his)的最大交易日期，然后从交易日期表(trading_dates)
# 查大于日线交易日期的日期列表
# 按日期调用tushare的api获取该日的所有交易历史记录存入数据库中，即完成该日数据补充。
# 可直接运行，不需要修改日期等条件。


# --- 配置区域 ---
# 1. Tushare Token
TUSHARE_TOKEN = 'YOUR_TUSHARE_TOKEN_HERE'

# 2. MySQL 数据库配置
MYSQL_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'py',  # 你的MySQL用户名
    'password': 'H123456',  # 你的MySQL密码
    'database': 'stock',  # 你要写入的数据库名
    'charset': 'utf8mb4'
}

# 3. MySQL 中存储日线数据的目标表名
DAILY_DATA_TABLE_NAME = 'ts_stock_daily_his'

# 4. 日志文件名
SUCCESS_LOG_FILE = 'log.txt'
ERROR_LOG_FILE = 'error.txt'


# --- 主程序 ---
def main():
    print("=" * 50)
    print("开始执行增量获取股票日线数据任务...")
    print("=" * 50)

    # 1. 初始化 Tushare 和数据库连接
    if TUSHARE_TOKEN == 'YOUR_TUSHARE_TOKEN_HERE':
        print("错误: 请先在代码中设置你的 Tushare API Token。")
        return

    try:
        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        print(f"[{datetime.now()}] Tushare API 初始化成功。")
    except Exception as e:
        print(f"[{datetime.now()}] Tushare API 初始化失败: {e}")
        return

    try:
        mysql_conn_str = (
            f"mysql+pymysql://{MYSQL_CONFIG['user']}:{MYSQL_CONFIG['password']}"
            f"@{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}?"
            f"charset={MYSQL_CONFIG['charset']}"
        )
        engine = create_engine(mysql_conn_str, echo=False)
        print(f"[{datetime.now()}] MySQL 数据库连接成功。")
    except Exception as e:
        print(f"[{datetime.now()}] MySQL 数据库连接失败: {e}")
        return

    # 2. 获取 ts_stock_daily_his 表中的最大交易日期
    max_existing_date_query = f"""
    SELECT MAX(trade_date) AS max_date FROM {DAILY_DATA_TABLE_NAME};
    """
    try:
        print(f"[{datetime.now()}] 正在查询 {DAILY_DATA_TABLE_NAME} 表中的最大交易日期...")
        max_date_result = pd.read_sql(max_existing_date_query, engine)
        max_date_in_db = max_date_result.iloc[0]['max_date']

        if pd.isna(max_date_in_db):  # 如果表是空的，没有最大日期
            max_date_in_db = None
            print(f"[{datetime.now()}] 表 {DAILY_DATA_TABLE_NAME} 似乎为空，将从 trading_dates 表的第一个日期开始处理。")
        else:
            print(f"[{datetime.now()}] {DAILY_DATA_TABLE_NAME} 表中的最大交易日期为: {max_date_in_db.strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"[{datetime.now()}] 查询最大交易日期失败: {e}")
        return

    # 3. 从 trading_dates 表中读取所有大于 max_date_in_db 的交易日期列表
    if max_date_in_db is None:
        # 如果表是空的，就获取 trading_dates 表中所有的日期
        sql_read_new_dates = "SELECT trade_date FROM trading_dates ORDER BY trade_date;"
    else:
        # 否则，获取大于 max_date_in_db 的日期
        sql_read_new_dates = f"""
        SELECT trade_date FROM trading_dates 
        WHERE trade_date > '{max_date_in_db.strftime('%Y-%m-%d')}'
        ORDER BY trade_date;
        """

    try:
        print(f"[{datetime.now()}] 正在从 trading_dates 表读取待处理的交易日期列表...")
        new_dates_df = pd.read_sql(sql_read_new_dates, engine)
        if new_dates_df.empty:
            print("信息: trading_dates 表中没有找到比数据库中最大日期更新的交易日期。")
            print("任务结束。")
            return
        new_trade_dates = new_dates_df['trade_date'].astype(str).tolist()  # 转为字符串，便于传递给API
        total_new_dates = len(new_trade_dates)
        print(f"[{datetime.now()}] 共找到 {total_new_dates} 个待处理的交易日期。")
    except Exception as e:
        print(f"[{datetime.now()}] 读取 trading_dates 表失败: {e}")
        return

    column_mapping = {
        'open': 'open_price',
        'close': 'close_price',
        'change': 'change_amount'
    }
    # 4. 遍历新的交易日期，获取数据并存入数据库
    for i, date in enumerate(new_trade_dates):
        print(f"\n[{datetime.now()}] 处理进度: {i + 1}/{total_new_dates}, 当前日期: {date}")

        try:
            # 调用 pro.daily 接口获取指定日期的所有股票日线数据
            # fields='*' 表示获取所有可用字段
            date_obj = datetime.strptime(date, '%Y-%m-%d')
            date_str = date_obj.strftime('%Y%m%d')
            daily_df = pro.daily(trade_date=date_str,
                                 fields='ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount')
            #daily_df = pro.daily(trade_date=date, fields='*')

            if daily_df is not None and not daily_df.empty:
                # 将交易日期转换为 datetime 类型
                daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'])
                df_mapped = daily_df.rename(columns=column_mapping)

                # 写入 MySQL 数据库
                df_mapped.to_sql(DAILY_DATA_TABLE_NAME, con=engine, if_exists='append', index=False)

                # 日线数据写入 MySQL 数据库
                #daily_df.to_sql(DAILY_DATA_TABLE_NAME, con=engine, if_exists='append', index=False)
                print(f"  -> 成功获取并写入 {len(daily_df)} 条记录。")

                # 5a. 如果成功，将当前日期覆盖写入 log.txt
                with open(SUCCESS_LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(date)
                print(f"  -> 成功日期已写入 {SUCCESS_LOG_FILE}")

                # 基于性能考虑将成交量及相关均线分开存放在表：ts_stock_daily_vol
                #  所以还需要将新增日线成交量插入到该表。
				# 此处设计需优化，将该功能移动成交量均线更新程序里。
                sql_vol = text(""" 
                    insert into ts_stock_daily_vol(ts_code, trade_date, vol)
                    select ts_code, trade_date, vol from ts_stock_daily_his 
                    where trade_date = :trade_date
                """)

                with engine.connect() as conn:
                    result = conn.execute(sql_vol, {"trade_date": date_obj})
                    conn.commit()
                    print(f" -> 成功写入成交量表{result.rowcount}笔记录")

            else:
                print(f"  -> 警告: 未获取到 {date} 的数据或数据为空。")
                # 即使数据为空，也认为处理成功（因为API调用本身没出错）
                with open(SUCCESS_LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(date)
                print(f"  -> (空数据)成功日期已写入 {SUCCESS_LOG_FILE}")

        except Exception as e:
            print(f"  -> 错误: 获取或写入 {date} 的数据时失败: {e}")

            # 5b. 如果出错，将出错的日期**追加**写入 error.txt，并退出程序
            with open(ERROR_LOG_FILE, 'a', encoding='utf-8') as f:  # 'a' 模式表示追加
                f.write(f"{date}\n")  # 换行以便记录多个错误
            print(f"  -> 出错日期已追加写入 {ERROR_LOG_FILE}")
            print("程序因错误而退出。")
            return  # 直接结束整个 main 函数

        # 4. 每处理完一个交易日期，暂停3秒
        if i < total_new_dates - 1:  # 最后一个日期处理完不需要再 sleep
            print(f"  -> 暂停 3 秒以遵守API频率限制...")
            time.sleep(3)

    # 如果所有新日期都处理完了，循环正常结束
    print("\n" + "=" * 50)
    print("所有新的交易日期处理完毕！程序正常结束。")
    print("=" * 50)


if __name__ == "__main__":
    main()