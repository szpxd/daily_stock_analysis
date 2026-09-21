#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
触发价监控（彭先生持仓专用，零重依赖）

为什么需要这个脚本：
  daily_stock_analysis 项目自带的"触发价告警中心"(price_cross) 依赖 AlertWorker，
  而 AlertWorker 只在常驻 `python main.py --schedule` 进程中注册生效。
  GitHub Actions 跑的是 `main.py --no-market-review` 一次性命令，
  项目 workflow 里也完全没有 AGENT_EVENT_ALERT_RULES_JSON 这一项，
  因此在 Actions 路径上触发价告警永远不会响。本脚本补上这个缺口。

数据源：腾讯行情 qt.gtimg.cn（主）+ 新浪 hq.sinajs.cn（兜底），均无需密钥。
退出码：0=无触发；2=有触发；1=取价失败
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

# ---------------------------------------------------------------------------
# 触发价规则（2026-08-26 彭先生确认）
# 注：茅台/腾讯受"集中度约束优先于触发价"约束，到价也不必然买入，标记为 reference
# ---------------------------------------------------------------------------
RULES = [
    {"symbol": "sh601318", "code": "601318", "name": "中国平安", "trigger": 45.0,   "note": "≤45 买入",              "reference": False},
    {"symbol": "sh600660", "code": "600660", "name": "福耀玻璃", "trigger": 50.0,   "note": "≤50 买入",              "reference": False},
    {"symbol": "sh600887", "code": "600887", "name": "伊利股份", "trigger": 22.0,   "note": "≤22 买入",              "reference": False},
    {"symbol": "sz000333", "code": "000333", "name": "美的集团", "trigger": 70.0,   "note": "≤70 买入",              "reference": False},
    {"symbol": "sh600519", "code": "600519", "name": "贵州茅台", "trigger": 1300.0, "note": "参考价·集中度约束优先", "reference": True},
    {"symbol": "hk00700",  "code": "00700",  "name": "腾讯控股", "trigger": 450.0,  "note": "参考价·集中度约束优先", "reference": True},
]

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _get(url: str, extra_headers: dict | None = None) -> str:
    headers = {"User-Agent": _UA}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("gbk", errors="replace")


def fetch_tencent(symbols: list[str]) -> dict[str, float]:
    """腾讯行情：A股 v_sh600519="1~名称~代码~现价~..."；港股 v_hk00700="100~名称~代码~现价~..."
    字段索引 3 = 现价，A股/港股一致。"""
    raw = _get("https://qt.gtimg.cn/q=" + ",".join(symbols))
    out: dict[str, float] = {}
    for line in raw.splitlines():
        line = line.strip().rstrip(";")
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        sym = key.strip().replace("v_", "")
        parts = val.strip().strip('"').split("~")
        if len(parts) < 4:
            continue
        try:
            price = float(parts[3])
        except (ValueError, IndexError):
            continue
        if price > 0:
            out[sym] = price
    return out


def fetch_sina(symbols: list[str]) -> dict[str, float]:
    """新浪行情：A股字段 3 = 现价；港股字段 6 = 现价。"""
    raw = _get("https://hq.sinajs.cn/list=" + ",".join(symbols),
               {"Referer": "https://finance.sina.com.cn"})
    out: dict[str, float] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        sym = key.strip().replace("var hq_str_", "")
        parts = val.strip().strip('";').split(",")
        idx = 6 if sym.startswith("hk") else 3
        try:
            price = float(parts[idx])
        except (ValueError, IndexError):
            continue
        if price > 0:
            out[sym] = price
    return out


def main() -> int:
    symbols = [r["symbol"] for r in RULES]
    quotes: dict[str, float] = {}
    source = "n/a"

    for label, fn in (("tencent", fetch_tencent), ("sina", fetch_sina)):
        try:
            got = fn(symbols)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {label} 取价失败: {exc}", file=sys.stderr)
            continue
        missing = [s for s in symbols if s not in got]
        if not missing:
            quotes, source = got, label
            break
        if len(got) > len(quotes):
            quotes, source = got, f"{label}(部分 {len(got)}/{len(symbols)})"

    if not quotes:
        print("[error] 所有数据源均取价失败", file=sys.stderr)
        return 1

    hits, rows = [], []
    for rule in RULES:
        sym = rule["symbol"]
        price = quotes.get(sym)
        if price is None:
            rows.append({**rule, "price": None, "gap_pct": None, "hit": False})
            continue
        hit = price <= rule["trigger"]
        gap = (price - rule["trigger"]) / rule["trigger"] * 100
        row = {**rule, "price": round(price, 3), "gap_pct": round(gap, 2), "hit": hit}
        rows.append(row)
        if hit and not rule["reference"]:
            hits.append(row)

    print(f"数据源: {source}")
    print(f"{'标的':<10}{'代码':<9}{'现价':>10}{'触发价':>10}{'距离':>9}  状态")
    print("-" * 62)
    for r in rows:
        if r["price"] is None:
            print(f"{r['name']:<10}{r['code']:<9}{'N/A':>10}{r['trigger']:>10.2f}{'--':>9}  ⚠️取价失败")
            continue
        if r["hit"]:
            mark = "🎯 已触发(参考)" if r["reference"] else "🎯 已触发"
        else:
            mark = "参考价" if r["reference"] else "未触发"
        print(f"{r['name']:<10}{r['code']:<9}{r['price']:>10.2f}{r['trigger']:>10.2f}{r['gap_pct']:>8.2f}%  {mark}")

    result = {"source": source, "rows": rows, "hits": hits, "hit_count": len(hits)}
    out_path = os.getenv("ALERT_RESULT_PATH", "alert_result.json")
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"[warn] 结果落盘失败: {exc}", file=sys.stderr)

    if hits:
        print(f"\n✅ 命中 {len(hits)} 只：{', '.join(h['name'] for h in hits)}")
        return 2
    print("\n无触发（茅台/腾讯为参考价，不计入命中）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
