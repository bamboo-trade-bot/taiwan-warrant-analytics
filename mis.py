# -*- coding: utf-8 -*-
"""證交所 MIS 即時報價，用來補上櫃權證缺少的買賣報價。

櫃買中心的每日 OpenAPI 完全不揭露買賣價（實測 10,468 筆行情裡買價賣價
100% 是空的），上櫃權證因此只能用收盤價反解隱波。但收盤價可能是數小時前
的成交，配上收盤的標的價，兩者時點根本對不起來，算出的隱波會誤導排序。
MIS 有上櫃權證的五檔報價，收盤後掃一輪就能換成真正的買賣中價。

注意事項：
  * MIS 是證交所個股行情網頁的後端，不是正式開放 API，沒有服務條款也沒有
    SLA，密集輪詢有被擋 IP 的風險。請維持低頻率。
  * 實測批次上限約 120 檔，超過會回 rtcode=9999，乾淨失敗不會給半截資料。
  * 回傳的是「當下」的快照。寫入前必須比對 MIS 的日期欄位與目標交易日，
    否則會把別天的報價寫進去。
"""
import json, gzip, time
import urllib.request

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
BATCH = 120          # 實測 128 檔仍可、150 檔失敗，留點餘裕
PAUSE = 0.7          # 每批之間的禮貌等待


def _channel(code, market):
    return ("tse_" if market == "TSE" else "otc_") + code + ".tw"


def _first_num(s):
    """MIS 的五檔是底線分隔字串，例如 "0.3100_0.3000_"，取第一檔。"""
    if not s or s in ("-", ""):
        return None
    head = s.split("_")[0].strip()
    if not head or head == "-":
        return None
    try:
        return float(head)
    except ValueError:
        return None


def fetch_batch(channels, timeout=40, tries=3):
    url = MIS_URL + "?ex_ch=" + "|".join(channels) + "&json=1&delay=0"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (warrant-analytics/1.0)",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp",
        "Accept-Encoding": "gzip",
    })
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            txt = raw.decode("utf-8")
            d = json.loads(txt[txt.index("{"):])
            if d.get("rtcode") != "0000":
                raise RuntimeError("rtcode=%s %s" % (d.get("rtcode"), d.get("rtmessage")))
            return d.get("msgArray", [])
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError("MIS 取用失敗: %s" % last)


def snapshot(targets, trade_date, batch=BATCH, pause=PAUSE, progress=None):
    """targets: [(code, market), ...]；trade_date: YYYY-MM-DD。

    只回傳 MIS 日期與 trade_date 相符的資料，避免把別天的報價寫進資料庫。
    回傳 (可用的報價 list, 日期不符而略過的筆數)。
    """
    want = trade_date.replace("-", "")
    out, stale, done = [], 0, 0
    for i in range(0, len(targets), batch):
        chunk = targets[i:i + batch]
        chans = [_channel(c, m) for c, m in chunk]
        for m in fetch_batch(chans):
            if m.get("d") != want:
                stale += 1
                continue
            bid, ask = _first_num(m.get("b")), _first_num(m.get("a"))
            if bid is None and ask is None:
                continue
            out.append(dict(
                code=m.get("c"),
                bid=bid, bid_size=_first_num(m.get("g")),
                ask=ask, ask_size=_first_num(m.get("f")),
            ))
        done += len(chunk)
        if progress:
            progress(done, len(targets), len(out))
        if i + batch < len(targets):
            time.sleep(pause)
    return out, stale


def fill_missing_quotes(con, trade_date, market="OTC", progress=None):
    """把當日缺買賣報價的權證用 MIS 補起來，回傳 (更新筆數, 略過筆數)。

    預設只處理上櫃。上市的買賣報價來自證交所每日收盤行情，那才是權威來源；
    上市之所以有缺，是造市商當天真的沒掛單，MIS 一樣抓不到，混用反而讓
    資料來源變得不清不楚。
    """
    targets = [(r[0], market) for r in con.execute("""
        SELECT code FROM warrant_quote
         WHERE trade_date = ? AND market = ?
           AND (bid IS NULL OR ask IS NULL)
    """, (trade_date, market))]
    if not targets:
        return (0, 0)

    quotes, stale = snapshot(targets, trade_date, progress=progress)
    con.executemany("""
        UPDATE warrant_quote
           SET bid = ?, bid_size = ?, ask = ?, ask_size = ?, quote_src = 'MIS'
         WHERE trade_date = ? AND code = ?
    """, [(q["bid"], q["bid_size"], q["ask"], q["ask_size"], trade_date, q["code"])
          for q in quotes])
    con.commit()
    return (len(quotes), stale)
