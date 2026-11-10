"""Offline verification entry point included in packaged builds."""
import json,os,unittest,time,sys
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import engine as E
def run():
    E.DATA.mkdir(parents=True,exist_ok=True)
    progress=E.DATA/'verification-tests.txt'
    progress.write_text('Verification started: '+time.strftime('%Y-%m-%d %H:%M:%S')+'\n',encoding='utf-8')
    tests_dir=Path(__file__).resolve().parent/'tests'
    if str(tests_dir) not in sys.path:sys.path.insert(0,str(tests_dir))
    import test_core,test_ui,test_squad_queue
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromModule(test_core),unittest.defaultTestLoader.loadTestsFromModule(test_ui),unittest.defaultTestLoader.loadTestsFromModule(test_squad_queue)])
    with progress.open('a',encoding='utf-8',buffering=1) as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    E.atomic_json(E.DATA/'verification-result.json',{'tests':result.testsRun,'passed':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors),'live_discord_tested':False,'live_meta_tested':False})
    return 0 if result.wasSuccessful() else 1
