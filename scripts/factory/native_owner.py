"""Restricted native fixture process records its identity before exec."""
import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--spec',type=Path,required=True);a=p.parse_args();s=json.loads(a.spec.read_text())
pid=os.getpid();fields=Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
record={'pid':pid,'startTicks':fields[19],'bootId':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'uid':os.geteuid(),'exe':s['binary']}
fd=os.open(s['record'],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:json.dump(record,f)
os.chdir(s['cwd']);os.execve(s['binary'],[s['binary'],'app-server','--stdio'],s['env'])
