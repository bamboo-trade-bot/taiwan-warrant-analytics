-- 權證基本資料（每日快照，保留歷史以便追蹤履約價/行使比例調整）
CREATE TABLE IF NOT EXISTS warrant_basic (
    snapshot_date TEXT NOT NULL,      -- YYYY-MM-DD 出表日期
    code          TEXT NOT NULL,      -- 權證代號
    name          TEXT,               -- 權證簡稱
    market        TEXT,               -- TSE / OTC
    issuer        TEXT,               -- 發行券商（由簡稱解析）
    kind          TEXT,               -- CALL / PUT
    category      TEXT,               -- 一般型 / 上下限型 / 牛證 / 熊證 ...
    style         TEXT,               -- 美式 / 歐式
    underlying    TEXT,               -- 標的代號
    underlying_nm TEXT,
    first_trade   TEXT,               -- 可行使開始日
    last_trade    TEXT,               -- 最後交易日
    maturity      TEXT,               -- 可行使截止日
    strike        REAL,               -- 最新履約價
    ratio         REAL,               -- 最新行使比例
    cap_price     REAL,               -- 上限價（牛證/上限型）
    floor_price   REAL,               -- 下限價
    issued_units  REAL,               -- 發行總量（張）
    cancelled     REAL,               -- 累計註銷（張）
    outstanding   REAL,               -- 流通在外（張）
    settle_type   TEXT,
    note          TEXT,
    PRIMARY KEY (snapshot_date, code)
);

-- 每日收盤行情
CREATE TABLE IF NOT EXISTS warrant_quote (
    trade_date   TEXT NOT NULL,
    code         TEXT NOT NULL,
    market       TEXT,
    name         TEXT,
    open         REAL, high REAL, low REAL, close REAL,
    volume       REAL,              -- 成交股數
    turnover     REAL,              -- 成交金額
    trades       REAL,              -- 成交筆數
    bid          REAL, bid_size REAL,
    ask          REAL, ask_size REAL,
    underlying   TEXT,
    und_close    REAL,              -- 標的收盤價
    quote_src    TEXT,              -- 買賣報價來源：NULL=交易所每日行情、MIS=盤後即時報價快照
    PRIMARY KEY (trade_date, code)
);

-- 衍生指標（自行計算）
CREATE TABLE IF NOT EXISTS warrant_metric (
    trade_date   TEXT NOT NULL,
    code         TEXT NOT NULL,
    mid          REAL,              -- 買賣中價（造市商真實報價）
    spread_pct   REAL,              -- 買賣價差比 = (ask-bid)/mid
    days_left    INTEGER,
    moneyness    REAL,              -- S/K （認售取 K/S）
    iv           REAL,              -- 隱含波動率（由中價反解）
    iv_close     REAL,              -- 由收盤價反解
    delta        REAL, gamma REAL, vega REAL, theta REAL,
    leverage     REAL,              -- 實質槓桿 = delta * S / (W/ratio)
    premium_pct  REAL,              -- 溢價比
    theo_price   REAL,              -- 理論價（用同標的中位數 IV）
    PRIMARY KEY (trade_date, code)
);

-- 履約價與行使比例的異動時間軸，由備註欄解析而來。
-- 交易所只給「最新」的履約價，要算歷史隱波就得知道當時的值。
CREATE TABLE IF NOT EXISTS warrant_adjustment (
    code      TEXT NOT NULL,
    adj_date  TEXT NOT NULL,     -- 除權息生效日
    kind      TEXT,              -- 除權 / 除息 / 除權、息
    strike    REAL,              -- 調整後履約價
    ratio     REAL,              -- 調整後行使比例
    PRIMARY KEY (code, adj_date)
);

CREATE INDEX IF NOT EXISTS ix_adj_code ON warrant_adjustment(code, adj_date);
CREATE INDEX IF NOT EXISTS ix_basic_code ON warrant_basic(code);
CREATE INDEX IF NOT EXISTS ix_basic_und  ON warrant_basic(underlying, snapshot_date);
CREATE INDEX IF NOT EXISTS ix_quote_und  ON warrant_quote(underlying, trade_date);
CREATE INDEX IF NOT EXISTS ix_metric_iv  ON warrant_metric(trade_date, iv);
