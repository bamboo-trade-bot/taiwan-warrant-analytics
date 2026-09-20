# -*- coding: utf-8 -*-
"""每日流程：抓資料 -> 入庫 -> 算 IV / Greeks -> 寫 warrant_metric。

用法:
    python main.py --date 20260919
    python main.py --date 20260919 --skip-fetch      # 只重算指標
"""
import argparse, datetime, statistics, sys, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest
import mis
import greeks as gk

RISK_FREE = 0.016          # 台銀一年期定存利率，之後可改成每日抓取


def ingest_all(con, date_str):
    # 行情要先入庫：上市基本資料缺標的代號，得靠行情表補，而且必須在寫入
    # 基本資料「之前」補好，否則去重會因為前後內容不一致而完全失效。
    n = ingest.upsert(con, "warrant_quote", ingest.twse_quotes(date_str))
    print("  上市每日行情 %6d 筆" % n)
    rows = [r for r in ingest.tpex_quotes() if r["trade_date"]]
    n = ingest.upsert(con, "warrant_quote", rows)
    print("  上櫃每日行情 %6d 筆" % n)

    umap = ingest.underlying_map(con)
    basic = ingest.twse_basic()
    for r in basic:
        if not r["underlying"]:
            r["underlying"] = umap.get(r["code"])
    n, same = ingest.upsert_basic(con, basic)
    print("  上市基本資料 %6d 筆異動（%d 筆未變動，略過）" % (n, same))
    filled = sum(1 for r in basic if r["outstanding"] is not None)
    print("    備註解析：流通在外補上 %d/%d 檔（%.1f%%）"
          % (filled, len(basic), 100.0 * filled / max(len(basic), 1)))
    print("    備註解析：履約價調整時間軸 %d 筆" % ingest.upsert_adjustments(con, basic))
    n, same = ingest.upsert_basic(con, ingest.tpex_basic())
    print("  上櫃基本資料 %6d 筆異動（%d 筆未變動，略過）" % (n, same))


def fill_otc_quotes(con, trade_date):
    """用 MIS 盤後快照補上櫃權證的買賣報價。

    失敗不該讓整條流程掛掉：MIS 不是正式 API，沒有 SLA。補不到就退回原本
    用收盤價的行為，只是資料品質差一點，而不是當天完全沒有資料。
    """
    state = {"next": 2500}          # CI 的 log 不會處理 \r，改成每隔一段印一行

    def show(done, total, got):
        if done >= state["next"] or done >= total:
            print("    掃描中 %5d/%d，已取得 %5d 筆報價" % (done, total, got))
            state["next"] = done + 2500
    try:
        n, stale = mis.fill_missing_quotes(con, trade_date, "OTC", progress=show)
        print("  MIS 補上櫃買賣報價 %5d 筆（日期不符略過 %d）" % (n, stale))
    except Exception as e:
        print("  MIS 掃描失敗，上櫃將沿用收盤價：%s" % e)


def build_metrics(con, trade_date, risk_free=RISK_FREE):
    cur = con.execute("""
        SELECT q.code, q.close, q.bid, q.ask, q.underlying, q.und_close,
               b.strike, b.ratio, b.kind, b.category, b.maturity, b.last_trade
          FROM warrant_quote q
          JOIN warrant_basic b
            ON b.code = q.code
           AND b.snapshot_date = COALESCE(
                 -- 優先取交易日當下已知的快照（回測時避免用到未來資訊）
                 (SELECT MAX(snapshot_date) FROM warrant_basic
                   WHERE code = q.code AND snapshot_date <= q.trade_date),
                 -- 基本資料表的出表日常比交易日晚（例如週六出表），退而取最早一份
                 (SELECT MIN(snapshot_date) FROM warrant_basic WHERE code = q.code))
         WHERE q.trade_date = ?
    """, (trade_date,))

    d0 = datetime.date.fromisoformat(trade_date)
    staged, skipped_barrier, no_iv = [], 0, 0

    for (code, close, bid, ask, und, S, K, ratio, kind, category,
         maturity, last_trade) in cur:
        if gk.is_barrier(category):
            skipped_barrier += 1
            continue
        if not maturity or S is None or K is None or not ratio:
            continue
        days = (datetime.date.fromisoformat(maturity) - d0).days
        if days <= 0:
            continue
        T = days / 365.0

        mid = (bid + ask) / 2.0 if (bid and ask) else None
        spread = ((ask - bid) / mid) if (mid and mid > 0 and bid and ask) else None

        # 以造市商中價為主、收盤價為輔（很多權證整天零成交，收盤價是空的）
        base = mid if mid else close
        m = gk.analyse(base, S, K, T, ratio, kind, risk_free)
        iv_close = gk.implied_vol(close / ratio, S, K, T, risk_free, 0.0, kind) if close else None
        if m["iv"] is None:
            no_iv += 1

        staged.append(dict(
            trade_date=trade_date, code=code, mid=mid, spread_pct=spread,
            days_left=days, moneyness=m["moneyness"], iv=m["iv"], iv_close=iv_close,
            delta=m["delta"], gamma=m["gamma"], vega=m["vega"], theta=m["theta"],
            leverage=m["leverage"], premium_pct=m["premium_pct"], theo_price=None,
            _und=und, _S=S, _K=K, _T=T, _ratio=ratio, _kind=kind,
        ))

    # 同標的的 IV 中位數 -> 理論價，用來抓「隱波灌水」的權證
    by_und = {}
    for r in staged:
        if r["iv"] and 0.05 < r["iv"] < 2.0 and r["moneyness"] and 0.7 < r["moneyness"] < 1.3:
            by_und.setdefault(r["_und"], []).append(r["iv"])
    med = dict((k, statistics.median(v)) for k, v in by_und.items() if len(v) >= 5)

    for r in staged:
        mv = med.get(r["_und"])
        if mv:
            r["theo_price"] = r["_ratio"] * gk.bs_price(
                r["_S"], r["_K"], r["_T"], mv, risk_free, 0.0, r["_kind"])
        for k in [k for k in r if k.startswith("_")]:
            del r[k]

    ingest.upsert(con, "warrant_metric", staged)
    print("  指標計算 %d 檔（牛熊/上下限型跳過 %d、無法解出 IV %d）"
          % (len(staged), skipped_barrier, no_iv))
    return len(staged)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None,
                    help="交易日 YYYYMMDD，省略則自動抓最近一個有行情的交易日")
    ap.add_argument("--db", default="warrant.db")
    ap.add_argument("--skip-fetch", action="store_true")
    ap.add_argument("--no-mis", action="store_true",
                    help="跳過 MIS 掃描，上櫃權證將沿用收盤價")
    a = ap.parse_args()

    date_str = a.date
    if not date_str:
        date_str = ingest.latest_trading_date()
        if not date_str:
            raise SystemExit("找不到最近的交易日：證交所可能暫時無法連線")
        print("自動判定最近交易日：%s" % date_str)
    trade_date = "%s-%s-%s" % (date_str[:4], date_str[4:6], date_str[6:8])

    con = ingest.connect(a.db)
    if not a.skip_fetch:
        print("擷取 %s ..." % trade_date)
        ingest_all(con, date_str)
        if not a.no_mis:
            fill_otc_quotes(con, trade_date)
    print("計算指標 ...")
    build_metrics(con, trade_date)
    con.close()
    print("完成 -> %s" % a.db)


if __name__ == "__main__":
    main()
