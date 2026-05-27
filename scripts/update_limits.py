#!/usr/bin/env python3
"""
Scrape Eastmoney announcement pages for LOF subscription status & daily limit.

The fund detail page (https://fundf10.eastmoney.com/jjgg_<code>_3.html) renders a
"交易状态" block server-side, e.g.:

    交易状态：<span>暂停申购 </span>（<span>单日累计购买上限10元</span>）<span>开放赎回</span>

We extract three pieces:
  - apply  : 申购状态 ("开放申购" / "暂停申购" / "限制大额申购" / "暂停大额申购" ...)
  - limit  : 单日累计申购上限 (integer 元, None when unlimited)
  - redeem : 赎回状态 ("开放赎回" / "暂停赎回")

Output: data/limits.json
"""
import json, re, sys, time
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

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

    # Compute summary stats
    ok = sum(1 for v in results.values() if "error" not in v and v.get("apply"))
    paused = sum(1 for v in results.values() if v.get("apply") and "暂停" in v["apply"])
    limited = sum(1 for v in results.values() if v.get("limit") is not None and v["limit"] > 0)
    open_funds = sum(1 for v in results.values() if v.get("apply") and "开放" in v["apply"] and v.get("limit") is None)
    over_100 = sum(1 for v in results.values() if v.get("limit") is None or (v.get("limit") and v["limit"] >= 100))

    out = {
        "_meta": {
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "fundf10.eastmoney.com (jjgg_*_3.html)",
            "total": len(codes),
            "parsed": ok,
            "paused": paused,
            "limited": limited,
            "open": open_funds,
            "eligible_arb": over_100,  # limit >= 100 or no limit (and apply contains 开放)
        },
        "data": results,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"\nWrote {OUT_FILE} ({OUT_FILE.stat().st_size} bytes)")
    print(f"  parsed={ok}/{len(codes)}, paused={paused}, limited={limited}, fully-open={open_funds}, arb-eligible(>=100)={over_100}")


if __name__ == "__main__":
    main()
