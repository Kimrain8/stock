
## 1.2 `docs/architecture.md`

````markdown
# 架构设计

## 一、系统总览

```mermaid
graph TB
    User[浏览器用户] -->|HTTP/WS| FastAPI[FastAPI 主入口]
    FastAPI --> AuthMW[认证中间件]
    AuthMW --> PermEngine[权限引擎]
    PermEngine --> Routes[路由分发]

    Routes --> Explorer[数据探索器]
    Routes --> Trend[趋势分析]
    Routes --> Scan[选股扫描]
    Routes --> Analysis[智能分析中心]

    Analysis --> DataFetcher[数据抓取]
    Analysis --> Preprocessor[数据预处理]
    Analysis --> Agents[4 Agent 协同]
    Analysis --> Judge[综合评审]

    DataFetcher --> DataCache[本地缓存]
    DataFetcher --> AKShare[akshare]
    DataFetcher --> MySQL[MySQL]

    Agents --> DeepSeek[DeepSeek API]

    Judge --> ReportStore[报告保存]
    ReportStore --> ReportsDir[reports/]

    ReportStore --> Reflection[复盘验证]
    Reflection --> Lessons[lessons_learned/]
    Reflection --> RuleBook[规则库]

    RuleBook --> RulesDir[rules/]
    RuleBook -.注入.-> Judge