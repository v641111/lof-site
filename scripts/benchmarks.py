"""
Per-fund benchmark mapping for LOF arbitrage real-time premium calculation.

Algorithm (Jisilu-style):
  estNav_today = NAV_published × (1 + benchmark.changePct)
  where changePct = (latest_benchmark_close / benchmark_close_on_jzrq_date) - 1

Asset type controls date alignment with jzrq:
  - 'hk':       HK market closes 16:00 CST, NAV published ~18:00 same day → use jzrq-date close
  - 'us':       US market closes ~04:00 CST next day → use (jzrq - 1) date close
  - 'futures':  24/7 markets, treat like 'us' (NY session close)
  - 'cn_idx':   A-share indices, use Sina hq instead of Yahoo
"""

# Yahoo ticker mapping for QDII & cross-border LOFs
YAHOO_BENCHMARKS = {
    # ─── 美股大盘 ─────────────────────────
    "161125": ("SPY",    "us",      "S&P 500 ETF"),
    "161229": ("VBR",    "us",      "Vanguard 小盘价值"),
    # ─── 美股行业 ────────────────────────
    "161126": ("XLV",    "us",      "标普医疗保健"),
    "161127": ("XBI",    "us",      "生物科技"),
    "161128": ("XLK",    "us",      "标普信息科技"),
    "161130": ("QQQ",    "us",      "纳斯达克100"),
    "162415": ("XLY",    "us",      "可选消费"),
    "501225": ("SOXX",   "us",      "费城半导体"),
    "501312": ("IXN",    "us",      "全球科技"),
    # ─── 油气/能源 ────────────────────────
    "162411": ("XOP",    "us",      "标普油气勘探开采"),
    "162719": ("XOP",    "us",      "道琼斯石油"),
    "163208": ("XOP",    "us",      "全球油气能源"),
    "160416": ("XOP",    "us",      "标普全球石油"),
    "161129": ("USO",    "us",      "WTI原油 ETF"),
    "160723": ("USO",    "us",      "原油 ETF"),
    "501018": ("USO",    "us",      "WTI原油 ETF"),
    # ─── 黄金/白银/商品 ──────────────────
    "161116": ("GDX",    "us",      "金矿股 ETF"),  # 黄金主题 = 金矿股
    "164701": ("GLD",    "us",      "黄金 ETF"),
    "160719": ("GLD",    "us",      "黄金 ETF"),
    "161226": ("SLV",    "us",      "白银 ETF"),
    "165513": ("DBC",    "us",      "大宗商品"),
    "160216": ("DBC",    "us",      "大宗商品"),
    # ─── 港股 ────────────────────────────
    "160125": ("^HSI",   "hk",      "恒生指数"),
    "160924": ("^HSI",   "hk",      "恒生指数"),
    "164705": ("^HSI",   "hk",      "恒生指数"),
    "501302": ("^HSI",   "hk",      "恒生指数"),
    "161831": ("^HSCE",  "hk",      "恒生中国企业"),
    "160717": ("^HSCE",  "hk",      "H股指数"),
    "501301": ("^HSI",   "hk",      "恒生中国30"),  # proxy
    "501303": ("^HSI",   "hk",      "恒生中型股"),  # proxy
    # ─── 中概互联网 ──────────────────────
    "160644": ("KWEB",   "us",      "中概互联"),
    "164906": ("KWEB",   "us",      "海外中国互联网"),
    # ─── 印度 ────────────────────────────
    "164824": ("INDA",   "us",      "印度 MSCI"),
    # ─── 美国 REIT ────────────────────────
    "160140": ("VNQ",    "us",      "美国 REIT"),
    "160141": ("VNQ",    "us",      "美国 REIT C"),
    # ─── 全球新能源汽车 ──────────────────
    "164212": ("DRIV",   "us",      "全球电动车"),
    # ─── 香港小盘 ────────────────────────
    "161124": ("EWH",    "hk",      "香港 MSCI"),  # proxy
    # ─── 纳指反向 / 其它复合策略 ─────────
    # (skipped - too specialized)
}

# A-share index LOFs — use Sina hq for these (s_sh000001 style)
SINA_BENCHMARKS = {
    "161725": ("s_sz399997", "cn_idx", "中证白酒"),
    "163407": ("s_sh000300", "cn_idx", "沪深300"),
    "160222": ("s_sz399396", "cn_idx", "国证食品饮料"),
    "160218": ("s_sz399393", "cn_idx", "国证房地产"),
    "160219": ("s_sz399394", "cn_idx", "国证医药卫生"),
    "160221": ("s_sz399395", "cn_idx", "国证有色金属"),
    "160223": ("s_sz399006", "cn_idx", "创业板指"),
    "160225": ("s_sz399417", "cn_idx", "国证新能源汽车"),
    "160615": ("s_sh000300", "cn_idx", "沪深300"),
    "160616": ("s_sh000905", "cn_idx", "中证500"),
    "160631": ("s_sh000931", "cn_idx", "中证银行"),
    "160633": ("s_sh399975", "cn_idx", "证券公司"),
    "160637": ("s_sz399006", "cn_idx", "创业板指"),
    "161017": ("s_sh000905", "cn_idx", "中证500"),
    "161024": ("s_sh399967", "cn_idx", "中证军工"),
    "161029": ("s_sh000932", "cn_idx", "中证银行"),
    "161032": ("s_sz399998", "cn_idx", "中证煤炭"),
    "163407": ("s_sh000300", "cn_idx", "沪深300"),
    "165509": ("s_sh000300", "cn_idx", "沪深300增强"),
    "165515": ("s_sh000300", "cn_idx", "沪深300"),
    "165511": ("s_sh000905", "cn_idx", "中证500"),
}

ALL_BENCHMARKS = {**YAHOO_BENCHMARKS, **SINA_BENCHMARKS}
