"""Poll Redis (gaba:* / da:*) and the observations table; append state deltas to a
timeline file. Lives under labs/ so writing here never trips uvicorn's reload."""
import os, sys, time, sqlite3, json, datetime as dt
import redis
DB = os.path.join(os.environ["LOCALAPPDATA"], "PersonaApp", "users.db")
OUT = sys.argv[1]
r = redis.Redis(decode_responses=True)
def now(): return dt.datetime.now().strftime("%H:%M:%S")
def log(line):
    with open(OUT, "a", encoding="utf-8") as f: f.write(f"{now()} {line}\n")
def redis_snap():
    snap = {}
    for k in r.keys("gaba:*") + r.keys("da:*"):
        t = r.type(k)
        snap[k] = r.get(k) if t == "string" else (r.hgetall(k) if t == "hash" else f"<{t}>")
    return snap
def db_rows(after_id):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    rows = c.execute("SELECT id, username, persona, event_type, timestamp, substr(content,1,90) "
                     "FROM observations WHERE id > ? ORDER BY id", (after_id,)).fetchall()
    c.close(); return rows
last_id = db_rows(0)[-1][0] if db_rows(0) else 0
prev = redis_snap()
log(f"START baseline observations.max_id={last_id} redis={json.dumps(prev)}")
while True:
    time.sleep(5)
    try:
        for row in db_rows(last_id):
            last_id = row[0]
            log(f"OBS id={row[0]} {row[1]}/{row[2]} {row[3]} ts={row[4]} :: {row[5]!r}")
        cur = redis_snap()
        for k in sorted(set(cur) | set(prev)):
            if cur.get(k) != prev.get(k):
                log(f"REDIS {k}: {prev.get(k)!r} -> {cur.get(k)!r}")
        prev = cur
    except Exception as e:
        log(f"ERR {type(e).__name__}: {e}")
