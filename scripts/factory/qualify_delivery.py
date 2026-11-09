"""Final local package/source pins, epoch refusal, update containment and archive."""
import argparse,json,os,shutil,subprocess,tarfile,tempfile,traceback
from pathlib import Path
from package_control import ROOT,digest,verify,source_identity,select_package,selected

def main():
 p=argparse.ArgumentParser();p.add_argument('--package',type=Path,required=True);p.add_argument('--old-package',type=Path,required=True);p.add_argument('--evidence',type=Path,required=True)
 p.add_argument('--expected-storage-epoch',type=int,choices=[2,3,4,5,6,7,8,9,10,11],default=2)
 p.add_argument('--archive-name',default='factory-local-cf010.tar.gz');a=p.parse_args()
 assert Path(a.archive_name).name==a.archive_name
 a.package=a.package.resolve();a.old_package=a.old_package.resolve();a.evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
 checks=[];commands=[];error=None;archive=None
 try:
  pin=digest(a.package/'factory-package.json');manifest=verify(a.package,pin)
  assert manifest['storageEpoch']==a.expected_storage_epoch and manifest['bridgeStorageEpoch']==(7 if a.expected_storage_epoch>=11 else 6 if a.expected_storage_epoch>=9 else 5 if a.expected_storage_epoch>=6 else 4 if a.expected_storage_epoch==5 else 3) and manifest['nativeFactoryStorageEpoch']==2
  if a.expected_storage_epoch>=3:assert manifest['recordingStorageEpoch']==1
  if a.expected_storage_epoch>=4:assert manifest['observationStorageEpoch']==(2 if a.expected_storage_epoch>=5 else 1)
  if a.expected_storage_epoch>=7:assert manifest['foremanStorageEpoch']==(3 if a.expected_storage_epoch>=10 else 2 if a.expected_storage_epoch>=8 else 1)
  if a.expected_storage_epoch>=10:assert manifest['managerStorageEpoch']==1
  checks.append('complete_package_with_compiled_component_epochs')
  assert manifest['source']==source_identity()
  checks.append('complete_source_content_modes_and_diff_fresh')
  stock=json.loads((ROOT/'.artifacts/factory/cf-001/inventory.json').read_text())['helpers']['codex']
  assert digest(stock['path'])==stock['sha256'];checks.append('installed_stock_cli_unchanged')
  with tempfile.TemporaryDirectory(prefix='delivery-home-',dir=a.evidence) as home:
   env={'PATH':os.environ['PATH'],'HOME':home,'CODEX_HOME':home}
   def run(cmd,expected=0):
    r=subprocess.run(list(map(str,cmd)),capture_output=True,text=True,env=env,timeout=20)
    commands.append({'command':list(map(str,cmd)),'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
    assert r.returncode==expected,commands[-1];return r
   identity=json.loads(run([a.package/'bin/codex','factory-info']).stdout)
   assert identity['storageEpoch']==a.expected_storage_epoch and identity['bridgeStorageEpoch']==(7 if a.expected_storage_epoch>=11 else 6 if a.expected_storage_epoch>=9 else 5 if a.expected_storage_epoch>=6 else 4 if a.expected_storage_epoch==5 else 3) and identity['nativeFactoryStorageEpoch']==2 and not identity['publicUpdatesAllowed']
   if a.expected_storage_epoch>=3:
    assert identity['recordingStorageEpoch']==1
    bridge_help=run([a.package/'bin/codex-factory-bridge','--help']).stdout
    assert all(command in bridge_help for command in ['collect-recording','recording-outbox','flush-recording'])
   if a.expected_storage_epoch>=4:
    assert identity['observationStorageEpoch']==(2 if a.expected_storage_epoch>=5 else 1)
    assert all(command in bridge_help for command in ['observe-work','report-presence','prune-observation'])
   if a.expected_storage_epoch>=5:
    assert identity['networkTransportProtocol']==1
    assert all(command in bridge_help for command in ['admit-host','rotate-host','revoke-host','bootstrap-remote','connect-host','remote-entry'])
   for argv in [['update'],['app-server','daemon','update'],['app-server','daemon','update','--from-cli','--yes'],['app-server','daemon','pid-update-loop']]:
    r=run([a.package/'bin/codex',*argv],1);assert 'factory_update_pinned' in r.stderr
   run([a.package/'bin/codex-code-mode-host','--help']);run([a.package/'codex-resources/bwrap','--version'])
   checks.append('native_updates_refused_and_required_helpers_start')
   export=a.evidence/'schema';run([a.package/'bin/codex','app-server','generate-json-schema','--experimental','--out',export])
   combined=''.join(path.read_text() for path in export.rglob('*.json'))
   assert all(method in combined for method in ['factory/peers','factory/inbox','factory/send','thread/factoryMessage','factory/status','factory/cancel'])
   checks.append('actual_cli_exports_all_six_experimental_methods')
  selector=a.evidence/'selection';select_package(selector,a.package,pin);before=(selector/'selection.json').read_bytes()
  try:select_package(selector,a.old_package,digest(a.old_package/'factory-package.json'));raise AssertionError('unsafe rollback accepted')
  except ValueError as exc:assert 'factory_rollback_storage_incompatible' in str(exc),str(exc)
  assert (selector/'selection.json').read_bytes()==before and selected(selector)['package']==str(a.package)
  checks.append('incompatible_epoch_selection_refused_without_changing_good_pin')
  if a.expected_storage_epoch>=3:
   old_selector=a.evidence/'previous-selection'
   select_package(old_selector,a.old_package,digest(a.old_package/'factory-package.json'))
   previous=(old_selector/'selection.json').read_bytes()
   try:select_package(old_selector,a.package,pin);raise AssertionError('profile epoch upgrade accepted')
   except ValueError as exc:assert 'factory_rollback_storage_incompatible' in str(exc),str(exc)
   assert (old_selector/'selection.json').read_bytes()==previous
   checks.append('previous_profile_epoch_crossing_refused_in_both_directions')
  with tempfile.TemporaryDirectory(prefix='package-negative-',dir=a.evidence) as scratch:
   shadow=Path(scratch)/'package';shutil.copytree(a.package,shadow)
   wrong=json.loads((shadow/'factory-package.json').read_text());wrong['storageEpoch']=1
   (shadow/'factory-package.json').write_text(json.dumps(wrong))
   try:select_package(selector,shadow,digest(shadow/'factory-package.json'));raise AssertionError('wrong compiled epoch accepted')
   except ValueError as exc:assert 'factory_compiled_storage_epoch_mismatch' in str(exc),str(exc)
   assert (selector/'selection.json').read_bytes()==before
   shutil.copy2(a.package/'factory-package.json',shadow/'factory-package.json');(shadow/'bin/codex-factory-bridge').unlink()
   try:select_package(selector,shadow,pin);raise AssertionError('missing bridge accepted')
   except ValueError as exc:assert 'factory_helper_mismatch' in str(exc),str(exc)
   assert (selector/'selection.json').read_bytes()==before
  checks.append('compiled_epoch_and_missing_bridge_refused_without_pin_mutation')
  archive=a.evidence/a.archive_name
  with tarfile.open(archive,'w:gz') as tar:tar.add(a.package,arcname='factory')
  with tempfile.TemporaryDirectory(prefix='package-extract-',dir=a.evidence) as scratch:
   with tarfile.open(archive,'r:gz') as tar:tar.extractall(scratch,filter='data')
   assert verify(Path(scratch)/'factory',pin)==manifest
  checks.append('archive_roundtrip_preserves_inventory_modes_and_external_pin')
 except Exception:error=traceback.format_exc()
 record={'passed':error is None,'exit_code':0 if error is None else 1,'checks':checks,'error':error,'commands':commands,
   'manifestSha256':digest(a.package/'factory-package.json'),'archive':str(archive) if archive else None,
   'archiveSha256':digest(archive) if archive and archive.exists() else None,'testSha256':digest(__file__)}
 (a.evidence/'result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record,indent=2));return record['exit_code']
if __name__=='__main__':raise SystemExit(main())
