"""One final supervised qualification across CF-006–010 on a stable source tree."""
import argparse,json,platform,subprocess,sys,traceback
from pathlib import Path
from package_control import ROOT,digest,verify,source_identity

def main():
 p=argparse.ArgumentParser()
 for name in ['package','fault-cli','fault-bridge','old-cli','canary-a','canary-b','evidence']:p.add_argument('--'+name,type=Path,required=True)
 a=p.parse_args()
 for key,value in vars(a).items():setattr(a,key,value.resolve())
 a.evidence.mkdir(mode=0o700,parents=True,exist_ok=False);results=[];error=None;before=source_identity()
 cli=a.package/'bin/codex';bridge=a.package/'bin/codex-factory-bridge'
 tests=[
 ('reconciliation','qualify_reconciliation.py',['--binary',a.fault_bridge,'--cli',a.fault_cli]),
 ('durability','qualify_durability.py',['--binary',a.fault_bridge]),
 ('cancellation','qualify_cancellation.py',['--binary',bridge,'--cli',cli]),
 ('default-fences','qualify_default_fences.py',['--binary',bridge,'--cli',cli,'--old-cli',a.old_cli]),
 ('native','qualify_native.py',['--binary',bridge,'--cli',cli]),
 ('native-controls','qualify_native_controls.py',['--binary',bridge,'--cli',cli]),
 ('credentials','qualify_credentials.py',['--binary',bridge]),
 ('enrollment','qualify_enrollment.py',['--binary',bridge]),
 ('recovery','qualify_recovery.py',['--binary',bridge]),
 ('contract','qualify_contract.py',['--binary',bridge]),
 ('sessions','qualify_sessions.py',['--package',a.package]),
 ('package-regression','qualify_packages.py',['--package-a',a.canary_a,'--package-b',a.canary_b]),
 ('delivery','qualify_delivery.py',['--package',a.package,'--old-package',a.canary_a]),
 ]
 try:
  verify(a.package,digest(a.package/'factory-package.json'))
  for name,script,extra in tests:
   cmd=list(map(str,[sys.executable,ROOT/'scripts/factory'/script,*extra,'--evidence',a.evidence/name]))
   record={'name':name,'command':cmd,'cwd':str(ROOT),'status':'running'};results.append(record)
   (a.evidence/'progress.json').write_text(json.dumps(results,indent=2)+'\n')
   with (a.evidence/(name+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
   record.update(exit_code=r.returncode,status='passed' if r.returncode==0 else 'failed')
   (a.evidence/'progress.json').write_text(json.dumps(results,indent=2)+'\n');print(name,r.returncode,flush=True)
   if r.returncode:raise RuntimeError('qualification failed: '+name)
  assert before==source_identity(),'source changed during qualification'
 except Exception:error=traceback.format_exc()
 record={'passed':error is None,'exit_code':0 if error is None else 1,'error':error,'results':results,
  'source':before,'sourceUnchanged':before==source_identity(),'platform':platform.platform(),'command':sys.argv,
  'binaries':{str(path):digest(path) for path in [cli,bridge,a.fault_cli,a.fault_bridge]},'runnerSha256':digest(__file__)}
 (a.evidence/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps({'passed':record['passed'],'lanes':len(results),'error':error},indent=2));return record['exit_code']
if __name__=='__main__':raise SystemExit(main())
