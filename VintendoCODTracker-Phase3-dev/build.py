"""Reproducible Windows one-file build. Run inside the pinned build environment."""
import os,importlib.util
from pathlib import Path
import PyInstaller.__main__
ROOT=Path(__file__).resolve().parent
WORK=Path(os.environ.get('VINTENDO_BUILD_DIR',str(ROOT/'build-work')))
DIST=Path(os.environ.get('VINTENDO_DIST_DIR',str(ROOT)))
WORK.mkdir(parents=True,exist_ok=True)
DIST.mkdir(parents=True,exist_ok=True)
# The host's media tooling adds old MSVC DLLs to PATH. Bundle Qt's matching
# runtime explicitly so dependency discovery cannot choose those unrelated DLLs.
os.environ['PATH']=os.pathsep.join(p for p in os.environ.get('PATH','').split(os.pathsep) if 'codex-runtimes' not in p.lower())
qt=Path(importlib.util.find_spec('PySide6').origin).parent
runtime=[]
for name in ('MSVCP140.dll','MSVCP140_1.dll','MSVCP140_2.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
    if (qt/name).exists():runtime+=['--add-binary',str(qt/name)+os.pathsep+'.']
arguments=[
    str(ROOT/'launcher.py'),'--noconfirm','--onefile','--windowed','--name','VintendoCODTracker',
    '--distpath',str(DIST),'--workpath',str(WORK/'pyinstaller'),'--specpath',str(WORK),
    '--icon',str(ROOT/'tracker.ico'),
    '--paths',str(ROOT/'core'/'legacy'),'--paths',str(ROOT/'tests'),
    '--hidden-import','vintendo_tracker','--hidden-import','achievements','--hidden-import','graphics',
    '--hidden-import','codmunity_share','--hidden-import','test_core','--hidden-import','test_ui',
    '--hidden-import','test_squad_queue',
    '--hidden-import','test_local_bridge','--hidden-import','test_extension',
    '--hidden-import','codmunity_stats','--hidden-import','test_codmunity_stats',
    '--collect-all','rapidocr_onnxruntime','--collect-all','dxcam','--collect-all','playwright',
    '--collect-binaries','onnxruntime','--collect-data','onnxruntime',
    '--add-data',str(ROOT/'core'/'legacy')+os.pathsep+'core/legacy',
    '--add-data',str(ROOT/'tracker.ico')+os.pathsep+'.',
]+runtime
for mod in ('torch','torchvision','torchaudio','transformers','diffusers','gradio','gradio_client','lightning','lightning_fabric','pytorch_lightning','jax','jaxlib','tensorflow','deepspeed','accelerate','datasets','tokenizers','sentencepiece','huggingface_hub'):
    arguments+=['--exclude-module',mod]
logo=ROOT/'assets'/'vintendo-cod-tracker-logo.png'
if logo.is_file():arguments+=['--add-data',str(logo)+os.pathsep+'assets']
PyInstaller.__main__.run(arguments)
