import tushare as ts
import pandas as pd
from sqlalchemy import create_engine
import pymysql
import time
from datetime import datetime

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
    print("开始执行按日期批量获取股票日线数据任务...")
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

    # 2. 从数据库读取 trading_dates 表中的交易日期列表
    try:
        sql_read_dates = "SELECT trade_date FROM trading_dates where trade_date <= '2018-08-10' ORDER BY trade_date desc ;"  # 假设表名为 trading_dates
        print(f"[{datetime.now()}] 正在从数据库读取交易日期列表...")
        dates_df = pd.read_sql(sql_read_dates, engine)
        if dates_df.empty:
            print("警告: trading_dates 表中没有找到任何交易日期。")
            return
        trade_dates = dates_df['trade_date'].astype(str).tolist()  # 转为字符串，便于传递给API
        total_dates = len(trade_dates)
        print(f"[{datetime.now()}] 共读取到 {total_dates} 个交易日。")
    except Exception as e:
        print(f"[{datetime.now()}] 读取 trading_dates 表失败: {e}")
        return

    # 3. 遍历交易日期，获取数据并存入数据库
    column_mapping = {
        'open':'open_price',
        'close':'close_price',
        'change':'change_amount'
    }
    for i, date in enumerate(trade_dates):
        print(f"\n[{datetime.now()}] 处理进度: {i + 1}/{total_dates}, 当前日期: {date}")

        try:
            # 调用 pro.daily 接口获取指定日期的所有股票日线数据
            # fields='*' 表示获取所有可用字段
            # date 参数可以是 trade_date
            date_obj = datetime.strptime(date, '%Y-%m-%d')
            date_str = date_obj.strftime('%Y%m%d')
            daily_df = pro.daily(trade_date=date_str, fields='ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount')
            if daily_df is not None and not daily_df.empty:
                # 将交易日期转换为 datetime 类型
                daily_df['trade_date'] = pd.to_datetime(daily_df['trade_date'])
                df_mapped = daily_df.rename(columns=column_mapping)

                # 写入 MySQL 数据库
                df_mapped.to_sql(DAILY_DATA_TABLE_NAME, con=engine, if_exists='append', index=False)
                print(f"  -> 成功获取并写入 {len(daily_df)} 条记录。")

                # 5a. 如果成功，将当前日期覆盖写入 log.txt
                # (根据题目描述，虽然逻辑上是处理完一个“股票”，但这里是按日期批量处理的，所以处理完一个日期就算成功)
                with open(SUCCESS_LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(date)
                print(f"  -> 成功日期已写入 {SUCCESS_LOG_FILE}")

            else:
                print(f"  -> 警告: 未获取到 {date} 的数据或数据为空。")
                # 即使数据为空，也认为处理成功（因为API调用本身没出错）
                with open(SUCCESS_LOG_FILE, 'w', encoding='utf-8') as f:
                    f.write(date)
                print(f"  -> (空数据)成功日期已写入 {SUCCESS_LOG_FILE}")

        except Exception as e:
            print(f"  -> 错误: 获取或写入 {date} 的数据时失败: {e}")

            # 5b. 如果出错，将出错的日期覆盖写入 error.txt，并退出程序
            with open(ERROR_LOG_FILE, 'w', encoding='utf-8') as f:
                f.write(date)
            print(f"  -> 出错日期已写入 {ERROR_LOG_FILE}")
            print("程序因错误而退出。")
            return  # 直接结束整个 main 函数

        # 4. 每处理完一个日期（相当于处理完一批股票），暂停2秒
        if i < total_dates - 1:  # 最后一个日期处理完不需要再 sleep
            print(f"  -> 暂停 30 秒以遵守API频率限制...")
            time.sleep(2)

    # 如果所有日期都处理完了，循环正常结束
    print("\n" + "=" * 50)
    print("所有交易日期处理完毕！程序正常结束。")
    print("=" * 50)


if __name__ == "__main__":
    main()
