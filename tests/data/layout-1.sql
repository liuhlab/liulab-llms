-- A state file of layout 1, as SQLite's dump: one pass of the tests' split kind over unit
-- "a" with FakeGateway, by a liulab-llms that wrote layout 1, then sqlite3's iterdump, with
-- the application_id and user_version pragmas written above it. Layout 1 never changes, so
-- neither does this file.
PRAGMA application_id=1819045236;
PRAGMA user_version=1;
BEGIN TRANSACTION;
CREATE TABLE checks (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        name TEXT NOT NULL,
        message TEXT,
        PRIMARY KEY (kind, key, pass_id, seq),
        FOREIGN KEY (kind, key, pass_id) REFERENCES outcomes
    );
CREATE TABLE outcomes (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        pass_id INTEGER NOT NULL REFERENCES passes,
        ending TEXT NOT NULL,
        inputs_tag TEXT,
        answer TEXT,
        reason TEXT,
        PRIMARY KEY (kind, key, pass_id)
    );
INSERT INTO "outcomes" VALUES('split','a',1,'settled','text=red green','{"words": ["red", "green"]}',NULL);
CREATE TABLE passes (
        pass_id INTEGER PRIMARY KEY,
        meter TEXT NOT NULL,
        kind TEXT NOT NULL,
        kind_version TEXT NOT NULL,
        label TEXT NOT NULL,
        models TEXT NOT NULL,
        ceiling_usd REAL NOT NULL,
        off_peak_only INTEGER NOT NULL,
        repo_version TEXT NOT NULL,
        labllm_version TEXT NOT NULL,
        stopped TEXT,
        spent_usd REAL,
        first_cost_usd REAL,
        requests INTEGER,
        cache_hits INTEGER
    );
INSERT INTO "passes" VALUES(1,'fd61718b64ae4863b305aace86f7748e','split','1','split','{"writer": {"provider": "openai", "name": "test-writer", "thinking": null, "effort": null, "temperature": null, "max_output_tokens": 50}}',1.0,0,'unknown','0.0.1.dev2+g0dd49747c',NULL,0.027,0.027,1,0);
COMMIT;
