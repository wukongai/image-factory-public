#!/usr/bin/env python3
"""Validate a rendered asset and publish a local, hash-bound delivery receipt."""
from pathlib import Path
import argparse, hashlib, json, os, sys, tempfile
from PIL import Image, UnidentifiedImageError

def accept(manifest_path, image_path, destination, approved=False, exact=False):
    manifest_path, image_path, destination = map(Path,(manifest_path,image_path,destination))
    m=json.loads(manifest_path.read_text())
    if m.get('public_edition') != 'image-factory-public/v1':
        raise ValueError('Use a public-edition image manifest.')
    if image_path.is_symlink() or destination.is_symlink():
        raise ValueError('Symlink image paths are not accepted.')
    with Image.open(image_path) as im:
        im.verify()
    with Image.open(image_path) as im:
        w,h=im.size
        ew,eh=map(int,m['render_handoff']['size'].split('x'))
        if (exact and (w,h)!=(ew,eh)) or w < ew or h < eh or abs(w/h-ew/eh)/(ew/eh)>0.01:
            raise ValueError(f'Expected {ew}x{eh}, got {w}x{h}; regenerate, do not distort.')
        if im.format!='PNG' or destination.suffix.lower()!='.png':
            raise ValueError('Expected a PNG input and .png destination.')
    data=image_path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    destination=destination.absolute();receipt=destination.with_suffix('.receipt.json')
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:
        raise ValueError('Destination has different content; use a new version path.')
    if receipt.is_symlink():raise ValueError('Symlink receipt not accepted.')
    previous=json.loads(receipt.read_text()) if receipt.exists() else {}
    if previous and previous.get('sha256')!=digest:
        raise ValueError('Receipt belongs to another image; use a new version path.')
    if previous and previous.get('id')!=m['render_handoff']['id']:
        raise ValueError('Receipt belongs to another image task.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    if not destination.exists():
        with destination.open('xb') as f:f.write(data)
    status='ready' if approved or previous.get('status')=='ready' else 'awaiting_visual_review'
    result={'schema_version':'image-delivery/v1','id':m['render_handoff']['id'],'image_path':str(destination),
            'dimensions':[w,h],'sha256':digest,'status':status}
    fd,tmp=tempfile.mkstemp(prefix='.receipt-',dir=destination.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(result,f,ensure_ascii=False,indent=2)
        os.replace(tmp,receipt)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True);p.add_argument('--image',required=True)
    p.add_argument('--destination',required=True);p.add_argument('--visual-approved',action='store_true');p.add_argument('--exact-size',action='store_true')
    a=p.parse_args()
    try:print(json.dumps(accept(a.manifest,a.image,a.destination,a.visual_approved,a.exact_size),ensure_ascii=False))
    except (ValueError,OSError,KeyError,UnidentifiedImageError) as e:
        print('Image not accepted: '+str(e),file=sys.stderr);return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
