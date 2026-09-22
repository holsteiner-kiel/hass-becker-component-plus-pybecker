# pylint: disable=missing-function-docstring
# pylint: disable=missing-class-docstring
import logging
import os
import time
import sqlite3
import threading
from functools import wraps
from random import randrange
from .becker_helper import hex4

NUMBER_FILE = "centronic-stick.num"
SQL_DB_FILE = "centronic-stick.db"
FILE_PATH = os.path.dirname(os.path.realpath(__file__))

_LOGGER = logging.getLogger(__name__)


def _synchronized(method):
    """Serialize access to one Database connection."""
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Database:

    def __init__(self, filename=None):
        self.filename = filename or os.path.join(FILE_PATH, SQL_DB_FILE)
        # Home Assistant can access the same Database instance from the event
        # loop and executor threads. sqlite3 therefore needs cross-thread use
        # enabled, while the re-entrant lock below guarantees that only one
        # operation uses the connection at a time.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.filename, check_same_thread=False)
        self.check()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    @_synchronized
    def close(self):
        """Close the SQLite connection safely."""
        self.conn.close()

    @_synchronized
    def check(self):
        # check if table already exist
        c = self.conn.cursor()
        check_table = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='unit'")
        if check_table.fetchone() is None:
            self.create()
            self.migrate()

    @_synchronized
    def migrate(self):
        try:
            # migrate the previous *.num file into its sqllite database
            self.old_file = os.path.join(FILE_PATH, NUMBER_FILE)
            if os.path.isfile(self.old_file):
                _LOGGER.info('Migrate previous *.num file...')
                with open(self.old_file, "r") as file:
                    number = int(file.read())
                    c = self.conn.cursor()
                    c.execute("UPDATE unit SET increment = ?, configured = ? WHERE code = ?", (number, 1, '1737b',))
                    self.conn.commit()
                    os.remove(self.old_file)
        except (sqlite3.Error, OSError):
            _LOGGER.error('Migration failed')
            self.conn.rollback()

    @_synchronized
    def init_dummy(self):
        try:
            c = self.conn.cursor()
            inc = randrange(10, 40, 1)
            c.execute("UPDATE unit SET increment = ?, configured = ? WHERE code = ?", (inc, 1, '1737b',))
            self.conn.commit()
        except (sqlite3.Error, OSError):
            _LOGGER.error('Dummy Unit initialization failed')
            self.conn.rollback()

    @_synchronized
    def create(self):
        # create the database table

        _LOGGER.info('Create database...')
        c = self.conn.cursor()
        c.execute('CREATE TABLE unit (code NVARCHAR(5), increment INTEGER(4), configured BIT, executed INTEGER, UNIQUE(code))')
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", ('1737b', 0, 0, 0,))
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", ('1737c', 0, 0, 0,))
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", ('1737d', 0, 0, 0,))
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", ('1737e', 0, 0, 0,))
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", ('1737f', 0, 0, 0,))

        self.conn.commit()

    @_synchronized
    def output(self):
        c = self.conn.cursor()
        res = c.execute('SELECT * FROM unit')
        _LOGGER.info('%-10s%-10s%-12s%-15s' % ('code', 'increment', 'configured', 'last run'))
        _LOGGER.info('%-10s%-18s%-12s%-15s' % ('code', 'increment (hex)', 'configured', 'last run'))
        for line in res.fetchall():
            last_run = '(unknown)'

            if line[3] > 0:
                last_run = time.strftime('%Y-%m-%d %H:%M', time.localtime(line[3]))
            _LOGGER.info('%-10s%-10s%-12s%-15s' % (line[0], line[1], line[2], last_run))
            _LOGGER.info('%-10s%-6s%-12s%-12s%-15s' % (line[0], line[1], "(0x" + hex4(line[1]) + ")", line[2], last_run))

    @_synchronized
    def get_unit(self, rowid):
        c = self.conn.cursor()
        res = c.execute("SELECT code, increment, configured FROM unit WHERE rowid = ?", (rowid,))
        result = res.fetchone()

        if result is not None:
            return list(result)

    @_synchronized
    def get_all_units(self):
        c = self.conn.cursor()
        res = c.execute('SELECT code, increment, configured FROM unit WHERE configured = 1 ORDER BY code ASC')
        result = []

        for row in res.fetchall():
            result.append(list(row))

        return result

    @_synchronized
    def export_units(self):
        """Return every unit row as dicts (all rows, unfiltered)."""
        c = self.conn.cursor()
        res = c.execute(
            "SELECT code, increment, configured FROM unit ORDER BY code ASC"
        )
        return [
            {"code": row[0], "increment": int(row[1]), "configured": int(row[2])}
            for row in res.fetchall()
        ]

    @_synchronized
    def import_units(self, rows):
        """Update increment and configured for known units atomically."""
        c = self.conn.cursor()
        seen_codes = set()
        try:
            for row in rows:
                code = row["code"]
                if code in seen_codes:
                    raise sqlite3.IntegrityError(
                        f"Duplicate Becker unit code: {code}"
                    )
                seen_codes.add(code)
                result = c.execute(
                    "UPDATE unit SET increment = ?, configured = ? WHERE code = ?",
                    (int(row["increment"]), int(row["configured"]), code),
                )
                if result.rowcount != 1:
                    raise sqlite3.IntegrityError(
                        f"Unknown Becker unit code: {code}"
                    )
            self.conn.commit()
        except (sqlite3.Error, KeyError, TypeError, ValueError):
            self.conn.rollback()
            raise

    @_synchronized
    def get_rowid_from_unit(self, code, create=True):
        c = self.conn.cursor()
        res = c.execute('SELECT rowid FROM unit WHERE code = ?', (code,))

        result = res.fetchone()
        rowid = result[0] if result is not None else -1

        return rowid

    @_synchronized
    def add_unit(self, unit):
        c = self.conn.cursor()
        c.execute("INSERT INTO unit VALUES (?, ?, ?, ?)", (unit[0], int(unit[1]), int(unit[2]), 0,))
        self.conn.commit()

    @_synchronized
    def remove_unit(self, code):
        c = self.conn.cursor()
        c.execute("DELETE FROM unit WHERE code = ?", (code,))
        self.conn.commit()

    @_synchronized
    def set_unit(self, unit, test=False):
        c = self.conn.cursor()
        last_run = int(time.time())

        #c.execute('UPDATE unit SET increment = ?, configured = ?, executed = ? WHERE code = ?',
        #          (unit[1], unit[2], last_run, unit[0],))

        if len(unit[0]) < 5:
            # assume the index is given (and not the exact unit)
            c.execute('UPDATE unit SET increment = ?, configured = ?, executed = ? '
                      'WHERE code = (SELECT code FROM unit LIMIT 1 OFFSET ?)',
                      (unit[1], unit[2], last_run, int(unit[0]) - 1,))
        else:
            c.execute('UPDATE unit SET increment = ?, configured = ?, executed = ? WHERE code = ?',
                      (unit[1], unit[2], last_run, unit[0],))

        if test:
            self.conn.rollback()
            return

        self.conn.commit()
