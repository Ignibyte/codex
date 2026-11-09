"""Retain local source overlay and executable identity without credentials/state."""
import argparse,hashlib,json,platform,subprocess,tarfile
from pathlib import Path
from package_control import source_identity,digest,ROOT

def retain(evidence,binaries):
 evidence=Path(evidence);identity=source_identity();diff=subprocess.check_output(['git','diff','--binary','HEAD'],cwd=ROOT)
 (evidence/'source-identity.json').write_text(json.dumps(identity,indent=2)+'\n')
 (evidence/'source.diff').write_bytes(diff)
 untracked=subprocess.check_output(['git','ls-files','--others','--exclude-standard','-z'],cwd=ROOT).decode().split('\0')
 paths=[p for p in untracked if p and p.startswith(('codex-rs/','scripts/factory/','docs/factory/')) and '__pycache__' not in p]
 with tarfile.open(evidence/'source-overlay.tar.gz','w:gz') as tar:
  tar.add(evidence/'source.diff',arcname='source.diff')
  for p in paths:tar.add(ROOT/p,arcname=p,recursive=False)
 record={'baseCommit':identity['commit'],'overlaySha256':digest(evidence/'source-overlay.tar.gz'),
  'manifestSha256':digest(evidence/'source-identity.json'),'platform':platform.platform(),'rust':'1.99.1',
  'binaries':{str(Path(p).resolve()):digest(p) for p in binaries},'reconstruct':'Checkout baseCommit, apply source.diff, extract new source paths; retain file modes. No credentials are included.'}
 (evidence/'source-reproduction.json').write_text(json.dumps(record,indent=2)+'\n')
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--binary',action='append',default=[]);a=p.parse_args();retain(a.evidence,a.binary)
