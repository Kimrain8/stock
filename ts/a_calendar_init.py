import a_trade_calendar
import pymysql
from datetime import datetime


def get_all_trading_dates_optimized(start_date_str='2005-01-01'):
    """
    优化版本：利用库的 get_next_trade_date 功能快速迭代。
    这个方法比逐日检查效率更高。

    Args:
        start_date_str (str): 开始日期字符串，格式 'YYYY-MM-DD'。

    Returns:
        list: 交易日期字符串列表，格式 'YYYY-MM-DD'。
    """
    start_date = start_date_str
    today_str = datetime.today().strftime('%Y-%m-%d')

    trading_dates = []
    current_date_str = start_date

    # 先确认起始日期本身是否为交易日
    if a_trade_calendar.is_trade_date(current_date_str):
        trading_dates.append(current_date_str)
    else:
        # 如果起始日不是交易日，则找到它之后的第一个交易日作为起点
        current_date_str = a_trade_calendar.get_next_trade_date(current_date_str, 1)

    # 循环获取后续交易日，直到超过今天
    while current_date_str <= today_str:
        trading_dates.append(current_date_str)
        try:
            # 获取下一个交易日
            current_date_str = a_trade_calendar.get_next_trade_date(current_date_str, 1)
        except Exception as e:
            # 如果出现日期耗尽的提示，说明已达到内置日历的末尾
            if "更新" in str(e) or "upgrade" in str(e).lower():
                print(f"日历数据已到达尽头或需要更新: {e}")
                break
            else:
                raise e  # 如果是其他错误，则重新抛出

    return trading_dates


def insert_to_mysql(trading_dates, db_config):
    """
    将交易日期列表插入到MySQL数据库。

    Args:
        trading_dates (list): 交易日期列表。
        db_config (dict): 包含数据库连接信息的字典。
    """
    connection = None
    try:
        # 建立数据库连接
        connection = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            charset='utf8mb4'
        )

        cursor = connection.cursor()

        # 创建表的SQL语句 (如果表不存在)
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS trading_dates (
            id INT AUTO_INCREMENT PRIMARY KEY,
            trade_date DATE NOT NULL UNIQUE,
            INDEX idx_trade_date (trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
        """
        cursor.execute(create_table_sql)
        print("检查/创建表 'trading_dates' 完成。")

        # 准备插入SQL语句 (使用 INSERT IGNORE 以防万一有重复数据)
        insert_sql = "INSERT IGNORE INTO trading_dates (trade_date) VALUES (%s)"

        # 将字符串日期转换为 datetime.date 对象以便 MySQL 正确接收
        date_objects = [datetime.strptime(d, '%Y-%m-%d').date() for d in trading_dates]

        # 批量插入数据
        cursor.executemany(insert_sql, [(d,) for d in date_objects])

        connection.commit()
        print(f"成功插入 {cursor.rowcount} 条交易日期记录到数据库。")

    except Exception as e:
        print(f"数据库操作发生错误: {e}")
        if connection:
            connection.rollback()
    finally:
        if connection:
            cursor.close()
            connection.close()
            print("数据库连接已关闭。")


def main():
    # 1. 配置数据库连接信息，请根据您的实际情况修改
    db_config = {
        'host': 'localhost',  # MySQL服务器地址
        'port': 3306,  # MySQL端口
        'user': 'root',  # 您的用户名
        'password': 'H123456',  # 您的密码
        'database': 'stock'  # 您的数据库名
    }

    print("正在使用 a-trade-calendar 库获取A股历史交易日期...")

    trading_dates = get_all_trading_dates_optimized(start_date_str='2005-01-01')
    print(f"共获取到 {len(trading_dates)} 个交易日。")

    print("\n正在将交易日期插入到MySQL数据库...")
    insert_to_mysql(trading_dates, db_config)

    print("\n程序执行完毕。")


if __name__ == "__main__":
    main()