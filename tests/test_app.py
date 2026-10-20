import unittest, tempfile, os, threading, json, urllib.request, urllib.error, sys
sys.path.insert(0,os.path.dirname(os.path.dirname(__file__)))
import app

class Marketplace(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory(); app.DB=cls.temp.name+'/test.db';app.init()
  cls.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);cls.url='http://127.0.0.1:'+str(cls.server.server_port)
  threading.Thread(target=cls.server.serve_forever,daemon=True).start()
 @classmethod
 def tearDownClass(cls): cls.server.shutdown();cls.server.server_close();cls.temp.cleanup()
 def call(self,path,method='GET',data=None,auth=None,tenant=None):
  headers={'Content-Type':'application/json'}
  if auth: headers.update({'Cookie':auth['cookie'],'X-CSRF-Token':auth['csrf'],'X-Tenant-ID':tenant or auth['tenant']})
  req=urllib.request.Request(self.url+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
  try:
   with urllib.request.urlopen(req) as r:return r.status,json.load(r),r.headers
  except urllib.error.HTTPError as e:return e.code,json.load(e),e.headers
 def register(self,email,account='buyer'):
  self.assertEqual(self.call('register','POST',{'name':email,'email':email,'password':'safe-password-123','company':email,'account':account})[0],200)
  _,r,h=self.call('login','POST',{'email':email,'password':'safe-password-123'})
  a={'cookie':h['Set-Cookie'].split(';')[0],'csrf':r['csrf'],'tenant':''}
  _,m,_=self.call('me',auth=a);a['tenant']=m['tenant'];return a,m
 def test_lifecycle_and_tenant_security(self):
  buyer,bm=self.register('buyer@example.com'); stranger,sm=self.register('stranger@example.com')
  _,p,_=self.call('users','POST',{'name':'Technician','email':'tech@example.com','password':'safe-password-123','role':'provider'},buyer)
  _,r,h=self.call('login','POST',{'email':'tech@example.com','password':'safe-password-123'})
  tech={'cookie':h['Set-Cookie'].split(';')[0],'csrf':r['csrf'],'tenant':buyer['tenant']}
  _,j,_=self.call('jobs','POST',{'title':'Install network','amount':50000,'tasks':[{'text':'Test connection','done':False}]},buyer);base='jobs/'+j['id']+'/'
  self.assertEqual(self.call('jobs',auth=stranger,tenant=buyer['tenant'])[0],403)
  self.assertEqual(self.call(base+'publish','POST',{},tech)[0],403)
  self.assertEqual(self.call(base+'publish','POST',{},buyer)[0],200)
  self.assertEqual(self.call(base+'request','POST',{'amount':55000},tech)[0],200)
  self.assertEqual(self.call(base+'assign','POST',{'provider':p['id']},buyer)[0],200)
  self.assertEqual(self.call(base+'check_in','POST',{},tech)[0],200)
  self.assertEqual(self.call(base+'check_out','POST',{},tech)[0],200)
  self.assertEqual(self.call(base+'complete','POST',{},tech)[0],409)
  self.assertEqual(self.call(base+'tasks','POST',{'index':0,'done':True},tech)[0],200)
  self.assertEqual(self.call(base+'complete','POST',{'notes':'Network verified'},tech)[0],200)
  self.assertEqual(self.call(base+'approve','POST',{},tech)[0],403)
  self.assertEqual(self.call(base+'approve','POST',{},buyer)[0],200)
  self.assertEqual(self.call(base+'approve','POST',{},buyer)[0],409)
  ledger=self.call('ledger',auth=tech)[1];self.assertEqual(ledger['entries'][0]['amount'],55000);self.assertFalse(ledger['payouts_enabled'])
  self.assertEqual(self.call('jobs/'+j['id'],auth=stranger)[0],404)
  self.assertEqual(self.call('records/plans','POST',{'name':'Fraud'},tech)[0],403)
  self.assertEqual(self.call('jobs','POST',{'title':'CSRF','amount':1},{**buyer,'csrf':'bad'})[0],403)
 def test_records_private(self):
  a,_=self.register('first@example.com','provider');b,_=self.register('second@example.com','provider')
  self.call('records/credentials','POST',{'name':'Network certificate'},a)
  self.assertEqual(len(self.call('records/credentials',auth=b)[1]),0)
  self.assertEqual(self.call('records/credentials',auth=b,tenant=a['tenant'])[0],403)

if __name__=='__main__':unittest.main()
