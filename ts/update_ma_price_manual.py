import pymysql
from datetime import datetime
import time

# 思路：将需要计算均线的交易日期hardcode到程序中，每次运行前记得修改该日期。
#  调用存储过程，接受传入日期参数，计算该日的各种均线值。

def connect_to_db():
    """
    创建并返回一个数据库连接。
    请根据你的实际情况修改连接参数。
    """
    connection = pymysql.connect(
        host='localhost',  # 例如 'localhost' 或 IP 地址
        user='py',  # 你的数据库用户名
        password='H123456',  # 你的数据库密码
        database='stock',  # 你的数据库名
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor  # 返回字典格式的结果，方便访问
    )
    return connection


def main():
    """
    主函数：获取所有交易日，并逐个调用存储过程更新均线。
    """
    print(f"--- 股票均线批量更新开始 ---")
    start_time = datetime.now()

    try:
        # 连接到数据库
        connection = connect_to_db()

        with connection.cursor() as cursor:
            # 从 trading_dates 表中获取所有交易日
            # 注意：请根据你的 trading_dates 表的结构调整列名
            fetch_dates_sql = "SELECT DISTINCT trade_date FROM trading_dates where trade_date <= '2025-09-08' ORDER BY trade_date desc;"
            cursor.execute(fetch_dates_sql)
            date_results = cursor.fetchall()

            total_dates = len(date_results)
            print(f"共找到 {total_dates} 个交易日需要处理。")

            processed_count = 0
            for row in date_results:
                current_date = row['trade_date']  # 根据 fetch_dates_sql 中的列名获取

                print(f"正在处理日期: {current_date} ...", end="")

                try:
                    # 调用存储过程
                    # 注意：如果你的存储过程名不是 UpdateMovingAverages，请在这里修改
                    call_proc_sql = "CALL UpdateMovingAverages(%s);"
                    cursor.execute(call_proc_sql, (current_date,))

                    # 从存储过程中获取输出信息
                    # PyMySQL 处理存储过程的输出信息可能略有不同
                    # 一种简单的方式是在存储过程中不使用 OUT 参数，
                    # 而是在最后 SELECT 一行信息，这里我们已经在存储过程中这么做了

                    # 读取并打印存储过程的 SELECT 输出
                    result = cursor.fetchone()
                    if result and 'update_info' in result:
                        print(f" {result['update_info']}")
                    else:
                        # 如果没有返回预期的输出，也认为成功
                        print(" Success.")

                    processed_count += 1
                    # connection.commit() # 因为存储过程内部已提交，这里不需要
                    time.sleep(1)

                except Exception as e_proc:
                    print(f" 失败! 错误: {e_proc}")
                    # 可以选择在此处中断循环，或继续处理下一个日期
                    # raise e_proc # 如果希望失败时立即停止，取消注释此行

        connection.close()

    except Exception as e_main:
        print(f"\n脚本执行过程中发生错误: {e_main}")
        return

    end_time = datetime.now()
    duration = end_time - start_time

    print(f"--- 股票均线批量更新完成 ---")
    print(f"总处理日期数: {processed_count}/{total_dates}")
    print(f"开始时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"结束时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总耗时: {duration}")


if __name__ == "__main__":
    main()