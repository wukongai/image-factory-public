from pathlib import Path
import importlib.util,json,subprocess,sys,tempfile,unittest,uuid
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from image_factory import cli
from image_factory.errors import ImageFactoryError
spec=importlib.util.spec_from_file_location('accept_image',ROOT/'scripts/accept_image.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
class PublicTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.out=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def compile(self,template='product-main-v1',extra=()):
  self.compile_out=self.out/('compiled-'+uuid.uuid4().hex)
  args=cli.build_parser().parse_args(['--title','需求实操课','--point','梳理用户需求','--template',template,'--scene','product_main' if template=='product-main-v1' else 'marketing_poster','--output-dir',str(self.compile_out),*extra]);cli.run(args)
  return self.compile_out/'image-asset-manifest.json'
 def test_main_and_detail(self):
  for t,size in [('product-main-v1','1024x1024'),('product-detail-v1','1024x1536'),('marketing-poster-conversion-v1','1024x1536')]:
   m=json.loads(self.compile(t).read_text());self.assertEqual(m['render_handoff']['size'],size)
   self.assertEqual(m['expected_outputs']['final_image'],m['expected_outputs']['raw_image'])
 def test_receipt_review_hash_and_conflict(self):
  m=self.compile();im=self.out/'test.png';dst=self.out/'product/main.png';Image.new('RGB',(1024,1024),'white').save(im)
  r=a.accept(m,im,dst);self.assertEqual(r['status'],'awaiting_visual_review')
  self.assertEqual(a.accept(m,im,dst,True)['status'],'ready')
  self.assertEqual(a.accept(m,im,dst)['status'],'ready')
  Image.new('RGB',(1024,1024),'red').save(im)
  with self.assertRaises(ValueError):a.accept(m,im,dst)
 def test_bad_dimensions_corrupt_and_symlink(self):
  m=self.compile();im=self.out/'test.png';dst=self.out/'product/main.png';Image.new('RGB',(100,100)).save(im)
  with self.assertRaises(ValueError):a.accept(m,im,dst)
  im.write_bytes(b'broken')
  with self.assertRaises(OSError):a.accept(m,im,dst)
  im.unlink();im.symlink_to(m)
  with self.assertRaises(ValueError):a.accept(m,im,dst)
 def test_larger_same_ratio_and_exact_policy(self):
  m=self.compile();im=self.out/"large.png";dst=self.out/"product/large.png";Image.new("RGB",(1254,1254)).save(im)
  self.assertEqual(a.accept(m,im,dst)["dimensions"],[1254,1254])
  with self.assertRaises(ValueError):a.accept(m,im,dst,exact=True)
 def test_existing_compile_directory_is_unchanged(self):
  m=self.compile();parent=m.parent;before={p.name:p.read_bytes() for p in parent.iterdir() if p.is_file()}
  args=cli.build_parser().parse_args(['--title','Changed','--template','product-main-v1','--scene','product_main','--output-dir',str(parent)])
  with self.assertRaises(ImageFactoryError):cli.run(args)
  after={p.name:p.read_bytes() for p in parent.iterdir() if p.is_file()};self.assertEqual(before,after)
 def test_canvas_preserves_integrated_text(self):
  m=json.loads(self.compile(extra=['--canvas-preset','square_1_1']).read_text())
  self.assertFalse(m['postprocess'].get('required'));self.assertEqual(m['size'],'2048x2048')
  self.assertEqual(m['expected_outputs']['final_image'],m['expected_outputs']['raw_image'])
  with self.assertRaises(ImageFactoryError):self.compile(extra=['--adaptive-layout'])
 def test_no_ip_scene(self):
  with self.assertRaises(ImageFactoryError):self.compile(extra=['--scene','ip_article_illustration'])
 def test_remix_requires_analysis(self):
  with self.assertRaises(ImageFactoryError):self.compile('poster-reference-remix-v1')
 def test_custom_palette_and_reference_remix(self):
  p=self.out/'analysis.json';p.write_text(json.dumps({'layout_prompt':'顶部标题中部主视觉','style_prompt':'暖白蓝色','reusable_rules':['留白'],'forbidden_to_copy':['品牌']}))
  m=self.compile('poster-reference-remix-v1',['--reference-analysis',str(p),'--set','style.visual_style=蓝白极简'])
  prompt=Path(json.loads(m.read_text())['render_handoff']['prompt_file']).read_text();self.assertIn('蓝白极简',prompt)
 def test_batch(self):
  p=subprocess.run([sys.executable,str(ROOT/'scripts/generate_image_batch.py'),'--batch',str(ROOT/'examples/product-set.json'),'--output-dir',str(self.out/'batch')],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr)
  self.assertEqual(len(list((self.out/'batch').rglob('image-asset-manifest.json'))),2)
 def test_public_scan(self):
  p=subprocess.run([sys.executable,str(ROOT/'scripts/check_public_release.py')],capture_output=True,text=True)
  self.assertEqual(p.returncode,0,p.stdout+p.stderr)
if __name__=='__main__':unittest.main()
