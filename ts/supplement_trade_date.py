import a_trade_calendar
import pymysql
from datetime import datetime, timedelta

# 直接运行，无需修改日期。

def connect_db(db_config):
    """建立并返回数据库连接"""
    return pymysql.connect(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        charset='utf8mb4'
    )


def get_last_trade_date(connection):
    """
    从 trading_dates 表中查询最大的 trade_date。

    Args:
        connection: 已建立的数据库连接对象。

    Returns:
        datetime.date: 最大的交易日期，如果表为空则返回 None。
    """
    with connection.cursor() as cursor:
        sql = "SELECT MAX(trade_date) FROM trading_dates"
        cursor.execute(sql)
        result = cursor.fetchone()
        max_date = result[0]  # result[0] 可能是 datetime.date 对象或 None

        if max_date:
            print(f"从数据库中查询到的最后交易日为: {max_date.strftime('%Y-%m-%d')}")
            return max_date
        else:
            print("数据库表 'trading_dates' 为空或未找到任何记录。")
            return None


def get_new_trading_dates_since(last_date_str, today_str):
    """
    获取从 last_date_str 的下一个交易日开始，到 today_str 为止的所有交易日。
    优化版：使用 a-trade-calendar 的 get_next_trade_date 快速迭代。

    Args:
        last_date_str (str): 最后一个已知交易日，格式 'YYYY-MM-DD'。
        today_str (str): 当前日期字符串，格式 'YYYY-MM-DD'。

    Returns:
        list: 新的交易日期字符串列表，格式 'YYYY-MM-DD'。
    """
    new_trading_dates = []

    # 获取 last_date_str 的下一个交易日
    next_trade_date_str = a_trade_calendar.get_next_trade_date(last_date_str, 1)

    # 循环获取后续交易日，直到超过今天
    current_date_str = next_trade_date_str
    while current_date_str <= today_str:
        new_trading_dates.append(current_date_str)
        try:
            # 获取再下一个交易日
            current_date_str = a_trade_calendar.get_next_trade_date(current_date_str, 1)
        except Exception as e:
            # 如果出现日期耗尽的提示，说明已达到内置日历的末尾
            if "更新" in str(e) or "upgrade" in str(e).lower():
                print(f"日历数据已到达尽头或需要更新: {e}")
                break  # 结束循环
            else:
                raise e  # 如果是其他错误，则重新抛出

    return new_trading_dates


def insert_new_dates(connection, new_dates):
    """
    将新的交易日期列表插入到 trading_dates 表中。

    Args:
        connection: 已建立的数据库连接对象。
        new_dates (list): 交易日期字符串列表。
    """
    with connection.cursor() as cursor:
        if not new_dates:
            print("没有新的交易日期需要插入。")
            return

        # 准备插入SQL语句 (使用 INSERT IGNORE 以防万一有重复数据)
        insert_sql = "INSERT IGNORE INTO trading_dates (trade_date) VALUES (%s)"

        # 将字符串日期转换为 datetime.date 对象以便 MySQL 正确接收
        date_objects = [datetime.strptime(d, '%Y-%m-%d').date() for d in new_dates]

        # 批量插入数据
        cursor.executemany(insert_sql, [(d,) for d in date_objects])

        connection.commit()
        print(f"成功插入 {cursor.rowcount} 条新的交易日期记录到数据库。")


def main():
    # 1. 配置数据库连接信息，请根据您的实际情况修改
    db_config = {
        'host': 'localhost',  # MySQL服务器地址
        'port': 3306,  # MySQL端口
        'user': 'py',  # 您的用户名
        'password': 'H123456',  # 您的密码
        'database': 'stock'  # 您的数据库名
    }

    # 2. 连接到数据库
    print("正在连接到 MySQL 数据库...")
    connection = connect_db(db_config)
    print("数据库连接成功。")

    try:
        # 3. 查询表中的最后交易日
        last_known_date_obj = get_last_trade_date(connection)

        if last_known_date_obj is None:
            # 如果表是空的，可以提示用户或退出
            print("表为空，无法继续增量更新。")
            return

        # 4. 获取当前日期
        today_obj = datetime.today().date()
        print(f"当前系统日期为: {today_obj}")

        # 将日期对象转换为字符串
        last_known_date_str = last_known_date_obj.strftime('%Y-%m-%d')
        today_str = today_obj.strftime('%Y-%m-%d')

        # 5. 检查是否需要更新
        if last_known_date_obj >= today_obj:
            print("数据库中的最后交易日已是最新的，无需更新。")
            return

        # 6. 获取新增的交易日
        print(f"正在查询从 {last_known_date_str} 之后到 {today_str} 之前的新增交易日...")
        new_trading_dates_list = get_new_trading_dates_since(last_known_date_str, today_str)
        print(f"共发现 {len(new_trading_dates_list)} 个新的交易日待插入。")

        # 7. 插入新的交易日
        insert_new_dates(connection, new_trading_dates_list)

    except Exception as e:
        print(f"程序执行过程中发生错误: {e}")
        connection.rollback()  # 发生错误时回滚事务
    finally:
        # 8. 关闭数据库连接
        connection.close()
        print("数据库连接已关闭。")

    print("\n程序执行完毕。")


if __name__ == "__main__":
    main()