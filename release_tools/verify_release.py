"""Check exact manifest and fail closed on unexpected files; no network calls."""
import hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 data=json.loads((ROOT/'RELEASE_MANIFEST.json').read_text(encoding='utf-8'));expected={r['path']:r for r in data['files']};issues=[]
 all_paths=list(ROOT.rglob('*'))
 if any(p.is_symlink() for p in all_paths):issues.append('Symlink in release tree')
 if len(expected)!=len(data['files']):issues.append('Duplicate manifest path')
 actual={p.relative_to(ROOT).as_posix():p for p in all_paths if p.is_file()}
 if set(actual)-{'RELEASE_MANIFEST.json'}!=set(expected):issues.append('Unexpected or missing release files')
 for rel,row in expected.items():
  p=ROOT/rel
  if Path(rel).is_absolute() or '..' in Path(rel).parts:issues.append('Unsafe manifest path');continue
  if p.is_symlink() or not p.is_file():issues.append('Not a regular manifest file: '+rel);continue
  raw=p.read_bytes()
  if len(raw)!=row['bytes'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:issues.append('Hash mismatch: '+rel)
  if any(part in {'inputs','verification','prototypes','versions','__pycache__','.git','node_modules'} for part in Path(rel).parts):issues.append('Excluded directory: '+rel)
  if p.suffix.lower() in {'.db','.sqlite','.sqlite3','.wal','.shm','.log','.mp4','.png','.jpg','.pem','.key'}:issues.append('Prohibited data/credential/binary file: '+rel)
  text=raw.decode('utf-8',errors='replace')
  if re.search(r'/'+r'Users/|/home/[A-Za-z]|[A-Z]:\\Users\\',text):issues.append('Personal path pattern: '+rel)
  if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text):issues.append('Private key header: '+rel)
  # Exact, reviewed fake transport / malformed-response literals only.
  allowed_test_digests={
   'tests/test_session_openai_key.py':{'b7b490a964c699d273611e63dd34080cae17e8abffe3e8d29d30ba1e98ccab9b','c474699da86b1757c86837c77491857a862089877ea9f4ad0c367360f22a6d19'},
   'tests/test_openai_request_compatibility.py':{'03d06fb288c02168d09f1e23875e9f84c2c76901b59640651bfa3511317b1c95'}}
  for token in re.findall(r'(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})',text):
   if hashlib.sha256(token.encode()).hexdigest() not in allowed_test_digests.get(rel,set()):issues.append('Credential-like literal: '+rel)

 print(json.dumps({'status':'FAIL' if issues else 'PASS','files':len(expected),'issues':sorted(set(issues)),'scope':'Hash/allowlist/pattern checks; not a guarantee against every secret format'},indent=2))
 return bool(issues)
if __name__=='__main__':sys.exit(main())
