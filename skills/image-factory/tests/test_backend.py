from pathlib import Path
import base64, contextlib, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import image_backend as b

class BackendTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.prompt=self.root/'prompt.txt';self.prompt.write_text('制作商品海报')
  self.manifest=self.root/'manifest.json';self.dst=self.root/'output.png'
  self.m={'public_edition':'image-factory-public/v1','render_handoff':{'id':'test','prompt_file':str(self.prompt),'size':'1024x1024','assets':[]}}
  self.save()
 def tearDown(self):self.tmp.cleanup()
 def save(self):self.manifest.write_text(json.dumps(self.m))
 def plan(self):return b.prepare(self.manifest,self.dst,b.DEFAULT_BASE,b.DEFAULT_MODEL)
 def response(self):
  buf=io.BytesIO();Image.new('RGB',(1024,1024),'white').save(buf,format='PNG')
  return json.dumps({'data':[{'b64_json':base64.b64encode(buf.getvalue()).decode()}]}).encode()
 def test_generation_receipt_and_private_key_boundary(self):
  plan=self.plan();seen=[]
  def send(req):seen.append(req);return self.response()
  result=b.render(plan,self.manifest,self.dst,'synthetic-test-key',send)
  self.assertEqual(result['status'],'awaiting_visual_review')
  self.assertEqual(seen[0].get_header('Authorization'),'Bearer synthetic-test-key')
  self.assertEqual(json.loads(seen[0].data)['n'],1)
  self.assertNotIn('synthetic-test-key',json.dumps(plan['preview']))
  self.assertNotIn('synthetic-test-key',self.dst.with_suffix('.receipt.json').read_text())
  self.assertFalse(self.dst.with_suffix('.render-lock').exists())
  with self.assertRaises(b.BackendError):self.plan()
 def test_reference_multipart_and_missing(self):
  ref=self.root/'ref.png';Image.new('RGB',(64,64)).save(ref)
  self.m['render_handoff']['assets']=[{'path':str(ref),'required':True}];self.save()
  p=self.plan();self.assertTrue(p['url'].endswith('/images/edits'))
  self.assertIn(b'name="image[]"',p['body']);self.assertIn(ref.read_bytes(),p['body'])
  ref.unlink()
  with self.assertRaises(b.BackendError):self.plan()
 def test_bad_endpoint(self):
  for url in ['http://example.com/v1','https://user:pass@example.com','https://example.com?a=1','https://example.com/#secret']:
   with self.assertRaises(b.BackendError):b.endpoint(url)
 def test_missing_key_never_sends(self):
  with self.assertRaises(b.BackendError):b.render(self.plan(),self.manifest,self.dst,None,lambda req:self.fail())
  self.assertFalse(self.dst.with_suffix('.render-lock').exists())
 def test_failure_lock_prevents_duplicate_charge(self):
  plan=self.plan();calls=[]
  def send(req):calls.append(req);return b'{}'
  with self.assertRaises(b.BackendError):b.render(plan,self.manifest,self.dst,'test',send)
  with self.assertRaises(b.BackendError):b.render(plan,self.manifest,self.dst,'test',send)
  self.assertEqual(len(calls),1);self.assertFalse(self.dst.exists())
 def test_invalid_image_preserved_but_not_ready(self):
  response=json.dumps({'data':[{'b64_json':base64.b64encode(b'not-an-image').decode()}]}).encode()
  with self.assertRaises(b.BackendError):b.render(self.plan(),self.manifest,self.dst,'test',lambda req:response)
  self.assertEqual(self.dst.with_suffix('.unverified.bin').read_bytes(),b'not-an-image')
  self.assertFalse(self.dst.with_suffix('.receipt.json').exists())
 def test_retained_output_blocks_new_request(self):
  self.dst.with_suffix('.unverified.bin').write_bytes(b'previous-paid-output')
  with self.assertRaises(b.BackendError):self.plan()
 def test_decoder_failure_preserves_paid_data(self):
  with patch.object(b,'accept',side_effect=Image.DecompressionBombError()):
   with self.assertRaises(b.BackendError):b.render(self.plan(),self.manifest,self.dst,'test',lambda req:self.response())
  self.assertTrue(self.dst.with_suffix('.unverified.bin').exists())
  self.assertFalse(self.dst.exists())
 def test_preview_no_key_no_network(self):
  with patch.object(sys,'argv',['image_backend.py','render','--manifest',str(self.manifest),'--output',str(self.dst)]),patch('urllib.request.build_opener',side_effect=AssertionError()),contextlib.redirect_stdout(io.StringIO()) as out:
   self.assertEqual(b.main(),0)
  self.assertFalse(json.loads(out.getvalue())['network_called']);self.assertFalse(self.dst.exists())
 def test_paid_flag_required(self):
  with patch.object(sys,'argv',['image_backend.py','render','--manifest',str(self.manifest),'--output',str(self.dst),'--execute']),contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()) as err:
   self.assertEqual(b.main(),1)
  self.assertIn('--allow-paid',err.getvalue());self.assertFalse(self.dst.exists())
 def test_doctor_does_not_expose_environment_key(self):
  with patch.dict(os.environ,{'IMAGE_FACTORY_API_KEY':'synthetic-test-key'}),patch.object(sys,'argv',['image_backend.py','doctor']),contextlib.redirect_stdout(io.StringIO()) as out:
   self.assertEqual(b.main(),0)
  self.assertNotIn('synthetic-test-key',out.getvalue());self.assertTrue(json.loads(out.getvalue())['key_in_environment'])
 def test_redirect_and_http_errors_redacted(self):
  self.assertIsNone(b.NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.example'))
  import urllib.error
  err=urllib.error.HTTPError('https://example.com',401,'synthetic-test-key',{},None)
  with patch('urllib.request.build_opener') as opener:
   opener.return_value.open.side_effect=err
   with self.assertRaises(b.BackendError) as ctx:b.transport(None)
  self.assertNotIn('synthetic-test-key',str(ctx.exception));self.assertIn('401',str(ctx.exception))
if __name__=='__main__':unittest.main()
