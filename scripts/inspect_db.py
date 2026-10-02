import sqlite3, sys, os
db_path = os.environ.get('DB_PATH', r'C:\000_Storage\Code\000_Sandbox\LAN_attendant_check\app\data\checknv.db')
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect(db_path)
print('DB path:', db_path)
print('EMPLOYEES:')
for r in conn.execute('SELECT id, code, name FROM employees').fetchall():
    print(' ', r)
print('ATTENDANCE:')
for r in conn.execute('SELECT id, employee_id, date, checkin_time, checkout_time FROM attendance').fetchall():
    print(' ', r)
print('TIMELOG:')
for r in conn.execute('SELECT id, employee_id, date, action, time_value FROM time_log').fetchall():
    print(' ', r)
print('ADMINS:')
for r in conn.execute('SELECT id FROM admins').fetchall():
    print(' ', r)