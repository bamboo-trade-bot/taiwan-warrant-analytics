# -*- coding: utf-8 -*-
"""把 SQLite 的計算結果匯出成網頁用的精簡 JSON。

只輸出「造市商當天有掛買賣報價」的權證 —— 沒有報價的權證買不到，
放進篩選器只會製造假選項。
"""
import sqlite3, json, argparse, os


def rnd(x, n):
    return None if x is None else round(x, n)


def export(db, trade_date, basis_date, out):
    con = sqlite3.connect(db)
    rows = con.execute("""
        SELECT q.underlying, q.und_close, b.underlying_nm, b.issuer, b.kind,
               m.code, b.name, m.days_left, b.strike, b.ratio,
               m.mid, m.iv, m.delta, m.leverage, m.premium_pct, m.spread_pct,
               q.volume, b.outstanding, b.market, b.maturity
          FROM warrant_metric m
          JOIN warrant_quote q USING (trade_date, code)
          JOIN warrant_basic b ON b.code = m.code AND b.snapshot_date = ?
         WHERE m.trade_date = ?
           AND m.iv IS NOT NULL AND m.mid IS NOT NULL AND m.mid > 0
           AND m.days_left >= 5
           AND q.underlying <> '' AND q.und_close IS NOT NULL
           -- 剔除明顯失真的報價：價差比破 100%、IV 破 300%，多半是單邊掛單
           AND m.spread_pct <= 1.0
           AND m.iv BETWEEN 0.03 AND 3.0
    """, (basis_date, trade_date)).fetchall()

    issuers, unds, out_rows = [], {}, []
    for (und, und_close, und_nm, issuer, kind, code, name, days, strike, ratio,
         mid, iv, delta, lev, prem, spread, vol, outstanding, market, maturity) in rows:
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
            rnd(mid, 2),                        # 8 買賣中價
            rnd(iv, 4),                         # 9 隱含波動率
            rnd(delta, 5),                      # 10 Delta
            rnd(lev, 2),                        # 11 實質槓桿
            rnd(prem, 4),                       # 12 溢價比
            rnd(spread, 4),                     # 13 買賣價差比
            int((vol or 0) / 1000),             # 14 成交張數
            None if outstanding is None else int(outstanding),   # 15 流通在外
            0 if market == "TSE" else 1,        # 16 市場別
            maturity,                           # 17 到期日
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="warrant.db")
    ap.add_argument("--date", default="2026-09-18")
    ap.add_argument("--basis", default="2026-09-19", help="warrant_basic 的快照日期")
    ap.add_argument("--out", default="docs/data.js",
                    help=".js 會包成 window.WARRANT_DATA；.json 則輸出純 JSON")
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    export(a.db, a.date, a.basis, a.out)
