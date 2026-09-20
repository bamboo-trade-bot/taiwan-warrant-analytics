# -*- coding: utf-8 -*-
"""把 SQLite 的計算結果匯出成網頁用的精簡 JSON。

參考價優先採用造市商的買賣中價，那才是當下真正能成交的價格。但櫃買中心
的 OpenAPI 不揭露買賣報價，上櫃權證一律沒有中價，因此退而採用收盤價，
並用第 18 欄標記來源，讓網頁能誠實呈現這個差別。曾經因為要求「必須有
中價」而把整個上櫃市場靜默排除掉，不要再犯。
"""
import sqlite3, json, argparse, os, re


def rnd(x, n):
    return None if x is None else round(x, n)


def export(db, trade_date, out):
    con = sqlite3.connect(db)
    # 上市與上櫃的基本資料出表日不同（證交所可能晚一天），所以不能寫死單一
    # 快照日期，必須逐檔取「交易日當下已知」的那一份，和 main.py 的規則一致。
    rows = con.execute("""
        SELECT q.underlying, q.und_close, b.underlying_nm, b.issuer, b.kind,
               m.code, b.name, m.days_left, b.strike, b.ratio,
               COALESCE(m.mid, q.close) AS price, m.mid,
               m.iv, m.delta, m.leverage, m.premium_pct, m.spread_pct,
               q.volume, b.outstanding, b.market, b.maturity
          FROM warrant_metric m
          JOIN warrant_quote q USING (trade_date, code)
          JOIN warrant_basic b ON b.code = m.code AND b.snapshot_date = COALESCE(
                (SELECT MAX(snapshot_date) FROM warrant_basic
                  WHERE code = m.code AND snapshot_date <= m.trade_date),
                (SELECT MIN(snapshot_date) FROM warrant_basic WHERE code = m.code))
         WHERE m.trade_date = ?
           AND m.iv IS NOT NULL
           AND m.days_left >= 5
           AND q.underlying <> '' AND q.und_close IS NOT NULL
           AND COALESCE(m.mid, q.close) > 0
           -- 櫃買中心不揭露買賣報價，上櫃權證的價差比永遠是 NULL，不能一併濾掉
           AND (m.spread_pct IS NULL OR m.spread_pct <= 1.0)
           AND m.iv BETWEEN 0.03 AND 3.0
    """, (trade_date,)).fetchall()

    issuers, unds, out_rows = [], {}, []
    for (und, und_close, und_nm, issuer, kind, code, name, days, strike, ratio,
         price, mid, iv, delta, lev, prem, spread, vol, outstanding,
         market, maturity) in rows:
        issuer = issuer or "其他"
        if issuer not in issuers:
            issuers.append(issuer)
        if und not in unds:
            unds[und] = [und, und_nm or und, rnd(und_close, 2), len(unds)]
        out_rows.append([
            unds[und][3],                       # 0 標的索引
            issuers.index(issuer),              # 1 券商索引
            0 if kind == "CALL" else 1,         # 2 認購/認售
            code,                               # 3 權證代號
            name,                               # 4 權證簡稱
            days,                               # 5 剩餘天數
            rnd(strike, 2),                     # 6 履約價
            rnd(ratio, 5),                      # 7 行使比例
            rnd(price, 2),                      # 8 參考價（中價，無中價時用收盤價）
            rnd(iv, 4),                         # 9 隱含波動率
            rnd(delta, 5),                      # 10 Delta
            rnd(lev, 2),                        # 11 實質槓桿
            rnd(prem, 4),                       # 12 溢價比
            rnd(spread, 4),                     # 13 買賣價差比
            int((vol or 0) / 1000),             # 14 成交張數
            None if outstanding is None else int(outstanding),   # 15 流通在外
            0 if market == "TSE" else 1,        # 16 市場別
            maturity,                           # 17 到期日
            0 if mid is not None else 1,        # 18 參考價來源：0 買賣中價、1 收盤價
        ])

    # 標的依權證檔數排序，選單上方先出現主流標的
    cnt = {}
    for r in out_rows:
        cnt[r[0]] = cnt.get(r[0], 0) + 1
    order = sorted(unds.values(), key=lambda u: -cnt.get(u[3], 0))
    remap = {}
    und_list = []
    for new_i, u in enumerate(order):
        remap[u[3]] = new_i
        und_list.append([u[0], u[1], u[2], cnt.get(u[3], 0)])
    for r in out_rows:
        r[0] = remap[r[0]]

    payload = {
        "date": trade_date,
        "issuers": issuers,
        "unds": und_list,
        "rows": out_rows,
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with open(out, "w", encoding="utf-8") as fh:
        if out.endswith(".js"):
            fh.write("window.WARRANT_DATA=" + body + ";")
        else:
            fh.write(body)
    size = os.path.getsize(out) / 1024 / 1024
    print("輸出 %s：%d 檔權證 / %d 個標的 / %d 家券商 / %.2f MB"
          % (out, len(out_rows), len(und_list), len(issuers), size))
    stamp_index(out, trade_date)


def stamp_index(out, trade_date):
    """把資料日期寫進 index.html 的 script src。

    GitHub Pages 送出 Cache-Control: max-age=600，若不換網址，使用者可能拿到
    新的 index.html 配上舊的 data.js，欄位會對不上。加上版本參數即可強制重抓。
    """
    idx = os.path.join(os.path.dirname(out) or ".", "index.html")
    base = os.path.basename(out)
    if not os.path.exists(idx):
        return
    with open(idx, encoding="utf-8") as fh:
        html = fh.read()
    new = re.sub(r'src="' + re.escape(base) + r'(\?[^"]*)?"',
                 'src="%s?d=%s"' % (base, trade_date.replace("-", "")), html)
    if new != html:
        with open(idx, "w", encoding="utf-8") as fh:
            fh.write(new)
        print("已更新 %s 的資料版本戳記 -> %s" % (idx, trade_date.replace("-", "")))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="warrant.db")
    ap.add_argument("--date", default=None,
                    help="交易日 YYYY-MM-DD，省略則用資料庫裡最新的一天")
    ap.add_argument("--out", default="docs/data.js",
                    help=".js 會包成 window.WARRANT_DATA；.json 則輸出純 JSON")
    a = ap.parse_args()
    date = a.date
    if not date:
        con = sqlite3.connect(a.db)
        row = con.execute("SELECT MAX(trade_date) FROM warrant_metric").fetchone()
        con.close()
        if not row or not row[0]:
            raise SystemExit("資料庫裡還沒有任何指標，請先執行 main.py")
        date = row[0]
        print("採用資料庫中最新的交易日：%s" % date)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    export(a.db, date, a.out)
