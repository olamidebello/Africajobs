"""Africa Jobs — dependency-free, tenant-scoped field-service marketplace."""
import os, json, sqlite3, secrets, hashlib, hmac, time, csv, io
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from pathlib import Path

ROOT=Path(__file__).parent
DB=os.environ.get('AFRICAJOBS_DB',str(ROOT/'data'/'jobs.db'))
ROLES=['buyer_admin','manager','dispatcher','provider','company_admin','technician','partner']
MODULES=['projects','templates','talent_pools','credentials','insurance','assessments','shipments','disputes','support','notifications','integrations','plans','content','dispatch_rules','approval_rules','settings']
ADMIN_MODULES={'plans','content','integrations','settings'}
BUYERS={'buyer_admin','manager','dispatcher','partner'}

def connect():
 c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); return c

def password(p,s=None):
 s=s or secrets.token_hex(16)
 return s+':'+hashlib.pbkdf2_hmac('sha256',p.encode(),s.encode(),310000).hex()

def valid(p,d):
 return hmac.compare_digest(password(p,d.split(':')[0]),d)

def init():
 Path(DB).parent.mkdir(parents=True,exist_ok=True)
 with connect() as c:
  c.executescript('''
CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY,name TEXT NOT NULL,currency TEXT NOT NULL DEFAULT 'NGN',active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,password TEXT NOT NULL,name TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,super INTEGER NOT NULL DEFAULT 0,profile TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS memberships(tenant TEXT REFERENCES tenants(id),user TEXT REFERENCES users(id),role TEXT NOT NULL,PRIMARY KEY(tenant,user));
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user TEXT REFERENCES users(id),csrf TEXT NOT NULL,expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,tenant TEXT REFERENCES tenants(id),title TEXT NOT NULL,description TEXT,location TEXT,schedule TEXT,amount INTEGER NOT NULL,currency TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'draft',provider TEXT REFERENCES users(id),creator TEXT REFERENCES users(id),tasks TEXT NOT NULL DEFAULT '[]',notes TEXT NOT NULL DEFAULT '',created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY,job TEXT REFERENCES jobs(id),user TEXT REFERENCES users(id),amount INTEGER,note TEXT,status TEXT NOT NULL DEFAULT 'requested',UNIQUE(job,user));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,tenant TEXT,job TEXT,user TEXT,action TEXT,payload TEXT,created REAL);
CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,tenant TEXT NOT NULL,module TEXT NOT NULL,owner TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,tenant TEXT,job TEXT,user TEXT,body TEXT,created REAL);
CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY,tenant TEXT,user TEXT,job TEXT,amount INTEGER,currency TEXT,kind TEXT,created REAL,UNIQUE(job,kind));
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,tenant TEXT,job TEXT,user TEXT,name TEXT,mime TEXT,body BLOB,created REAL);
CREATE TABLE IF NOT EXISTS login_limits(key TEXT PRIMARY KEY,attempts INTEGER,until REAL);
''')
  email=os.environ.get('AFRICAJOBS_ADMIN_EMAIL'); pw=os.environ.get('AFRICAJOBS_ADMIN_PASSWORD')
  if email and pw and not c.execute('SELECT 1 FROM users WHERE super=1').fetchone():
   if len(pw)<14: raise ValueError('Admin password must contain at least 14 characters')
   c.execute('INSERT INTO users(id,email,password,name,super) VALUES(?,?,?,?,1)',(secrets.token_hex(12),email.lower(),password(pw),'Platform Administrator'))

def audit(c,u,t,action,job=None,payload=None):
 c.execute('INSERT INTO events(tenant,job,user,action,payload,created) VALUES(?,?,?,?,?,?)',(t,job,u,action,json.dumps(payload or {}),time.time()))

def clean(row): return dict(row) if row else None

class Error(Exception):
 def __init__(self,message,status=400): self.message,self.status=message,status

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send(self,data,status=200,mime='application/json'):
  b=json.dumps(data).encode() if mime=='application/json' else data
  self.send_response(status)
  self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(b)))
  self.send_header('X-Content-Type-Options','nosniff'); self.send_header('X-Frame-Options','DENY')
  self.send_header('Referrer-Policy','same-origin')
  self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'")
  self.send_header('Cache-Control','no-store')
  if getattr(self,'cookie',None): self.send_header('Set-Cookie',self.cookie)
  self.end_headers(); self.wfile.write(b)
 def do_GET(self): self.handle_request()
 def do_POST(self): self.handle_request()
 def do_PATCH(self): self.handle_request()
 def do_DELETE(self): self.handle_request()
 def handle_request(self):
  try:
   self.cookie=None; path=self.path.split('?')[0]
   if not path.startswith('/api/'):
    assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
    if path not in assets: raise Error('Not found',404)
    p=ROOT/'static'/assets[path]
    return self.send(p.read_bytes(),mime={'html':'text/html; charset=utf-8','js':'text/javascript','css':'text/css'}[p.suffix[1:]])
   length=int(self.headers.get('Content-Length','0'))
   if length>8_000_000: raise Error('Maximum request size is 8 MB',413)
   self.data=json.loads(self.rfile.read(length) or b'{}')
   if not isinstance(self.data,dict): raise Error('Expected an object')
   with connect() as c:
    self.c=c
    result=self.api(path)
   if isinstance(result,tuple): return self.send(*result)
   self.send(result)
  except Error as e: self.send({'error':e.message},e.status)
  except (ValueError,KeyError,TypeError,json.JSONDecodeError): self.send({'error':'Invalid request data'},400)
  except sqlite3.IntegrityError: self.send({'error':'Duplicate record or invalid reference'},409)
  except Exception:
   import traceback; traceback.print_exc(); self.send({'error':'Internal server error'},500)
 def require(self,roles):
  if not self.u['super'] and self.role not in roles: raise Error('Permission denied',403)
 def job(self,j):
  row=self.c.execute('SELECT * FROM jobs WHERE id=? AND tenant=?',(j,self.t)).fetchone()
  if not row: raise Error('Work order not found',404)
  if self.role in {'provider','technician','company_admin'} and row['provider']!=self.u['id'] and row['status']!='published':
   if not self.c.execute("SELECT 1 FROM requests WHERE job=? AND user=? AND status='routed'",(j,self.u['id'])).fetchone(): raise Error('Work order not accessible',403)
  return dict(row)
 def api(self,p):
  c=self.c; d=self.data; method=self.command
  if p=='/api/health': return {'status':'ok','service':'Africa Jobs'}
  if p=='/api/register' and method=='POST':
   email=d['email'].strip().lower(); pw=d['password']; name=d['name'].strip(); org=d['company'].strip()
   if '@' not in email or len(pw)<12 or not name or not org: raise Error('Name, company, valid email and a 12-character password are required')
   uid,tid=secrets.token_hex(12),secrets.token_hex(12)
   c.execute('INSERT INTO users(id,email,password,name) VALUES(?,?,?,?)',(uid,email,password(pw),name))
   c.execute('INSERT INTO tenants(id,name,currency) VALUES(?,?,?)',(tid,org,d.get('currency','NGN') if d.get('currency','NGN') in ['NGN','USD','KES','GHS','ZAR','EUR','GBP'] else 'NGN'))
   role='provider' if d.get('account')=='provider' else 'buyer_admin'
   c.execute('INSERT INTO memberships VALUES(?,?,?)',(tid,uid,role)); return {'registered':True}
  if p=='/api/login' and method=='POST':
   key=self.client_address[0]+':'+d.get('email','').lower()
   limit=c.execute('SELECT * FROM login_limits WHERE key=?',(key,)).fetchone()
   if limit and limit['attempts']>=8 and limit['until']>time.time(): raise Error('Too many attempts. Try again in 15 minutes.',429)
   u=c.execute('SELECT * FROM users WHERE email=? AND active=1',(d.get('email','').lower(),)).fetchone()
   if not u or not valid(d.get('password',''),u['password']):
    c.execute('INSERT INTO login_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET attempts=CASE WHEN until < ? THEN 1 ELSE attempts+1 END,until=excluded.until',(key,time.time()+900,time.time())); c.commit(); raise Error('Invalid login',401)
   c.execute('DELETE FROM login_limits WHERE key=?',(key,))
   token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(24)
   c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],csrf,time.time()+43200))
   self.cookie='africajobs='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'+('; Secure' if os.environ.get('AFRICAJOBS_SECURE_COOKIES')=='1' else '')
   return {'csrf':csrf}
  cookies=SimpleCookie(); cookies.load(self.headers.get('Cookie',''))
  token=cookies.get('africajobs'); token=token.value if token else ''
  sess=c.execute('SELECT * FROM sessions WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
  self.u=c.execute('SELECT id,email,name,super,profile FROM users WHERE id=? AND active=1',(sess['user'],)).fetchone() if sess else None
  if not self.u: raise Error('Please sign in',401)
  if method!='GET' and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),sess['csrf']): raise Error('Invalid security token',403)
  memberships=[dict(x) for x in c.execute('SELECT t.*,m.role FROM tenants t JOIN memberships m ON m.tenant=t.id WHERE m.user=? AND t.active=1',(self.u['id'],))]
  if self.u['super']: memberships=[dict(x)|{'role':'super_admin'} for x in c.execute('SELECT * FROM tenants')]
  self.t=self.headers.get('X-Tenant-ID') or (memberships[0]['id'] if memberships else '')
  m=next((x for x in memberships if x['id']==self.t),None); self.role=m['role'] if m else ''
  if p=='/api/me': return {'user':dict(self.u),'tenants':memberships,'tenant':self.t,'role':self.role,'csrf':sess['csrf']}
  if p=='/api/logout' and method=='POST':
   c.execute('DELETE FROM sessions WHERE token=?',(sess['token'],)); self.cookie='africajobs=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'; return {'ok':True}
  if p=='/api/profile' and method=='PATCH':
   c.execute('UPDATE users SET profile=? WHERE id=?',(json.dumps(d),self.u['id'])); return {'ok':True}
  if p=='/api/tenants' and method=='POST':
   self.require(set()); tid=secrets.token_hex(12)
   c.execute('INSERT INTO tenants(id,name,currency) VALUES(?,?,?)',(tid,d['name'],d.get('currency','NGN'))); audit(c,self.u['id'],tid,'tenant.created'); return {'id':tid}
  if not m: raise Error('Select an authorized organization',403)
  if p=='/api/users':
   self.require({'buyer_admin','company_admin'})
   if method=='GET': return [dict(x) for x in c.execute('SELECT u.id,u.name,u.email,u.active,m.role FROM users u JOIN memberships m ON m.user=u.id WHERE m.tenant=?',(self.t,))]
   if method=='POST':
    role=d['role']
    if role not in ROLES or (self.role=='company_admin' and role not in {'technician','provider'}): raise Error('Role not allowed',403)
    uid=secrets.token_hex(12); existing=c.execute('SELECT id FROM users WHERE email=?',(d['email'].lower(),)).fetchone()
    if existing: raise Error('Existing users must join using their own account; membership cannot be granted by email alone',409)
    if len(d['password'])<12: raise Error('Password must be 12 characters')
    c.execute('INSERT INTO users(id,email,password,name) VALUES(?,?,?,?)',(uid,d['email'].lower(),password(d['password']),d['name']))
    c.execute('INSERT INTO memberships VALUES(?,?,?)',(self.t,uid,role)); audit(c,self.u['id'],self.t,'user.created',payload={'user':uid,'role':role}); return {'id':uid}
  if p=='/api/audit':
   self.require({'buyer_admin'}); return [dict(x) for x in c.execute('SELECT * FROM events WHERE tenant=? ORDER BY id DESC LIMIT 500',(self.t,))]
  if p=='/api/ledger':
   sql='SELECT * FROM ledger WHERE tenant=?'; args=[self.t]
   if self.role not in BUYERS and not self.u['super']: sql+=' AND user=?'; args.append(self.u['id'])
   return {'entries':[dict(x) for x in c.execute(sql+' ORDER BY created DESC',args)],'payment_gateway':'not_configured','payouts_enabled':False}
  if p=='/api/providers':
   self.require(BUYERS|{'company_admin'})
   return [dict(x) for x in c.execute("SELECT u.id,u.name,u.profile,m.role FROM users u JOIN memberships m ON m.user=u.id WHERE m.tenant=? AND m.role IN ('provider','technician','company_admin') AND u.active=1",(self.t,))]
  if p=='/api/jobs':
   if method=='GET':
    rows=[dict(x) for x in c.execute('SELECT * FROM jobs WHERE tenant=? ORDER BY created DESC',(self.t,))]
    if self.role not in BUYERS and not self.u['super']:
     routed={x[0] for x in c.execute("SELECT job FROM requests WHERE user=? AND status='routed'",(self.u['id'],))}
     rows=[x for x in rows if x['status']=='published' or x['provider']==self.u['id'] or x['id'] in routed]
    return rows
   self.require(BUYERS)
   if method=='POST':
    amount=int(d['amount'])
    if amount<=0 or not d['title'].strip(): raise Error('Title and positive amount required')
    jid=secrets.token_hex(8)
    c.execute('INSERT INTO jobs(id,tenant,title,description,location,schedule,amount,currency,creator,tasks,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(jid,self.t,d['title'],d.get('description',''),d.get('location',''),d.get('schedule',''),amount,m['currency'],self.u['id'],json.dumps(d.get('tasks',[])),time.time()))
    audit(c,self.u['id'],self.t,'job.created',jid); return {'id':jid}
  if p.startswith('/api/jobs/'):
   parts=p.split('/'); jid=parts[3]; j=self.job(jid); action=parts[4] if len(parts)>4 else ''
   if method=='GET':
    if action=='files': return [dict(x) for x in c.execute('SELECT id,name,mime,created FROM files WHERE job=? AND tenant=?',(jid,self.t))]
    if action=='messages': return [dict(x) for x in c.execute('SELECT m.*,u.name FROM messages m JOIN users u ON u.id=m.user WHERE job=? AND tenant=? ORDER BY created',(jid,self.t))]
    if action=='requests':
     self.require(BUYERS); return [dict(x) for x in c.execute('SELECT r.*,u.name FROM requests r JOIN users u ON u.id=r.user WHERE job=?',(jid,))]
    return j
   if method!='POST': raise Error('Method not allowed',405)
   if action=='messages':
    if j['provider']!=self.u['id']: self.require(BUYERS)
    body=d.get('body','').strip()
    if not body or len(body)>10000: raise Error('Message must contain 1–10000 characters')
    c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(secrets.token_hex(12),self.t,jid,self.u['id'],body,time.time())); return {'ok':True}
   if action=='files':
    import base64
    if j['provider']!=self.u['id']: self.require(BUYERS)
    body=base64.b64decode(d['body'],validate=True)
    if len(body)>5_000_000: raise Error('File limit is 5 MB')
    c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?)',(secrets.token_hex(12),self.t,jid,self.u['id'],Path(d['name']).name,d.get('mime','application/octet-stream'),body,time.time())); audit(c,self.u['id'],self.t,'file.uploaded',jid); return {'ok':True}
   if action in {'request','counteroffer'}:
    self.require({'provider','technician','company_admin'})
    if j['status']!='published': raise Error('Job is not accepting requests',409)
    amount=int(d.get('amount',j['amount']))
    if amount<=0: raise Error('Amount must be positive')
    c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?) ON CONFLICT(job,user) DO UPDATE SET amount=excluded.amount,note=excluded.note,status=excluded.status',(secrets.token_hex(12),jid,self.u['id'],amount,d.get('note',''),'requested'))
   elif action=='route':
    self.require(BUYERS)
    if j['status'] not in {'draft','published','routed'}: raise Error('Cannot route this work',409)
    provider=self.provider(d['provider'])
    c.execute("INSERT INTO requests VALUES(?,?,?,?,?,'routed') ON CONFLICT(job,user) DO UPDATE SET status='routed'",(secrets.token_hex(12),jid,provider,j['amount'],''))
    c.execute("UPDATE jobs SET status='routed' WHERE id=?",(jid,))
   elif action=='accept':
    r=c.execute("SELECT * FROM requests WHERE job=? AND user=? AND status='routed'",(jid,self.u['id'])).fetchone()
    if not r or j['status'] not in {'routed','published'}: raise Error('No available routed offer',409)
    c.execute("UPDATE jobs SET status='assigned',provider=? WHERE id=?",(self.u['id'],jid))
   elif action=='decline':
    c.execute("UPDATE requests SET status='declined' WHERE job=? AND user=? AND status='routed'",(jid,self.u['id']))
   elif action=='assign':
    self.require(BUYERS)
    if j['status'] not in {'published','routed'}: raise Error('Publish or route before assigning',409)
    provider=self.provider(d['provider'])
    req=c.execute("SELECT * FROM requests WHERE job=? AND user=? AND status='requested'",(jid,provider)).fetchone()
    if not req: raise Error('Provider must request or accept the work before assignment',409)
    c.execute("UPDATE jobs SET status='assigned',provider=?,amount=? WHERE id=?",(provider,req['amount'],jid))
   elif action in {'publish','cancel','approve','reopen'}:
    self.require(BUYERS)
    allowed={'publish':{'draft','routed'},'cancel':{'draft','published','routed','assigned'},'approve':{'done'},'reopen':{'done'}}
    if j['status'] not in allowed[action]: raise Error('Invalid lifecycle transition',409)
    state={'publish':'published','cancel':'cancelled','approve':'approved','reopen':'checked_in'}[action]
    if action=='approve':
     # Accrual only: no imaginary wallet funding, external transfer, or paid status.
     c.execute('INSERT INTO ledger VALUES(?,?,?,?,?,?,?,?)',(secrets.token_hex(12),self.t,j['provider'],jid,j['amount'],j['currency'],'payable',time.time()))
    c.execute('UPDATE jobs SET status=? WHERE id=?',(state,jid))
   elif action in {'on_my_way','check_in','check_out','complete','tasks'}:
    if j['provider']!=self.u['id']: raise Error('Only the assigned provider can perform this action',403)
    if action=='tasks':
     if j['status'] not in {'assigned','on_my_way','checked_in','checked_out'}: raise Error('Work is not active',409)
     tasks=json.loads(j['tasks']); idx=int(d['index']); tasks[idx]['done']=bool(d['done']); c.execute('UPDATE jobs SET tasks=? WHERE id=?',(json.dumps(tasks),jid))
    else:
     allowed={'on_my_way':{'assigned'},'check_in':{'assigned','on_my_way'},'check_out':{'checked_in'},'complete':{'checked_out'}}
     if j['status'] not in allowed[action]: raise Error('Invalid lifecycle transition',409)
     if action=='complete' and any(not x.get('done') for x in json.loads(j['tasks'])): raise Error('Complete required tasks first',409)
     state={'on_my_way':'on_my_way','check_in':'checked_in','check_out':'checked_out','complete':'done'}[action]
     c.execute('UPDATE jobs SET status=?,notes=? WHERE id=?',(state,d.get('notes',j['notes']),jid))
   else: raise Error('Unknown action',404)
   audit(c,self.u['id'],self.t,action,jid); return {'ok':True}
  if p.startswith('/api/files/') and method=='GET':
   f=c.execute('SELECT * FROM files WHERE id=? AND tenant=?',(p.split('/')[-1],self.t)).fetchone()
   if not f: raise Error('File not found',404)
   self.job(f['job']); return (f['body'],200,'application/octet-stream')
  if p.startswith('/api/records/'):
   parts=p.split('/'); module=parts[3]
   if module not in MODULES: raise Error('Unknown module',404)
   if module in ADMIN_MODULES: self.require(set())
   private=self.role not in BUYERS and not self.u['super']
   if method=='GET':
    sql='SELECT * FROM records WHERE tenant=? AND module=?'; args=[self.t,module]
    if private: sql+=' AND owner=?'; args.append(self.u['id'])
    return [dict(x)|{'data':json.loads(x['data'])} for x in c.execute(sql+' ORDER BY created DESC',args)]
   if method=='POST':
    if module in {'projects','templates','talent_pools','dispatch_rules','approval_rules','assessments'}: self.require(BUYERS|{'company_admin'})
    if not str(d.get('name','')).strip(): raise Error('Name is required')
    rid=secrets.token_hex(12)
    c.execute('INSERT INTO records VALUES(?,?,?,?,?,?)',(rid,self.t,module,self.u['id'],json.dumps(d),time.time())); audit(c,self.u['id'],self.t,module+'.created'); return {'id':rid}
   if method in {'PATCH','DELETE'} and len(parts)==5:
    row=c.execute('SELECT * FROM records WHERE id=? AND tenant=? AND module=?',(parts[4],self.t,module)).fetchone()
    if not row: raise Error('Record not found',404)
    if row['owner']!=self.u['id']: self.require({'buyer_admin'})
    if method=='DELETE': c.execute('DELETE FROM records WHERE id=?',(row['id'],))
    else: c.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(json.loads(row['data'])|d),row['id']))
    audit(c,self.u['id'],self.t,module+'.'+method.lower()); return {'ok':True}
  raise Error('Not found',404)
 def provider(self,uid):
  r=self.c.execute("SELECT 1 FROM memberships m JOIN users u ON u.id=m.user WHERE m.tenant=? AND m.user=? AND m.role IN ('provider','technician','company_admin') AND u.active=1",(self.t,uid)).fetchone()
  if not r: raise Error('Eligible provider must belong to this tenant',403)
  return uid

if __name__=='__main__':
 init(); port=int(os.environ.get('PORT','8080')); print('Africa Jobs listening on',port,flush=True)
 ThreadingHTTPServer((os.environ.get('HOST','127.0.0.1'),port),Handler).serve_forever()
