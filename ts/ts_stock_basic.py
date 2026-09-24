import tushare as ts
import pandas as pd
import pymysql

## 实际没完全使用(因为这个api每天有使用次数限制，不好调试)，仅使用该程序将股票基本数据另存为csv的功能，然后用数据库工具从csv导入到mysql数据库。

def fetch_stock_basics(token):
    """
    从 Tushare 获取沪深 A 股上市公司基本情况。

    参数:
        token (str): 你的 Tushare API Token。

    返回:
        pandas.DataFrame: 包含股票代码、名称等信息的数据框。
    """
    # 设置 Tushare 的 token
    ts.set_token(token)

    # 初始化 pro API 接口
    pro = ts.pro_api()

    try:
        # 调用接口获取数据
        # 'ts_code': 股票代码, 'name': 股票名称, 'area': 所在地域, 'industry': 所属行业, 'market': 市场类型
        df = pro.stock_basic(exchange='', list_status='L', fields='ts_code,symbol,name,area,industry,fullname,cnspell,market, list_status, list_date, is_hs, act_name, act_ent_type')

        print(f"成功获取到 {len(df)} 条股票基础信息。")
        return df

    except Exception as e:
        print(f"获取数据时发生错误: {e}")
        return None

def upsert_stocks_to_db(connection, df):
    """
    将股票数据 upsert (INSERT ... ON DUPLICATE KEY UPDATE) 到数据库
    """
    cursor = connection.cursor()

    upsert_sql = """
    INSERT INTO ts_stock_basic (ts_code,symbol,name,area,
            industry,fullname, cnspell, market, 
            list_status, list_date, delist_date, is_hs, 
            act_name, act_ent_type 
        ) 
    VALUES (%s, %s,%s, %s,%s, %s,%s, %s,%s, %s,%s, %s,%s, %s) 
    ON DUPLICATE KEY UPDATE 
        symbol = VALUES(symbol),
        name = VALUES(name), 
        area = VALUES(area),
        industry = VALUES(industry), 
        fullname = VALUES(fullname),
        cnspell = VALUES(cnspell), 
        market = VALUES(market), 
        list_status = VALUES(list_status), 
        list_date= VALUES(list_date),        
        is_hs = VALUES(is_hs), 
        act_name = VALUES(act_name), 
        act_ent_type = VALUES(act_ent_type)         
        ;
    """

    records = df[['ts_code','symbol','name','area','industry','fullname','cnspell','market', 'list_status', 'list_date', 'is_hs', 'act_name', 'act_ent_type']].to_records(index=False).tolist()

    try:
        cursor.executemany(upsert_sql, records)
        connection.commit()
        print(f"成功 upsert {cursor.rowcount} 条记录到数据库。")
    except Exception as e:
        print(f"Upsert 数据失败: {e}")
        connection.rollback()
    finally:
        cursor.close()


def connect_to_mysql():
    """
    连接到 MySQL 数据库
    Returns:
        connection: PyMySQL Connection 对象
    """
    # --- 请在此处修改为你的数据库连接信息 ---
    config = {
        'host': 'localhost',  # 例如: '192.168.1.100'
        'port': 3306,
        'user': 'py',  # 你的MySQL用户名
        'password': 'H123456',  # 你的MySQL密码
        'db': 'stock',  # 你要操作的数据库名
        'charset': 'utf8mb4'  # 推荐使用 utf8mb4 支持emoji等
    }
    try:
        connection = pymysql.connect(**config)
        print("成功连接到 MySQL 数据库。")
        return connection
    except Exception as e:
        print(f"连接 MySQL 数据库失败: {e}")
        return None

def main():
    """
    主函数，用于执行获取和保存操作。
    """
    # 请在此处填入你自己的 Tushare Token
    TOKEN = '换成你的token'

    # 获取股票基础信息
    stock_data = fetch_stock_basics(TOKEN)

    if stock_data is not None:
        # 可以选择打印前几行预览数据
        print("\n--- 数据预览 ---")
        print(stock_data.head())

        # 将数据保存为 CSV 文件
        filename = 'stock_basics.csv'
        stock_data.to_csv(filename, index=False, encoding='utf-8-sig')  # 使用 utf-8-sig 编码以确保中文能被 Excel 正确识别
        print(f"\n数据已成功保存到文件: {filename}")

    if stock_data is not None:
        db_connection = connect_to_mysql()
        if not db_connection:
            print("无法建立数据库连接，程序退出。")
            return
        try:
            upsert_stocks_to_db(db_connection, stock_data)
            print("\n股票列表更新完成！")
        finally:
            # 5. 关闭数据库连接
            db_connection.close()
            print("已关闭数据库连接。")

if __name__ == "__main__":
    main()

