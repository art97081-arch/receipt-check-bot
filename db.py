import sqlite3
from typing import List, Optional

DB_PATH = 'bot_data.db'

def init_db(owner_tg_id: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')
    cur.execute('''
    CREATE TABLE IF NOT EXISTS allowed_users (
        tg_id INTEGER PRIMARY KEY
    )
    ''')
    # store owner
    cur.execute('REPLACE INTO meta (key, value) VALUES (?, ?)', ("owner_tg_id", str(owner_tg_id)))
    conn.commit()
    conn.close()

def get_owner() -> Optional[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT value FROM meta WHERE key = ?', ("owner_tg_id",))
    row = cur.fetchone()
    conn.close()
    if row:
        return int(row[0])
    return None

def add_allowed(tg_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO allowed_users (tg_id) VALUES (?)', (tg_id,))
    conn.commit()
    conn.close()

def remove_allowed(tg_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('DELETE FROM allowed_users WHERE tg_id = ?', (tg_id,))
    conn.commit()
    conn.close()

def list_allowed() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT tg_id FROM allowed_users')
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

def is_allowed(tg_id: int) -> bool:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT 1 FROM allowed_users WHERE tg_id = ?', (tg_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)
