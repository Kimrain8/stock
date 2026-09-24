CREATE DATABASE `stock` /*!40100 DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci */ /*!80016 DEFAULT ENCRYPTION='N' */;

use stock;

CREATE TABLE `stock_macd` (
  `ts_code` varchar(10) NOT NULL COMMENT '股票代码',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `ema12` decimal(12,4) DEFAULT NULL COMMENT '12日指数移动平均',
  `ema26` decimal(12,4) DEFAULT NULL COMMENT '26日指数移动平均',
  `dif` decimal(12,4) DEFAULT NULL COMMENT 'DIF值(快线)',
  `dea` decimal(12,4) DEFAULT NULL COMMENT 'DEA值(慢线)',
  `macd` decimal(12,4) DEFAULT NULL COMMENT 'MACD柱状图',
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_ts_code` (`ts_code`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='MACD指标表'
/*!50500 PARTITION BY RANGE  COLUMNS(trade_date)
(PARTITION p2024 VALUES LESS THAN ('2025-01-01') ENGINE = InnoDB,
 PARTITION p2025 VALUES LESS THAN ('2026-01-01') ENGINE = InnoDB,
 PARTITION p2026 VALUES LESS THAN ('2027-01-01') ENGINE = InnoDB,
 PARTITION p2027 VALUES LESS THAN ('2028-01-01') ENGINE = InnoDB,
 PARTITION p2028 VALUES LESS THAN ('2029-01-01') ENGINE = InnoDB,
 PARTITION p2029 VALUES LESS THAN ('2030-01-01') ENGINE = InnoDB,
 PARTITION p_future VALUES LESS THAN (MAXVALUE) ENGINE = InnoDB) */;

CREATE TABLE `trading_dates` (
  `id` int NOT NULL AUTO_INCREMENT,
  `trade_date` date NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `trade_date` (`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB AUTO_INCREMENT=5149 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `ts_op_log_date` (
  `op_date` date NOT NULL COMMENT '已更新收盘价均线的日期',
  `op_type` varchar(20) NOT NULL COMMENT '操作日志类型',
  `create_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  PRIMARY KEY (`op_date`,`op_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='日志记录表';

CREATE TABLE `ts_stock_basic` (
  `ts_code` varchar(20) NOT NULL COMMENT 'tushare 股票代码, 比如：600000.SH',
  `symbol` varchar(10) NOT NULL COMMENT '股票代码',
  `name` varchar(50) NOT NULL COMMENT '股票名称',
  `area` varchar(50) DEFAULT NULL COMMENT '地域',
  `industry` varchar(50) DEFAULT NULL COMMENT '所属行业',
  `fullname` varchar(200) DEFAULT NULL COMMENT '股票全称',
  `cnspell` varchar(20) DEFAULT NULL COMMENT '拼音缩写',
  `market` varchar(20) DEFAULT NULL COMMENT '市场类型：主板/创业板/科创板/CDR',
  `list_status` char(1) DEFAULT NULL COMMENT '上市状态 L上市 D退市 G过会未交易 P暂停上市',
  `list_date` date DEFAULT NULL COMMENT '上市日期',
  `is_hs` char(1) DEFAULT NULL COMMENT '是否沪深港通标的',
  `act_name` varchar(200) DEFAULT NULL COMMENT '实控人名称',
  `act_ent_type` varchar(100) DEFAULT NULL COMMENT '实控人企业性质',
  `update_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '数据爬取时间',
  PRIMARY KEY (`ts_code`),
  KEY `idx_ts_stock_symbol` (`symbol`),
  KEY `idx_ts_stock_cnspell` (`cnspell`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='tushare股票基本信息表';

CREATE TABLE `ts_stock_daily_his` (
  `ts_code` varchar(20) NOT NULL COMMENT '股票代码',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `open_price` decimal(10,2) DEFAULT NULL COMMENT '开盘价',
  `high` decimal(10,2) DEFAULT NULL COMMENT '最高价',
  `low` decimal(10,2) DEFAULT NULL COMMENT '最低价',
  `close_price` decimal(10,2) DEFAULT NULL COMMENT '收盘价',
  `pre_close` decimal(10,2) DEFAULT NULL COMMENT '昨收价',
  `change_amount` decimal(10,2) DEFAULT NULL COMMENT '涨跌额',
  `pct_chg` decimal(10,4) DEFAULT NULL COMMENT '涨跌幅',
  `vol` decimal(20,4) DEFAULT NULL COMMENT '成交量',
  `amount` decimal(20,4) DEFAULT NULL COMMENT '成交额',
  `ma5` decimal(20,4) DEFAULT NULL COMMENT '5日均线',
  `ma10` decimal(20,4) DEFAULT NULL COMMENT '10日均线',
  `ma20` decimal(20,4) DEFAULT NULL COMMENT '20日均线',
  `ma30` decimal(20,4) DEFAULT NULL COMMENT '30日均线',
  `ma60` decimal(20,4) DEFAULT NULL COMMENT '60日均线',
  `ma120` decimal(20,4) DEFAULT NULL COMMENT '120日均线',
  `ma150` decimal(20,4) DEFAULT NULL COMMENT '150日均线',
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日线历史数据表'
/*!50100 PARTITION BY RANGE (to_days(`trade_date`))
(PARTITION p2000 VALUES LESS THAN (730851) ENGINE = InnoDB,
 PARTITION p2009 VALUES LESS THAN (734138) ENGINE = InnoDB,
 PARTITION p2011 VALUES LESS THAN (734868) ENGINE = InnoDB,
 PARTITION p2013 VALUES LESS THAN (735599) ENGINE = InnoDB,
 PARTITION p2015 VALUES LESS THAN (736329) ENGINE = InnoDB,
 PARTITION p2017 VALUES LESS THAN (737060) ENGINE = InnoDB,
 PARTITION p2019 VALUES LESS THAN (737790) ENGINE = InnoDB,
 PARTITION p2020 VALUES LESS THAN (738156) ENGINE = InnoDB,
 PARTITION p2021 VALUES LESS THAN (738521) ENGINE = InnoDB,
 PARTITION p2022 VALUES LESS THAN (738886) ENGINE = InnoDB,
 PARTITION p2023 VALUES LESS THAN (739251) ENGINE = InnoDB,
 PARTITION p2024 VALUES LESS THAN (739617) ENGINE = InnoDB,
 PARTITION p2025 VALUES LESS THAN (739982) ENGINE = InnoDB,
 PARTITION p2026 VALUES LESS THAN (740347) ENGINE = InnoDB,
 PARTITION p2027 VALUES LESS THAN (740712) ENGINE = InnoDB,
 PARTITION p2028 VALUES LESS THAN (741078) ENGINE = InnoDB,
 PARTITION p2029 VALUES LESS THAN (741443) ENGINE = InnoDB,
 PARTITION pmax VALUES LESS THAN MAXVALUE ENGINE = InnoDB) */;

CREATE TABLE `ts_stock_daily_vol` (
  `ts_code` varchar(20) NOT NULL COMMENT '股票代码',
  `trade_date` date NOT NULL COMMENT '交易日期',
  `vol` decimal(20,4) DEFAULT NULL COMMENT '成交量',
  `ma5` decimal(20,4) DEFAULT NULL COMMENT '5日均线',
  `ma10` decimal(20,4) DEFAULT NULL COMMENT '10日均线',
  `ma20` decimal(20,4) DEFAULT NULL COMMENT '20日均线',
  `ma30` decimal(20,4) DEFAULT NULL COMMENT '30日均线',
  `ma60` decimal(20,4) DEFAULT NULL COMMENT '60日均线',
  `ma120` decimal(20,4) DEFAULT NULL COMMENT '120日均线',
  `ma150` decimal(20,4) DEFAULT NULL COMMENT '150日均线',
  PRIMARY KEY (`ts_code`,`trade_date`),
  KEY `idx_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='股票日线历史数据表'
/*!50100 PARTITION BY RANGE (to_days(`trade_date`))
(PARTITION p2000 VALUES LESS THAN (730851) ENGINE = InnoDB,
 PARTITION p2009 VALUES LESS THAN (734138) ENGINE = InnoDB,
 PARTITION p2011 VALUES LESS THAN (734868) ENGINE = InnoDB,
 PARTITION p2013 VALUES LESS THAN (735599) ENGINE = InnoDB,
 PARTITION p2015 VALUES LESS THAN (736329) ENGINE = InnoDB,
 PARTITION p2017 VALUES LESS THAN (737060) ENGINE = InnoDB,
 PARTITION p2019 VALUES LESS THAN (737790) ENGINE = InnoDB,
 PARTITION p2020 VALUES LESS THAN (738156) ENGINE = InnoDB,
 PARTITION p2021 VALUES LESS THAN (738521) ENGINE = InnoDB,
 PARTITION p2022 VALUES LESS THAN (738886) ENGINE = InnoDB,
 PARTITION p2023 VALUES LESS THAN (739251) ENGINE = InnoDB,
 PARTITION p2024 VALUES LESS THAN (739617) ENGINE = InnoDB,
 PARTITION p2025 VALUES LESS THAN (739982) ENGINE = InnoDB,
 PARTITION p2026 VALUES LESS THAN (740347) ENGINE = InnoDB,
 PARTITION p2027 VALUES LESS THAN (740712) ENGINE = InnoDB,
 PARTITION p2028 VALUES LESS THAN (741078) ENGINE = InnoDB,
 PARTITION p2029 VALUES LESS THAN (741443) ENGINE = InnoDB,
 PARTITION pmax VALUES LESS THAN MAXVALUE ENGINE = InnoDB) */;
 
 
 DELIMITER $$
CREATE DEFINER=`root`@`localhost` PROCEDURE `UpdateMA_VOL`(IN input_trade_date DATE)
BEGIN
    -- 定义一个变量，用于记录受影响的行数
    DECLARE affected_rows INT DEFAULT 0;

    -- 开始一个事务以保证数据一致性
    START TRANSACTION;

    -- 更新指定日期的均线值
    -- 使用子查询计算均线，并通过 UPDATE JOIN 将结果更新回原表
    UPDATE ts_stock_daily_vol AS target_table
    JOIN (
        SELECT 
            ts_code,
            trade_date,
            -- 计算5日、10日、20日、30日、60日、120日、150日移动平均线
            -- 使用 RANGE BETWEEN INTERVAL X DAY PRECEDING AND CURRENT ROW
            -- 这种方式能正确处理非交易日（如周末、节假日）
            AVG(vol) OVER (
               PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_5,
            AVG(vol) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_10,
            AVG(vol) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_20,
            AVG(vol) OVER (
               PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_30,
            AVG(vol) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_60,
            AVG(vol) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_120,
            AVG(vol) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 149 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_150
        FROM ts_stock_daily_vol
        -- 为了提高效率，只处理截止到目标日期的数据
        WHERE trade_date <= input_trade_date
          -- 可选优化：进一步限制数据范围，只处理最近可能影响均线的日期
			AND trade_date >= DATE_SUB(input_trade_date, INTERVAL 300 DAY)
    ) AS calculated_values
    ON target_table.ts_code = calculated_values.ts_code 
       AND target_table.trade_date = calculated_values.trade_date
    SET 
        target_table.ma5 = ROUND(calculated_values.calculated_ma_5, 2),
        target_table.ma10 = ROUND(calculated_values.calculated_ma_10, 2),
        target_table.ma20 = ROUND(calculated_values.calculated_ma_20, 2),
        target_table.ma30 = ROUND(calculated_values.calculated_ma_30, 2),
        target_table.ma60 = ROUND(calculated_values.calculated_ma_60, 2),
        target_table.ma120 = ROUND(calculated_values.calculated_ma_120, 2),
        target_table.ma150 = ROUND(calculated_values.calculated_ma_150, 2)
    WHERE target_table.trade_date = input_trade_date;

    -- 获取本次更新影响的行数
    SET affected_rows = ROW_COUNT();
    -- 提交事务
    COMMIT;

    -- 打印信息（可选，用于调试或监控）
    SELECT CONCAT('Successfully updated ', affected_rows, ' records for date: ', input_trade_date) AS update_info;

END$$
DELIMITER ;

DELIMITER $$
CREATE DEFINER=`root`@`localhost` PROCEDURE `UpdateMovingAverageForDate`(
    IN in_ts_code VARCHAR(10),
    IN in_target_date DATE
)
BEGIN
   
    -- 执行更新
    UPDATE ts_stock_daily_his_test1 t1
    JOIN (
        SELECT 
            ts_code,
            trade_date,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            ) AS calculated_ma5,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
            ) AS calculated_ma10,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS calculated_ma20
        FROM ts_stock_daily_his_test1
        WHERE ts_code = in_ts_code
          AND trade_date <= in_target_date
    ) t2
    ON t1.ts_code = t2.ts_code AND t1.trade_date = t2.trade_date
    SET 
        t1.ma5 = t2.calculated_ma5,
        t1.ma10 = t2.calculated_ma10,
        t1.ma20 = t2.calculated_ma20
    WHERE t1.ts_code = in_ts_code AND t1.trade_date = in_target_date;
    
    SELECT CONCAT('股票 ', in_ts_code, ' 在日期 ', in_target_date, ' 的均线更新完成') AS Result;

END$$
DELIMITER ;

DELIMITER $$
CREATE DEFINER=`root`@`localhost` PROCEDURE `UpdateMovingAverages`(IN input_trade_date DATE)
BEGIN
    -- 定义一个变量，用于记录受影响的行数
    DECLARE affected_rows INT DEFAULT 0;

    -- 开始一个事务以保证数据一致性
    START TRANSACTION;

    -- 更新指定日期的均线值
    -- 使用子查询计算均线，并通过 UPDATE JOIN 将结果更新回原表
    UPDATE ts_stock_daily_his AS target_table
    JOIN (
        SELECT 
            ts_code,
            trade_date,
            -- 计算5日、10日、20日、30日、60日、120日、150日移动平均线
            -- 使用 RANGE BETWEEN INTERVAL X DAY PRECEDING AND CURRENT ROW
            -- 这种方式能正确处理非交易日（如周末、节假日）
            AVG(close_price) OVER (
               PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_5,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_10,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_20,
            AVG(close_price) OVER (
               PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_30,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_60,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_120,
            AVG(close_price) OVER (
                PARTITION BY ts_code 
                ORDER BY trade_date 
                ROWS BETWEEN 149 PRECEDING AND CURRENT ROW
            ) AS calculated_ma_150
        FROM ts_stock_daily_his
        -- 为了提高效率，只处理截止到目标日期的数据
        WHERE trade_date <= input_trade_date
          -- 可选优化：进一步限制数据范围，只处理最近可能影响均线的日期
			AND trade_date >= DATE_SUB(input_trade_date, INTERVAL 300 DAY)
    ) AS calculated_values
    ON target_table.ts_code = calculated_values.ts_code 
       AND target_table.trade_date = calculated_values.trade_date
    SET 
        target_table.ma5 = ROUND(calculated_values.calculated_ma_5, 2),
        target_table.ma10 = ROUND(calculated_values.calculated_ma_10, 2),
        target_table.ma20 = ROUND(calculated_values.calculated_ma_20, 2),
        target_table.ma30 = ROUND(calculated_values.calculated_ma_30, 2),
        target_table.ma60 = ROUND(calculated_values.calculated_ma_60, 2),
        target_table.ma120 = ROUND(calculated_values.calculated_ma_120, 2),
        target_table.ma150 = ROUND(calculated_values.calculated_ma_150, 2)
    WHERE target_table.trade_date = input_trade_date;

    -- 获取本次更新影响的行数
    SET affected_rows = ROW_COUNT();
    -- 提交事务
    COMMIT;

    -- 打印信息（可选，用于调试或监控）
    SELECT CONCAT('Successfully updated ', affected_rows, ' records for date: ', input_trade_date) AS update_info;
    -- SELECT affected_rows as affected_rows;

END$$
DELIMITER ;

