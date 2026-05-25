import sqlite3

DB_PATH = r'C:\Users\insom\.gemini\antigravity\Project_Sleeper\architect_memory.db'
OUT_PATH = r'C:\Users\insom\OneDrive\Desktop\Personas\PersonaApp-merged\memory_dump.txt'

def dump_memory():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    with open(OUT_PATH, 'w', encoding='utf-8') as f:
        f.write('=== DECISION LOG ===\n\n')
        rows = conn.execute('SELECT id, decision, rationale, tags, timestamp FROM decision_log ORDER BY timestamp ASC').fetchall()
        for r in rows:
            f.write(f"[{r['timestamp']}] #{r['id']} | TAGS: {r['tags']}\n")
            f.write(f"DECISION: {r['decision']}\n")
            f.write(f"RATIONALE: {r['rationale']}\n\n")
            
    conn.close()
    print("Database dumped successfully to memory_dump.txt")

if __name__ == '__main__':
    dump_memory()
