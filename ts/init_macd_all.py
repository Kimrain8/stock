import pymysql
import pandas as pd
import numpy as np
import argparse
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger_sum = logging.getLogger('macd')
logger_sum.setLevel(logging.INFO)


class MACDCalculatorBatch:
    def __init__(self, host='localhost', port=3306, user='root', password='H123456', database='stock'):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.connection = None

        self.fast_period = 12
        self.slow_period = 26
        self.signal_period = 9

        self.fast_alpha = 2 / (self.fast_period + 1)
        self.slow_alpha = 2 / (self.slow_period + 1)
        self.signal_alpha = 2 / (self.signal_period + 1)

    def connect(self):
        try:
            self.connection = pymysql.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
            logger.info(f"成功连接到数据库: {self.database}")
            return True
        except Exception as e:
            logger.error(f"连接数据库失败: {e}")
            return False

    def disconnect(self):
        if self.connection and self.connection.open:
            self.connection.close()
            logger.info("数据库连接已关闭")

    def get_latest_macd(self, ts_code: str) -> Optional[dict]:
        try:
            with self.connection.cursor() as cursor:
                query = """
                SELECT trade_date, ema12, ema26, dea
                FROM stock_macd
                WHERE ts_code = %s
                ORDER BY trade_date DESC
                LIMIT 1
                """
                cursor.execute(query, (ts_code,))
                result = cursor.fetchone()
                return result
        except Exception as e:
            logger.error(f"查询最新MACD数据失败: {e}")
            return None

    def get_daily_data_batch(self, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            with self.connection.cursor() as cursor:
                query = """
                SELECT trade_date, close_price
                FROM ts_stock_daily_his
                WHERE ts_code = %s
                AND trade_date BETWEEN %s AND %s
                ORDER BY trade_date ASC
                """
                cursor.execute(query, (ts_code, start_date, end_date))
                results = cursor.fetchall()

                if not results:
                    return pd.DataFrame()

                df = pd.DataFrame(results)
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
                df['close_price'] = pd.to_numeric(df['close_price'], errors='coerce')
                df = df.dropna(subset=['close_price'])

                return df
        except Exception as e:
            logger.error(f"查询日线数据失败: {e}")
            return pd.DataFrame()

    def get_trade_date_range(self, ts_code: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            with self.connection.cursor() as cursor:
                query = """
                SELECT MIN(trade_date) as min_date, MAX(trade_date) as max_date
                FROM ts_stock_daily_his
                WHERE ts_code = %s
                """
                cursor.execute(query, (ts_code,))
                result = cursor.fetchone()

                if result:
                    return result['min_date'], result['max_date']
                return None, None
        except Exception as e:
            logger.error(f"查询交易日期范围失败: {e}")
            return None, None

    def get_all_stock_codes(self) -> List[str]:
        try:
            with self.connection.cursor() as cursor:
                query = """
                SELECT ts_code
                FROM ts_stock_basic
                ORDER BY ts_code
                """
                cursor.execute(query)
                results = cursor.fetchall()
                return [row['ts_code'] for row in results]
        except Exception as e:
            logger.error(f"查询股票代码列表失败: {e}")
            return []

    def is_stock_calculated(self, ts_code: str) -> bool:
        try:
            with self.connection.cursor() as cursor:
                query = """
                SELECT COUNT(*) as count
                FROM stock_macd
                WHERE ts_code = %s
                """
                cursor.execute(query, (ts_code,))
                result = cursor.fetchone()
                return result['count'] > 0
        except Exception as e:
            logger.error(f"检查股票MACD计算状态失败: {e}")
            return False

    def calculate_ema(self, prices: np.ndarray, alpha: float, prev_ema: Optional[float] = None) -> np.ndarray:
        if len(prices) == 0:
            return np.array([])

        ema_values = []

        if prev_ema is not None:
            current_ema = prev_ema
            for price in prices:
                current_ema = price * alpha + current_ema * (1 - alpha)
                ema_values.append(current_ema)
        else:
            sma = np.mean(prices[:self.fast_period])
            ema_values.append(sma)
            prices = prices[self.fast_period:]

            for price in prices:
                ema = price * alpha + ema_values[-1] * (1 - alpha)
                ema_values.append(ema)

        return np.array(ema_values)

    def calculate_macd_incremental(self, data_df: pd.DataFrame, prev_ema12: Optional[float] = None,
                                   prev_ema26: Optional[float] = None,
                                   prev_dea: Optional[float] = None) -> pd.DataFrame:
        if data_df.empty:
            return pd.DataFrame()

        close_prices = data_df['close_price'].values
        trade_dates = data_df['trade_date'].values

        ema12_values = self.calculate_ema(close_prices, self.fast_alpha, prev_ema12)
        ema26_values = self.calculate_ema(close_prices, self.slow_alpha, prev_ema26)

        min_length = min(len(ema12_values), len(ema26_values), len(close_prices))
        ema12_values = ema12_values[:min_length]
        ema26_values = ema26_values[:min_length]
        close_prices = close_prices[:min_length]
        trade_dates = trade_dates[:min_length]

        dif_values = ema12_values - ema26_values

        dea_values = self.calculate_ema(dif_values, self.signal_alpha, prev_dea)

        min_length = min(len(dif_values), len(dea_values))
        dif_values = dif_values[:min_length]
        dea_values = dea_values[:min_length]
        trade_dates = trade_dates[:min_length]
        ema12_values = ema12_values[:min_length]
        ema26_values = ema26_values[:min_length]

        macd_values = (dif_values - dea_values) * 2

        result_df = pd.DataFrame({
            'trade_date': trade_dates,
            'ema12': ema12_values,
            'ema26': ema26_values,
            'dif': dif_values,
            'dea': dea_values,
            'macd': macd_values
        })

        return result_df

    def save_macd_data(self, ts_code: str, macd_df: pd.DataFrame) -> bool:
        try:
            with self.connection.cursor() as cursor:
                insert_query = """
                INSERT INTO stock_macd (ts_code, trade_date, ema12, ema26, dif, dea, macd)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                ema12 = VALUES(ema12),
                ema26 = VALUES(ema26),
                dif = VALUES(dif),
                dea = VALUES(dea),
                macd = VALUES(macd)
                """

                data_list = []
                for _, row in macd_df.iterrows():
                    data_list.append((
                        ts_code,
                        row['trade_date'],
                        float(row['ema12']),
                        float(row['ema26']),
                        float(row['dif']),
                        float(row['dea']),
                        float(row['macd'])
                    ))

                cursor.executemany(insert_query, data_list)
                self.connection.commit()

                logger.info(f"成功保存 {len(data_list)} 条MACD数据")
                return True
        except Exception as e:
            logger.error(f"保存MACD数据失败: {e}")
            self.connection.rollback()
            return False

    def calculate_macd_by_batch(self, ts_code: str, start_date: Optional[str] = None,
                                end_date: Optional[str] = None, batch_days: int = 500) -> bool:
        logger.info(f"开始计算股票 {ts_code} 的MACD指标...")

        prev_macd_data = self.get_latest_macd(ts_code)

        if prev_macd_data:
            last_date = prev_macd_data['trade_date']
            prev_ema12 = float(prev_macd_data['ema12'])
            prev_ema26 = float(prev_macd_data['ema26'])
            prev_dea = float(prev_macd_data['dea'])

            logger.info(f"发现历史MACD数据，最新日期: {last_date}")
            logger.info(f"历史EMA值: EMA12={prev_ema12:.4f}, EMA26={prev_ema26:.4f}, DEA={prev_dea:.4f}")

            if isinstance(last_date, str):
                calc_start_date = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            else:
                calc_start_date = (datetime.combine(last_date, datetime.min.time()) + timedelta(days=1)).strftime(
                    '%Y-%m-%d')
        else:
            logger.info("未发现历史MACD数据，将进行全量计算")
            prev_ema12 = None
            prev_ema26 = None
            prev_dea = None

            if start_date is None:
                min_date, max_date = self.get_trade_date_range(ts_code)
                if min_date is None:
                    logger.error(f"未找到股票 {ts_code} 的日线数据")
                    return False
                calc_start_date = min_date
            else:
                calc_start_date = start_date

        if end_date is None:
            _, max_date = self.get_trade_date_range(ts_code)
            if max_date is None:
                logger.error(f"未找到股票 {ts_code} 的日线数据")
                return False
            calc_end_date = max_date
        else:
            calc_end_date = end_date

        logger.info(f"计算日期范围: {calc_start_date} 至 {calc_end_date}")

        if isinstance(calc_start_date, str):
            current_start = datetime.strptime(calc_start_date, '%Y-%m-%d')
        else:
            current_start = datetime.combine(calc_start_date, datetime.min.time())

        if isinstance(calc_end_date, str):
            calc_end = datetime.strptime(calc_end_date, '%Y-%m-%d')
        else:
            calc_end = datetime.combine(calc_end_date, datetime.min.time())

        total_saved = 0

        while current_start <= calc_end:
            current_end = min(current_start + timedelta(days=batch_days - 1), calc_end)

            batch_start_str = current_start.strftime('%Y-%m-%d')
            batch_end_str = current_end.strftime('%Y-%m-%d')

            logger.info(f"处理批次: {batch_start_str} 至 {batch_end_str}")

            data_df = self.get_daily_data_batch(ts_code, batch_start_str, batch_end_str)

            if data_df.empty:
                logger.warning(f"批次 {batch_start_str} 至 {batch_end_str} 无数据")
                current_start = current_end + timedelta(days=1)
                continue

            macd_df = self.calculate_macd_incremental(data_df, prev_ema12, prev_ema26, prev_dea)

            if macd_df.empty:
                logger.warning(f"批次 {batch_start_str} 至 {batch_end_str} MACD计算失败")
                current_start = current_end + timedelta(days=1)
                continue

            if self.save_macd_data(ts_code, macd_df):
                total_saved += len(macd_df)

                last_row = macd_df.iloc[-1]
                prev_ema12 = last_row['ema12']
                prev_ema26 = last_row['ema26']
                prev_dea = last_row['dea']

            current_start = current_end + timedelta(days=1)

        logger.info(f"股票 {ts_code} MACD计算完成，共保存 {total_saved} 条数据")
        return True

    def calculate_multiple_stocks(self, ts_codes: List[str], start_date: Optional[str] = None,
                                  end_date: Optional[str] = None, batch_days: int = 500) -> Tuple[int, int]:
        success_count = 0
        fail_count = 0

        for i, ts_code in enumerate(ts_codes):
            logger.info(f"\n{'=' * 60}")
            logger.info(f"处理第 {i + 1}/{len(ts_codes)} 只股票: {ts_code}")
            logger.info(f"{'=' * 60}")

            if self.calculate_macd_by_batch(ts_code, start_date, end_date, batch_days):
                success_count += 1
            else:
                fail_count += 1

        logger.info(f"\n{'=' * 60}")
        logger.info(f"批量处理完成: 成功 {success_count} 只, 失败 {fail_count} 只")
        logger.info(f"{'=' * 60}")

        return success_count, fail_count


def main():
    parser = argparse.ArgumentParser(description='股票日线MACD分批次计算程序')

    parser.add_argument('--ts-code', '-c', type=str, help='股票代码（如：600000.SH）')
    parser.add_argument('--all', '-a', action='store_false', help='计算所有股票的MACD（从ts_stock_basic表获取）')
    parser.add_argument('--start-date', '-s', type=str, help='开始日期（格式：YYYY-MM-DD）')
    parser.add_argument('--end-date', '-e', type=str, help='结束日期（格式：YYYY-MM-DD）')
    parser.add_argument('--batch-days', '-b', type=int, default=500, help='每批次处理天数（默认：500）')
    parser.add_argument('--host', type=str, default='localhost', help='数据库主机（默认：localhost）')
    parser.add_argument('--port', type=int, default=3306, help='数据库端口（默认：3306）')
    parser.add_argument('--user', type=str, default='root', help='数据库用户（默认：root）')
    parser.add_argument('--password', '-p', type=str, default='H123456', help='数据库密码（默认：H123456）')
    parser.add_argument('--database', '-d', type=str, default='stock', help='数据库名（默认：stock）')

    args = parser.parse_args()

    calculator = MACDCalculatorBatch(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database
    )

    if not calculator.connect():
        logger.error("数据库连接失败，程序退出")
        return

    try:
        if args.all:
            stock_codes = calculator.get_all_stock_codes()

            if not stock_codes:
                logger.error("未从ts_stock_basic表获取到股票代码")
                return

            logger.info(f"从ts_stock_basic表获取到 {len(stock_codes)} 只股票代码")

            success_count = 0
            fail_count = 0
            skip_count = 0

            for i, ts_code in enumerate(stock_codes):
                logger.info(f"\n{'=' * 60}")
                logger.info(f"处理第 {i + 1}/{len(stock_codes)} 只股票: {ts_code}")
                logger.info(f"{'=' * 60}")

                if calculator.calculate_macd_by_batch(ts_code, args.start_date, args.end_date, args.batch_days):
                    success_count += 1
                else:
                    fail_count += 1

            logger_sum.info(f"\n{'=' * 60}")
            logger_sum.info(f"批量处理完成:")
            logger_sum.info(f"  总计: {len(stock_codes)} 只")
            logger_sum.info(f"  成功: {success_count} 只")
            logger_sum.info(f"  失败: {fail_count} 只")
            logger_sum.info(f"  跳过: {skip_count} 只")
            logger_sum.info(f"{'=' * 60}")

        elif args.ts_code:
            calculator.calculate_macd_by_batch(
                args.ts_code,
                args.start_date,
                args.end_date,
                args.batch_days
            )
        else:
            logger.error("请指定股票代码（--ts-code）或使用--all参数计算所有股票")
            parser.print_help()
    finally:
        calculator.disconnect()


if __name__ == "__main__":
    main()
