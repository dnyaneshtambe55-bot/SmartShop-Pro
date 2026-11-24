
import os, sqlite3, secrets, io, json, re
from pathlib import Path
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from urllib.parse import urlencode
from flask import Flask, request, session, redirect, render_template, flash, send_file, abort
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from xml.sax.saxutils import escape
from openpyxl import Workbook
import qrcode

BASE=Path(__file__).resolve().parent
DB=BASE/'shop.db'
app=Flask(__name__)
key=BASE/'.secret'
if not key.exists(): key.write_text(secrets.token_hex(32))
app.secret_key=key.read_text()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
def connect():
    db=sqlite3.connect(DB); db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db
def init():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT, cost REAL, price REAL, stock INTEGER, barcode TEXT UNIQUE, gst INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, phone TEXT);
        CREATE TABLE IF NOT EXISTS bills(id INTEGER PRIMARY KEY, customer TEXT, date TEXT, subtotal INTEGER, tax INTEGER, total INTEGER, profit INTEGER, payment TEXT);
        CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, bill_id INTEGER REFERENCES bills(id), name TEXT, qty INTEGER, price INTEGER, gst INTEGER, tax INTEGER, total INTEGER);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
        """)
        cols={r[1] for r in db.execute('PRAGMA table_info(products)')}
        if 'barcode' not in cols: db.execute('ALTER TABLE products ADD COLUMN barcode TEXT')
        if 'gst' not in cols: db.execute('ALTER TABLE products ADD COLUMN gst INTEGER DEFAULT 0')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS barcode_unique ON products(barcode)')
def backup():
    folder=BASE/'backups'; folder.mkdir(exist_ok=True)
    dest=folder/(datetime.now().strftime('%Y-%m-%d')+'.db')
    with connect() as source, sqlite3.connect(dest) as target: source.backup(target)
def money(x):
    d=Decimal(str(x))
    if not d.is_finite() or d<0 or d>100000000: raise ValueError('Invalid amount')
    return int((d*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
def csrf():
    if 'csrf' not in session: session['csrf']=secrets.token_hex(24)
    return session['csrf']
app.jinja_env.globals.update(csrf=csrf, rupees=lambda n:f'{n/100:,.2f}')
@app.before_request
def protect():
    if request.method=='POST' and not secrets.compare_digest(session.get('csrf','!'),request.form.get('csrf','')):
        abort(400)
def login_required(fn):
    @wraps(fn)
    def wrapper(*a,**kw):
        if not session.get('uid'): return redirect('/login')
        return fn(*a,**kw)
    return wrapper
@app.route('/login',methods=['GET','POST'])
@app.route('/register',methods=['GET','POST'])
def auth():
    with connect() as db:
        exists=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        register=request.path=='/register'
        if register and exists: return redirect('/login')
        if request.method=='POST':
            u=request.form['username'].strip(); p=request.form['password']
            if register:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]: abort(403)
                if len(u)<3 or len(p)<10: flash('Username: 3+ characters; password: 10+ characters.')
                else:
                    db.execute('INSERT INTO users(username,password) VALUES (?,?)',(u,generate_password_hash(p)))
                    return redirect('/login')
            else:
                row=db.execute('SELECT * FROM users WHERE username=?',(u,)).fetchone()
                if row and check_password_hash(row['password'],p):
                    session.clear(); session['uid']=row['id']; backup(); return redirect('/')
                flash('Invalid credentials')
    return render_template('auth.html',register=register,exists=exists)
@app.post('/logout')
def logout():
    session.clear(); return redirect('/login')
@app.route('/')
@login_required
def home():
    with connect() as db:
        products=[dict(r) for r in db.execute('SELECT * FROM products ORDER BY name')]
        customers=db.execute('SELECT * FROM customers ORDER BY name').fetchall()
        bills=db.execute('SELECT * FROM bills ORDER BY id DESC').fetchall()
        daily=db.execute("SELECT substr(date,1,10) label,SUM(total) value FROM bills GROUP BY label ORDER BY label DESC LIMIT 14").fetchall()
        monthly=db.execute("SELECT substr(date,1,7) label,SUM(total) value FROM bills GROUP BY label ORDER BY label DESC LIMIT 12").fetchall()
        settings=dict(db.execute('SELECT key,value FROM settings').fetchall())
    return render_template('index.html',products=products,customers=customers,bills=bills,daily=daily,monthly=monthly,settings=settings)
@app.post('/product')
@login_required
def product():
    try:
        f=request.form; name=f['name'].strip(); stock=int(f['stock']); gst=int(f['gst'])
        if not name or stock<0 or gst not in (0,5,12,18,28): raise ValueError('Invalid product')
        values=(name,money(f['cost'])/100,money(f['price'])/100,stock,f['barcode'].strip() or None,gst)
        with connect() as db:
            if f.get('id'): db.execute('UPDATE products SET name=?,cost=?,price=?,stock=?,barcode=?,gst=? WHERE id=?',(*values,int(f['id'])))
            else: db.execute('INSERT INTO products(name,cost,price,stock,barcode,gst) VALUES (?,?,?,?,?,?)',values)
        backup(); flash('Product saved')
    except (ValueError,sqlite3.IntegrityError,ArithmeticError): flash('Invalid product or duplicate barcode')
    return redirect('/#inventory')
@app.post('/delete/<int:id>')
@login_required
def delete(id):
    with connect() as db: db.execute('DELETE FROM products WHERE id=?',(id,))
    backup(); return redirect('/#inventory')
@app.post('/customer')
@login_required
def customer():
    name=request.form['name'].strip()
    if name:
        with connect() as db: db.execute('INSERT INTO customers(name,phone) VALUES (?,?)',(name,request.form['phone']))
        backup()
    return redirect('/#customers')
@app.post('/settings')
@login_required
def settings():
    upi=request.form['upi'].strip()
    if upi and not re.fullmatch(r'[\w.\-]+@[\w.\-]+',upi): flash('Invalid UPI ID'); return redirect('/#settings')
    with connect() as db:
        for k in ('shop','upi','gstin'):
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(k,request.form[k].strip()))
    backup(); return redirect('/#settings')
@app.post('/sell')
@login_required
def sell():
    try:
        cart=json.loads(request.form['cart'])
        if not isinstance(cart,dict) or not cart or len(cart)>100: raise ValueError('Empty or invalid cart')
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            lines=[]; sub=tax=profit=0
            for id,qty in cart.items():
                if type(qty) is not int or qty<=0: raise ValueError('Invalid quantity')
                p=db.execute('SELECT * FROM products WHERE id=?',(int(id),)).fetchone()
                if not p or qty>p['stock']: raise ValueError('Insufficient stock')
                price=money(p['price']); base=price*qty
                t=int((Decimal(base)*p['gst']/100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
                sub+=base; tax+=t; profit+=base-money(p['cost'])*qty
                lines.append((p['name'],qty,price,p['gst'],t,base+t))
                db.execute('UPDATE products SET stock=stock-? WHERE id=?',(qty,int(id)))
            c=db.execute('SELECT name FROM customers WHERE id=?',(request.form.get('customer'),)).fetchone()
            payment=request.form['payment']
            if payment not in ('Cash','UPI pending'): raise ValueError('Invalid payment')
            id=db.execute('INSERT INTO bills(customer,date,subtotal,tax,total,profit,payment) VALUES (?,?,?,?,?,?,?)',(c[0] if c else 'Walk-in',datetime.now().isoformat(timespec='seconds'),sub,tax,sub+tax,profit,payment)).lastrowid
            db.executemany('INSERT INTO items(bill_id,name,qty,price,gst,tax,total) VALUES (?,?,?,?,?,?,?)',[(id,*l) for l in lines])
        backup(); return redirect(f'/bill/{id}')
    except (ValueError,TypeError,ArithmeticError) as e: flash(str(e)); return redirect('/#billing')
def bill_data(id):
    with connect() as db:
        b=db.execute('SELECT * FROM bills WHERE id=?',(id,)).fetchone()
        if not b: abort(404)
        return b,db.execute('SELECT * FROM items WHERE bill_id=?',(id,)).fetchall(),dict(db.execute('SELECT key,value FROM settings').fetchall())
@app.get('/bill/<int:id>')
@login_required
def bill(id):
    b,items,s=bill_data(id); return render_template('bill.html',b=b,items=items,s=s)
@app.get('/qr/<int:id>')
@login_required
def qr(id):
    b,_,s=bill_data(id)
    if not s.get('upi'): abort(404)
    uri='upi://pay?'+urlencode(dict(pa=s['upi'],pn=s.get('shop','SmartShop'),am=f"{b['total']/100:.2f}",cu='INR',tn=f'Bill {id}'))
    out=io.BytesIO(); qrcode.make(uri).save(out,format='PNG'); out.seek(0)
    return send_file(out,mimetype='image/png')
@app.post('/paid/<int:id>')
@login_required
def paid(id):
    with connect() as db: db.execute("UPDATE bills SET payment='UPI manually verified' WHERE id=? AND payment='UPI pending'",(id,))
    backup(); return redirect(f'/bill/{id}')
@app.get('/pdf/<int:id>')
@login_required
def pdf(id):
    b,items,s=bill_data(id); out=io.BytesIO(); styles=getSampleStyleSheet()
    rows=[['Product','Qty','Unit Rs.','GST %','Total Rs.']]+[[Paragraph(escape(i['name']),styles['Normal']),i['qty'],f"{i['price']/100:.2f}",i['gst'],f"{i['total']/100:.2f}"] for i in items]
    story=[Paragraph(escape(s.get('shop','SmartShop')),styles['Title']),Paragraph(f"Bill {id} | {escape(b['date'])} | {escape(b['customer'])}",styles['Normal']),Paragraph('GSTIN: '+escape(s.get('gstin','Not configured')),styles['Normal']),Spacer(1,12),Table(rows,colWidths=[180,40,75,55,85],repeatRows=1,style=[('GRID',(0,0),(-1,-1),0.5,'#aaaaaa')]),Spacer(1,12),Paragraph(f"Subtotal Rs. {b['subtotal']/100:.2f} | GST Rs. {b['tax']/100:.2f} | Total Rs. {b['total']/100:.2f}",styles['Normal']),Paragraph(escape(b['payment']),styles['Normal'])]
    SimpleDocTemplate(out).build(story); out.seek(0); return send_file(out,download_name=f'bill-{id}.pdf',as_attachment=True,mimetype='application/pdf')
@app.get('/excel')
@login_required
def excel():
    w=Workbook(); sheet=w.active; sheet.title='Sales'; sheet.append(['Bill','Customer','Date','Subtotal INR','GST INR','Total INR','Gross profit INR','Payment'])
    with connect() as db:
        for b in db.execute('SELECT * FROM bills'):
            safe=lambda v: "'"+v if v.startswith(('=','+','-','@')) else v
            sheet.append([b['id'],safe(b['customer']),b['date'],*[b[k]/100 for k in ('subtotal','tax','total','profit')],b['payment']])
    out=io.BytesIO(); w.save(out); out.seek(0); return send_file(out,download_name='sales.xlsx',as_attachment=True)
@app.get('/backup')
@login_required
def download_backup():
    backup(); return send_file(BASE/'backups'/(datetime.now().strftime('%Y-%m-%d')+'.db'),as_attachment=True)
init()
if __name__=='__main__': app.run()
#python app.py