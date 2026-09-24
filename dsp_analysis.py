import os
import time
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv
import logging

# 加载环境变量
# load_dotenv()
DEEPSEEK_API_KEY = os.getenv("dspkey")
if not DEEPSEEK_API_KEY:
    raise ValueError("请设置环境变量 dspkey 为你的 DeepSeek API Key")

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ============================================================================
# 1. DeepSeek AI 调用封装
# ============================================================================
def call_deepseek(prompt: str, system: str = "你是一位资深金融分析师。", max_tokens=500) -> str:
    """
    调用 DeepSeek API 进行自然语言分析
    """
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens
    }
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"DeepSeek API 调用失败: {e}")
        return ""

# ============================================================================
# 2. 数据采集模块 (使用 akshare)
# ============================================================================
import akshare as ak

class DataFetcher:
    """实时数据获取"""
    
    @staticmethod
    def get_stock_quote(symbol: str) -> Dict:
        """
        获取个股实时行情 (返回: 最新价, 涨跌幅, 量比, 换手率, 主力净流入等)
        symbol: 股票代码，如 '000001' 或 '600519'
        """
        try:
            # 使用 akshare 的实时行情接口
            df = ak.stock_zh_a_spot_em()  # 东方财富实时行情
            row = df[df['代码'] == symbol]
            if row.empty:
                # 尝试转为六位代码
                symbol = symbol.zfill(6)
                row = df[df['代码'] == symbol]
            if row.empty:
                return {}
            data = row.iloc[0]
            return {
                "code": symbol,
                "name": data.get("名称", ""),
                "price": float(data.get("最新价", 0)),
                "change_pct": float(data.get("涨跌幅", 0)),
                "volume": float(data.get("成交量", 0)),
                "turnover_rate": float(data.get("换手率", 0)) if data.get("换手率") else 0,
                "pe": float(data.get("市盈率-动态", 0)) if data.get("市盈率-动态") else 0,
                "pb": float(data.get("市净率", 0)) if data.get("市净率") else 0,
                "amount": float(data.get("成交额", 0)),
                "high": float(data.get("最高", 0)),
                "low": float(data.get("最低", 0)),
            }
        except Exception as e:
            logger.error(f"获取 {symbol} 行情失败: {e}")
            return {}

    @staticmethod
    def get_sector_flow() -> pd.DataFrame:
        """获取行业板块资金流向 (主力净流入、涨跌幅等)"""
        try:
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流向")
            return df
        except Exception as e:
            logger.error(f"获取板块资金流向失败: {e}")
            return pd.DataFrame()

    @staticmethod
    def get_news_for_stock(symbol: str, limit: int = 10) -> List[str]:
        """获取个股相关新闻 (简化版，使用akshare新闻接口)"""
        try:
            # 尝试获取新闻
            df = ak.stock_news_em(symbol=symbol)  # 需要东方财富新闻接口
            if not df.empty:
                titles = df['标题'].tolist()[:limit]
                return titles
            return []
        except:
            # 备用：使用简单搜索
            return []

    @staticmethod
    def get_concept_stocks(concept: str) -> List[str]:
        """获取某个概念板块的成分股代码"""
        try:
            df = ak.stock_board_concept_name_em()
            # 查找概念ID
            row = df[df['板块名称'] == concept]
            if row.empty:
                return []
            concept_id = row.iloc[0]['板块代码']
            stocks = ak.stock_board_concept_cons_em(symbol=concept_id)
            return stocks['代码'].tolist()
        except:
            return []

# ============================================================================
# 3. 行业与标的筛选引擎
# ============================================================================
class StockScreener:
    """基于行业、机构、概念筛选股票池"""
    
    # 国家重点扶持行业（一级分类）
    KEY_INDUSTRIES = [
        "半导体", "新能源", "人工智能", "生物医药", "高端装备",
        "新材料", "数字经济", "国防军工"
    ]
    # 重要民生行业
    ESSENTIAL_INDUSTRIES = [
        "食品饮料", "农林牧渔", "医药商业", "公用事业", "交通运输"
    ]
    
    # 已知华为投资 / 重点机构持股概念（可通过调整）
    HUAWEI_CONCEPT = "华为概念"
    INSTITUTION_CONCEPT = "机构重仓"
    
    @classmethod
    def get_target_stocks(cls) -> List[str]:
        """
        返回待监控的股票代码列表
        策略：从关键行业中取龙头，并加入华为/机构概念股
        """
        target_codes = set()
        fetcher = DataFetcher()
        
        # 1. 从关键行业获取龙头（通过行业资金流向排序）
        sector_flow = fetcher.get_sector_flow()
        if not sector_flow.empty:
            # 筛选出包含关键行业的板块，取资金流入前5的行业
            sector_flow['行业'] = sector_flow['名称']
            # 简化：取所有行业名称中有关键词
            for ind in cls.KEY_INDUSTRIES + cls.ESSENTIAL_INDUSTRIES:
                matched = sector_flow[sector_flow['名称'].str.contains(ind)]
                if not matched.empty:
                    # 取该行业流入前3的股票（需要个股资金流向，这里简化，从概念股获取）
                    pass
        
        # 2. 直接获取概念股
        # 华为概念
        huawei_stocks = fetcher.get_concept_stocks(cls.HUAWEI_CONCEPT)
        target_codes.update(huawei_stocks[:20])  # 限制数量
        
        # 机构重仓（通过行业权重股）
        # 使用沪深300成分股作为机构重仓代表
        try:
            hs300 = ak.index_stock_cons_csindex("000300")
            target_codes.update(hs300['成分券代码'].tolist()[:50])
        except:
            pass
        
        # 3. 手动补充各行业公认龙头（确保覆盖）
        must_have = [
            "600519",  # 贵州茅台（消费）
            "000858",  # 五粮液
            "002594",  # 比亚迪（新能源）
            "300750",  # 宁德时代
            "688981",  # 中芯国际（半导体）
            "002230",  # 科大讯飞（AI）
            "600276",  # 恒瑞医药（医药）
            "000001",  # 平安银行（金融，作为参考）
        ]
        target_codes.update(must_have)
        
        # 转换为带市场前缀的代码（akshare需要六位数字）
        codes = [code.zfill(6) for code in target_codes if code]
        return list(codes)

# ============================================================================
# 4. 资金与主力分析模块
# ============================================================================
class MoneyAnalyzer:
    @staticmethod
    def analyze_money_flow(code: str, quote: Dict) -> Dict:
        """
        分析主力资金动向
        返回: 主力净流入比例, 量比, 换手率, 主力成本估算
        """
        result = {}
        # 获取实时大单资金（akshare 没有直接提供，可用 Level2 接口，这里模拟）
        # 实际可以使用 stock_fund_flow_individual 获取个股资金流向
        try:
            flow_df = ak.stock_fund_flow_individual(symbol=code)
            if not flow_df.empty:
                # 取最近一天的主力净流入额
                latest = flow_df.iloc[-1]
                net_inflow = latest.get('主力净流入-净额', 0)
                net_inflow_ratio = latest.get('主力净流入-净占比', 0)
                result['main_net_inflow'] = net_inflow
                result['main_net_inflow_ratio'] = net_inflow_ratio
        except:
            pass
        
        # 量比 = 当前成交量 / 5日均量，需计算历史量，这里简化，通过 akshare 获取历史数据
        try:
            hist = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=(datetime.now()-timedelta(days=10)).strftime("%Y%m%d"))
            if not hist.empty:
                volume = hist['成交量'].values[-1]  # 最新成交量
                avg_vol_5 = hist['成交量'].tail(5).mean()
                result['volume_ratio'] = volume / avg_vol_5 if avg_vol_5 else 1.0
        except:
            result['volume_ratio'] = 1.0
        
        # 换手率直接从行情获取
        result['turnover_rate'] = quote.get('turnover_rate', 0)
        
        # 主力成本估算：通过筹码分布（简化用近期均价）
        # 可用 stock_zh_a_hist 计算区间均价
        try:
            hist = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=(datetime.now()-timedelta(days=60)).strftime("%Y%m%d"))
            if not hist.empty:
                # 计算60日均价作为主力成本近似
                avg_price_60 = (hist['收盘'].values * hist['成交量'].values).sum() / hist['成交量'].sum() if hist['成交量'].sum()>0 else quote.get('price', 0)
                result['main_cost'] = avg_price_60
        except:
            result['main_cost'] = quote.get('price', 0) * 0.95  # 假设成本低于现价5%
        
        return result

# ============================================================================
# 5. 综合分析引擎 (集成 DeepSeek)
# ============================================================================
class ComprehensiveAnalyzer:
    def __init__(self, code: str):
        self.code = code
        self.quote = DataFetcher.get_stock_quote(code)
        self.name = self.quote.get('name', code)
        self.news = DataFetcher.get_news_for_stock(code)
        self.fund_flow = MoneyAnalyzer.analyze_money_flow(code, self.quote)
        
    def analyze(self) -> Dict:
        """
        进行全方位分析，返回结构化结果
        """
        result = {
            "code": self.code,
            "name": self.name,
            "timestamp": datetime.now().isoformat(),
            "price": self.quote.get('price'),
            "change_pct": self.quote.get('change_pct'),
            "pe": self.quote.get('pe'),
            "pb": self.quote.get('pb'),
            "turnover_rate": self.fund_flow.get('turnover_rate'),
            "volume_ratio": self.fund_flow.get('volume_ratio'),
            "main_net_inflow_ratio": self.fund_flow.get('main_net_inflow_ratio'),
            "main_cost": self.fund_flow.get('main_cost'),
            "short_term_signal": "",
            "long_term_signal": "",
            "ai_comment": "",
        }
        
        # ----- 短期分析（基于技术面和资金） -----
        signals = []
        # 1. 量比 > 1.5 且涨幅>0 可能放量上涨
        if result['volume_ratio'] and result['volume_ratio'] > 1.5 and result['change_pct'] > 0:
            signals.append("放量上涨，短期动能强")
        elif result['volume_ratio'] and result['volume_ratio'] > 1.5 and result['change_pct'] < 0:
            signals.append("放量下跌，短期需谨慎")
        # 2. 主力净流入占比 > 5% 看好
        if result['main_net_inflow_ratio'] and result['main_net_inflow_ratio'] > 5:
            signals.append("主力资金大幅净流入，短期关注")
        elif result['main_net_inflow_ratio'] and result['main_net_inflow_ratio'] < -5:
            signals.append("主力资金大幅流出，短期回避")
        # 3. 换手率 > 5% 活跃
        if result['turnover_rate'] and result['turnover_rate'] > 5:
            signals.append("换手率高，交易活跃")
        # 4. 价格相对主力成本偏离
        if result['main_cost'] and result['price']:
            offset = (result['price'] - result['main_cost']) / result['main_cost'] * 100
            if offset > 20:
                signals.append("股价远高于主力成本，有获利回吐压力")
            elif offset < -10:
                signals.append("股价低于主力成本，有安全边际")
        result['short_term_signal'] = "；".join(signals) if signals else "中性"
        
        # ----- 长期分析（基于新闻和AI解读） -----
        long_signals = []
        # 通过新闻标题判断产品方向
        if self.news:
            # 用DeepSeek分析新闻主题
            news_text = "\n".join(self.news[:5])
            prompt = f"""
            以下是股票 {self.name}({self.code}) 近期的新闻标题：
            {news_text}
            请分析：
            1. 这些新闻主要涉及哪些产品/业务方向？
            2. 这些方向是否属于国家扶持或高潜力领域？
            3. 给出长期投资价值评分（1-10分）和简要理由。
            只输出JSON格式，包含 "products", "potential_score", "reason"。
            """
            ai_res = call_deepseek(prompt)
            try:
                ai_json = json.loads(ai_res)
                result['long_term_signal'] = f"评分：{ai_json.get('potential_score')}，理由：{ai_json.get('reason')}"
                result['ai_comment'] = ai_res
            except:
                result['ai_comment'] = ai_res
        else:
            result['long_term_signal'] = "无新闻数据，无法评估"
        
        return result

# ============================================================================
# 6. 主监控循环
# ============================================================================
def monitor_loop(interval_minutes=5):
    """
    持续监控，每隔 interval_minutes 分钟执行一次分析
    """
    # 初始化股票池
    target_stocks = StockScreener.get_target_stocks()
    logger.info(f"监控股票池数量: {len(target_stocks)}")
    
    while True:
        try:
            logger.info("===== 开始新一轮监控 =====")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            # 1. 先分析行业资金流向
            sector_df = DataFetcher.get_sector_flow()
            if not sector_df.empty:
                # 筛选关键行业
                key_sectors = sector_df[sector_df['名称'].str.contains('|'.join(StockScreener.KEY_INDUSTRIES + StockScreener.ESSENTIAL_INDUSTRIES))]
                logger.info(f"重点行业资金流向 (前5):\n{key_sectors[['名称', '主力净流入-净额', '涨跌幅']].head(5).to_string()}")
            
            # 2. 对每个股票进行分析
            signals = []
            for code in target_stocks[:20]:  # 限制数量防止过慢
                try:
                    analyzer = ComprehensiveAnalyzer(code)
                    result = analyzer.analyze()
                    # 若有明显的短期信号，输出警报
                    if "主力资金大幅净流入" in result['short_term_signal'] or "放量上涨" in result['short_term_signal']:
                        signals.append(f"{result['name']}({code}) 短期信号：{result['short_term_signal']}")
                    # 长期高评分
                    if "评分：" in result['long_term_signal']:
                        score_str = result['long_term_signal'].split("评分：")[1].split("，")[0]
                        try:
                            score = float(score_str)
                            if score >= 8:
                                signals.append(f"{result['name']}({code}) 长期评分 {score}，建议关注")
                        except:
                            pass
                except Exception as e:
                    logger.error(f"分析 {code} 失败: {e}")
                    continue
            
            # 输出汇总
            if signals:
                logger.info("\n".join(["【警报】"] + signals))
            else:
                logger.info("当前无明显交易信号")
            
            # 等待下次
            logger.info(f"本轮监控完成，等待 {interval_minutes} 分钟...")
            time.sleep(interval_minutes * 60)
            
        except KeyboardInterrupt:
            logger.info("监控已停止")
            break
        except Exception as e:
            logger.error(f"监控循环异常: {e}")
            time.sleep(60)

# ============================================================================
# 7. 入口
# ============================================================================
if __name__ == "__main__":
    # 首次运行测试单只股票
    test_code = "600519"
    print(f"测试分析 {test_code}:")
    analyzer = ComprehensiveAnalyzer(test_code)
    res = analyzer.analyze()
    print(json.dumps(res, ensure_ascii=False, indent=2))
    
    # 启动持续监控（注释掉以仅测试）
    # monitor_loop(interval_minutes=5)