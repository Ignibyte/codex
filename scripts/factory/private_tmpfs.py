"""Root-only private mount-namespace lifetime owner for a tiny ENOSPC fixture."""
import argparse,json,os,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--mountpoint',type=Path,required=True);a=p.parse_args()
if os.geteuid()!=0:raise RuntimeError('root fixture namespace required')
path=a.mountpoint
if not path.is_absolute() or path.name!='tiny' or not path.parent.name.startswith('cf-fleet-'):raise RuntimeError('unexpected fixture mount')
subprocess.run(['mount','-t','tmpfs','-o','size=1048576,mode=0711,uid=65534,gid=65534,nodev,nosuid,noexec','factory-owned-fixture',str(path)],check=True)
try:
 print(json.dumps({'pid':os.getpid(),'mountNamespace':os.readlink('/proc/self/ns/mnt'),'device':path.stat().st_dev,'bytes':os.statvfs(path).f_blocks*os.statvfs(path).f_frsize}),flush=True)
 sys.stdin.readline()
finally:subprocess.run(['umount',str(path)],check=True)
