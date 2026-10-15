#!/usr/bin/env python3
"""Check only release files; never print matched secret values."""
from pathlib import Path
import re,sys
ROOT=Path(__file__).resolve().parents[1]
# Concatenation prevents this rule set matching its own literals.
PRIVATE=['ai'+'xiao','艾'+'笑','/Users/'+'aim5','my-'+'mediacenter','ai'+'xiaoai.cloud']
PATTERNS=[re.compile(r'\bsk-[A-Za-z0-9_-]{20,}'),re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'),re.compile(r'BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY'),re.compile(r'\bAKIA[0-9A-Z]{16}\b')]
def scan(root):
    issues=[]
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        if any(x in {'.git','.venv','__pycache__','.skill-engineering'} for x in rel.parts):continue
        if p.is_symlink():issues.append((str(rel),'symlink'));continue
        if not p.is_file():continue
        if p.name.startswith('.env') and p.name!='.env.example':issues.append((str(rel),'private filename'));continue
        if p.suffix.lower() in {'.pem','.key','.p12'}:issues.append((str(rel),'key file'));continue
        if p.suffix.lower() in {'.png','.jpg','.jpeg','.webp'}:
            if not str(rel).startswith('examples/rendered/'):issues.append((str(rel),'unapproved media'))
            continue
        try:s=p.read_text()
        except UnicodeDecodeError:issues.append((str(rel),'unreviewed binary'));continue
        if any(x.lower() in s.lower() for x in PRIVATE):issues.append((str(rel),'personal marker'))
        if any(x.search(s) for x in PATTERNS):issues.append((str(rel),'credential pattern'))
    return issues
if __name__=='__main__':
    issues=scan(ROOT)
    for f,kind in issues:print(f'{f}: {kind}')
    print(f'Public release scan: {len(issues)} issue(s); values never printed.')
    raise SystemExit(bool(issues))
