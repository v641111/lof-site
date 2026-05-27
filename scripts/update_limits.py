#!/usr/bin/env python3
"""
Scrape Eastmoney for per-LOF data:
  1. Subscription status & daily limit (jjgg_<code>_3.html "交易状态" block)
  2. NAV backup via f10/lsjz when Tiantian fundgz returns empty (QDII gap)

Output: data/limits.json with shape:
  {
    "_meta": {...},
    "data": {
      "<code>": {
        "apply": "开放申购|暂停申购|限大额",
        "limit": int|null,        # 元
        "redeem": "开放赎回|暂停赎回",
        "limit_text": str|null,
        "navBackup": {            # only present when fundgz lacks data
          "dwjz": float,
          "jzrq": "YYYY-MM-DD",
          "prevDwjz": float       # day before, for ratio reference
        }|null
      }
    }
  }
"""
import json, re, sys, time, os
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Ensure local imports (benchmarks.py) work regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(__file__).resolve().parent.parent
CODES_FILE = ROOT / "scripts" / "lof-codes.txt"
OUT_FILE = ROOT / "data" / "limits.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
URL_TPL = "https://fundf10.eastmoney.com/jjgg_{code}_3.html"

# Regex to extract the "交易状态" block. Handles three patterns:
#   <span>开放申购</span><span>开放赎回</span>          (no limit)
#   <span>暂停申购 </span><span>开放赎回</span>          (no limit number)
#   <span>暂停申购 </span>（<span>单日累计...10元</span>）<span>开放赎回</span>
TRADESTATE_RE = re.compile(
    r"交易状态[:：][^<]*"
    r"<span>([^<]+?)</span>"           # group 1: apply status
    r"(?:[^<]*<span>（<span>([^<]+?)</span>）</span>)?"  # group 2: optional limit phrase
    r"[^<]*<span>([^<]+?)</span>",     # group 3: redeem status
    re.DOTALL,
)

LIMIT_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(万元|元)")


def parse_limit_text(text: str):
    """Extract an integer 元 amount from text like '单日累计购买上限10元' or '1.00万元'."""
    if not text:
        return None
    m = LIMIT_NUM_RE.search(text)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * 10000) if m.group(2) == "万元" else int(n)


SPAN_RE = re.compile(r"<span[^>]*>([^<]*)</span>", re.DOTALL)
LIMIT_BRACKET_RE = re.compile(r"（\s*<span[^>]*>([^<]+?)</span>\s*）")
CLOSING_TAG_RE = re.compile(r"</(?:label|p|div)>")


def parse(html: str) -> dict:
    """
    Format observed:
      交易状态：<span>{apply}</span>
                    [<span>（<span>{limit_phrase}</span>）</span>]
                    <span>{redeem}</span>
                  </label>

    {apply}  ∈ {"开放申购", "暂停申购", "限大额", "暂停大额", "拒绝大额"}
    {redeem} ∈ {"开放赎回", "暂停赎回"}
    """
    idx = html.find("交易状态")
    if idx < 0:
        return {"apply": None, "limit": None, "redeem": None}
    # Slice until the </label> that closes this status block
    chunk = html[idx:idx + 1500]
    end = CLOSING_TAG_RE.search(chunk)
    if end:
        chunk = chunk[: end.start()]

    # Extract limit phrase (inside brackets)
    lm = LIMIT_BRACKET_RE.search(chunk)
    limit_text = lm.group(1).strip() if lm else None

    # Remove the bracketed portion entirely so it doesn't interfere with apply/redeem detection
    chunk_clean = LIMIT_BRACKET_RE.sub("", chunk)

    apply_s = None
    redeem_s = None
    for sm in SPAN_RE.finditer(chunk_clean):
        t = sm.group(1).replace("&nbsp;", "").strip()
        if not t:
            continue
        if apply_s is None:
            apply_s = t  # first non-empty span = apply status
        elif "赎回" in t:
            redeem_s = t
            break

    return {
        "apply": apply_s,
        "limit": parse_limit_text(limit_text),
        "redeem": redeem_s,
        "limit_text": limit_text,
    }


def fetch_one(code: str, retries: int = 2) -> tuple:
    url = URL_TPL.format(code=code)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://fund.eastmoney.com/"})
    last_err = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode("utf-8", errors="replace")
            return code, parse(html)
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1 + attempt)
    return code, {"error": f"fetch failed: {last_err}"}


def fetch_nav_backup(code: str) -> dict | None:
    """Fallback NAV via Eastmoney f10/lsjz for funds where Tiantian fundgz is empty.
    Returns {'dwjz': float, 'jzrq': str, 'prevDwjz': float} or None."""
    import json as _json
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=2"
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://fundf10.eastmoney.com/"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = _json.loads(resp.read().decode("utf-8", errors="replace"))
        items = (j.get("Data") or {}).get("LSJZList") or []
        if not items:
            return None
        latest = items[0]
        prev = items[1] if len(items) > 1 else None
        return {
            "dwjz": float(latest.get("DWJZ") or 0),
            "jzrq": latest.get("FSRQ"),
            "prevDwjz": float(prev.get("DWJZ") or 0) if prev else None,
        }
    except Exception:
        return None


def fundgz_has_nav(code: str) -> bool:
    """Quick check if Tiantian fundgz returns usable data."""
    url = f"https://fundgz.1234567.com.cn/js/{code}.js?rt={int(time.time()*1000)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        m = re.match(r"jsonpgz\((.+)\);?", body.strip())
        if not m or not m.group(1).strip():
            return False
        import json as _j
        d = _j.loads(m.group(1))
        return bool(float(d.get("dwjz", 0) or 0))
    except Exception:
        return False


def fetch_yahoo_chart(ticker: str, range_days: int = 15) -> list | None:
    """Fetch Yahoo Finance v8 chart daily data. Returns [{date:'YYYY-MM-DD', close: float}, ...]."""
    import json as _j, urllib.parse
    try:
        sym_enc = urllib.parse.quote(ticker)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym_enc}?range={range_days}d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            j = _j.loads(resp.read().decode("utf-8"))
        res = (j.get("chart") or {}).get("result") or []
        if not res:
            return None
        ts = res[0].get("timestamp") or []
        closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        import datetime as _dt
        out = []
        for t, c in zip(ts, closes):
            if c is None:
                continue
            d = _dt.datetime.fromtimestamp(t, _dt.timezone.utc).date().isoformat()
            out.append({"date": d, "close": float(c)})
        return out if out else None
    except Exception:
        return None


def fetch_sina_index(symbol: str) -> dict | None:
    """Fetch one A-share index quote from Sina. symbol like 's_sh000300'.
    Returns {price, prevClose, changePct} or None.
    s_xxx format response: var hq_str_s_sh000300="名称,价格,涨跌额,涨跌幅,成交量,成交额";
    """
    try:
        url = f"https://hq.sinajs.cn/list={symbol}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
        # Sina returns GBK
        text = raw.decode("gbk", errors="replace")
        m = re.search(r'"([^"]+)"', text)
        if not m:
            return None
        parts = m.group(1).split(",")
        if len(parts) < 4:
            return None
        # parts: name, price, change, change_pct, volume, amount
        price = float(parts[1])
        change = float(parts[2])
        prevClose = price - change
        changePct = float(parts[3]) if parts[3] else 0.0
        return {"price": price, "prevClose": prevClose, "changePct": changePct}
    except Exception:
        return None


def lookup_benchmark_change(chart: list, jzrq: str, asset_type: str) -> dict | None:
    """
    Given a chart (list of {date, close}) and the NAV date string,
    find the benchmark close that was "available" when the NAV was published,
    and compute changePct relative to the latest close.

    Date alignment:
      - 'hk':       look for close on jzrq (same day)
      - 'us'/'futures': look for the latest close STRICTLY BEFORE jzrq (US ends ~04:00 CST next day)
    """
    if not chart or not jzrq:
        return None
    import datetime as _dt
    try:
        target = _dt.date.fromisoformat(jzrq)
    except Exception:
        return None
    if asset_type == "hk":
        cands = [e for e in chart if e["date"] <= jzrq]
    else:
        cands = [e for e in chart if e["date"] < jzrq]
    if not cands:
        return None
    nav_date_entry = max(cands, key=lambda e: e["date"])
    latest = chart[-1]
    if nav_date_entry["close"] <= 0:
        return None
    chg = (latest["close"] / nav_date_entry["close"] - 1.0) * 100
    return {
        "navDateClose": nav_date_entry["close"],
        "navDateMatched": nav_date_entry["date"],
        "latestClose": latest["close"],
        "latestDate": latest["date"],
        "changePct": chg,
    }


def fetch_kline_and_nav(code: str, mkt_hint: str | None = None) -> dict | None:
    """Fetch (a) latest published NAV from Eastmoney f10/lsjz,
    and (b) the LOF closing price on that NAV's date from Tencent K-line.

    Returns: {dwjz, jzrq, navDateClose, prevDwjz, prevJzrq} or None.
    """
    import json as _j
    # 1. Latest NAVs (most recent 5)
    try:
        url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=5"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://fundf10.eastmoney.com/"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            j = _j.loads(resp.read().decode("utf-8", errors="replace"))
        items = (j.get("Data") or {}).get("LSJZList") or []
        if not items:
            return None
        latest = items[0]
        dwjz = float(latest.get("DWJZ") or 0)
        jzrq = latest.get("FSRQ")  # "YYYY-MM-DD"
        prev = items[1] if len(items) > 1 else None
        prevDwjz = float(prev.get("DWJZ") or 0) if prev else None
        prevJzrq = prev.get("FSRQ") if prev else None
    except Exception:
        return None
    if not dwjz or not jzrq:
        return None

    # 2. K-line close on jzrq date
    nav_date_close = None
    # Try both prefixes if mkt unknown
    prefixes = [mkt_hint] if mkt_hint else ["sz", "sh"]
    for pre in prefixes:
        try:
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={pre}{code},day,,,15,qfq"
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            kdata = _j.loads(text)
            day_rows = ((kdata.get("data") or {}).get(f"{pre}{code}") or {}).get("qfqday") or []
            if not day_rows:
                continue
            for row in day_rows:
                # row: [date, open, close, high, low, ...]
                if row[0] == jzrq:
                    nav_date_close = float(row[2])
                    break
            # If exact date not found (rare), use the LAST close on/before jzrq
            if nav_date_close is None:
                for row in reversed(day_rows):
                    if row[0] <= jzrq:
                        nav_date_close = float(row[2])
                        break
            if nav_date_close:
                break
        except Exception:
            continue

    return {
        "dwjz": dwjz,
        "jzrq": jzrq,
        "navDateClose": nav_date_close,
        "prevDwjz": prevDwjz,
        "prevJzrq": prevJzrq,
    }


def main():
    if not CODES_FILE.exists():
        print(f"ERROR: {CODES_FILE} not found", file=sys.stderr)
        sys.exit(1)
    codes = [ln.strip() for ln in CODES_FILE.read_text().splitlines() if ln.strip()]
    print(f"Scraping {len(codes)} LOF funds from Eastmoney...")

    results: dict = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, data = fut.result()
            results[code] = data
            done += 1
            if done % 50 == 0 or done == len(codes):
                print(f"  progress: {done}/{len(codes)}")

    # 对所有基金拉 (最新已公布净值 + K线收盘价) — 这是新的"自算"baseline
    # 公式 estNav_今 = NAV_published × (rtPrice / closeOnNAVDate)
    # 这种 close-on-NAV-date pair 是修复"24%虚高"问题的关键
    print("\nFetching NAV + K-line for all funds (for accurate estNav)...")
    with ThreadPoolExecutor(max_workers=6) as ex:
        kn = {ex.submit(fetch_kline_and_nav, c): c for c in codes}
        kn_ok = 0
        for i, fut in enumerate(as_completed(kn)):
            c = kn[fut]
            r = fut.result()
            if r:
                results.setdefault(c, {})["nav"] = r
                kn_ok += 1
            if (i+1) % 50 == 0:
                print(f"  nav progress: {i+1}/{len(codes)}")
    print(f"  NAV+K-line ok: {kn_ok}/{len(codes)}")
    with_close = sum(1 for v in results.values() if v.get("nav") and v["nav"].get("navDateClose"))
    print(f"  navDateClose available: {with_close}/{kn_ok}")

    # ─── 集思录算法核心 ────────────────────────────────────────
    # 对有 benchmark 映射的基金（QDII 跟踪海外、A股指数型），
    # 用 benchmark 当前价 vs NAV发布时刻 benchmark close 算 changePct
    # estNav = NAV × (1 + changePct)
    print("\nFetching benchmark prices for tracked funds...")
    from benchmarks import YAHOO_BENCHMARKS, SINA_BENCHMARKS

    # Unique tickers to fetch
    unique_yahoo = sorted(set(t for (t, _, _) in YAHOO_BENCHMARKS.values()))
    unique_sina = sorted(set(s for (s, _, _) in SINA_BENCHMARKS.values()))
    print(f"  Yahoo tickers: {len(unique_yahoo)}, Sina indices: {len(unique_sina)}")

    yahoo_charts: dict = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fetch_yahoo_chart, t): t for t in unique_yahoo}
        for fut in as_completed(futs):
            t = futs[fut]
            yahoo_charts[t] = fut.result()
    yahoo_ok = sum(1 for v in yahoo_charts.values() if v)
    print(f"  Yahoo charts ok: {yahoo_ok}/{len(unique_yahoo)}")

    sina_quotes: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(fetch_sina_index, s): s for s in unique_sina}
        for fut in as_completed(futs):
            s = futs[fut]
            sina_quotes[s] = fut.result()
    sina_ok = sum(1 for v in sina_quotes.values() if v)
    print(f"  Sina indices ok: {sina_ok}/{len(unique_sina)}")

    # Attach benchmark data to each fund's nav block
    benchmark_attached = 0
    for code, mapping in YAHOO_BENCHMARKS.items():
        ticker, asset_type, name = mapping
        chart = yahoo_charts.get(ticker)
        nav = results.get(code, {}).get("nav") or {}
        jzrq = nav.get("jzrq")
        if not chart or not jzrq:
            continue
        bench = lookup_benchmark_change(chart, jzrq, asset_type)
        if bench:
            results.setdefault(code, {}).setdefault("nav", {})["benchmark"] = {
                "ticker": ticker, "name": name, "type": asset_type, "source": "yahoo",
                **bench,
            }
            benchmark_attached += 1

    for code, mapping in SINA_BENCHMARKS.items():
        symbol, asset_type, name = mapping
        q = sina_quotes.get(symbol)
        if not q:
            continue
        # For Sina, we use the today's quote change as benchmark.changePct
        # (assumes NAV was published last night reflecting yesterday's close)
        results.setdefault(code, {}).setdefault("nav", {})["benchmark"] = {
            "ticker": symbol, "name": name, "type": asset_type, "source": "sina",
            "latestClose": q["price"], "navDateClose": q["prevClose"],
            "changePct": q["changePct"],
        }
        benchmark_attached += 1

    print(f"  benchmark mappings attached: {benchmark_attached}/{len(YAHOO_BENCHMARKS) + len(SINA_BENCHMARKS)}")

    # Compute summary stats
    ok = sum(1 for v in results.values() if "error" not in v and v.get("apply"))
    paused = sum(1 for v in results.values() if v.get("apply") and "暂停" in v["apply"])
    limited = sum(1 for v in results.values() if v.get("limit") is not None and v["limit"] > 0)
    open_funds = sum(1 for v in results.values() if v.get("apply") and "开放" in v["apply"] and v.get("limit") is None)
    over_100 = sum(1 for v in results.values() if v.get("limit") is None or (v.get("limit") and v["limit"] >= 100))
    nav_complete = sum(1 for v in results.values() if v.get("nav") and v["nav"].get("dwjz") and v["nav"].get("navDateClose"))
    with_benchmark = sum(1 for v in results.values() if v.get("nav") and v["nav"].get("benchmark"))

    out = {
        "_meta": {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "eastmoney(jjgg/lsjz) + tencent(kline) + yahoo(chart) + sina(idx)",
            "total": len(codes),
            "parsed": ok,
            "paused": paused,
            "limited": limited,
            "open": open_funds,
            "eligible_arb": over_100,
            "nav_complete": nav_complete,
            "with_benchmark": with_benchmark,
        },
        "data": results,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nWrote {OUT_FILE} ({OUT_FILE.stat().st_size} bytes)")
    print(f"  parsed={ok}/{len(codes)}, paused={paused}, limited={limited}, fully-open={open_funds}, arb-eligible(>=100)={over_100}")


if __name__ == "__main__":
    main()
