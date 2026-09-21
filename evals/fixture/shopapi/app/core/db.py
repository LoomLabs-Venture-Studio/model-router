import sqlite3, threading
_conn = sqlite3.connect("shop.db", check_same_thread=False)
_lock = threading.Lock()

def query(sql, params=()):
    cur = _conn.execute(sql, params)
    return cur.fetchall()

def execute(sql, params=()):
    _conn.execute(sql, params)
    _conn.commit()
