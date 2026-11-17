"""Reproducible Windows one-file build. Run inside the pinned build environment."""
import os,importlib.util,shutil
from pathlib import Path
import PyInstaller.__main__
ROOT=Path(__file__).resolve().parent
WORK=Path(os.environ.get('VINTENDO_BUILD_DIR',str(ROOT/'build-work')))
DIST=Path(os.environ.get('VINTENDO_DIST_DIR',str(ROOT)))
WORK.mkdir(parents=True,exist_ok=True)
DIST.mkdir(parents=True,exist_ok=True)
# charset-normalizer ships optional mypyc extensions in the global site-packages
# used by this machine. Package its equivalent Python sources instead, so the
# one-file bootloader does not need to extract the hashed native helper module.
charset_spec=importlib.util.find_spec('charset_normalizer')
if charset_spec is None or not charset_spec.origin:raise RuntimeError('charset-normalizer is required for the build.')
pure_root=(WORK/'pure-python-deps').resolve();pure_package=pure_root/'charset_normalizer'
if pure_package.exists():
    if pure_package.resolve().parent!=pure_root:raise RuntimeError('Refusing to replace an unexpected pure-Python package path.')
    shutil.rmtree(pure_package)
pure_package.mkdir(parents=True,exist_ok=True)
for source in Path(charset_spec.origin).parent.rglob('*'):
    if source.is_file() and source.suffix=='.py':
        target=pure_package/source.relative_to(Path(charset_spec.origin).parent)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
# The host's media tooling adds old MSVC DLLs to PATH. Bundle Qt's matching
# runtime explicitly so dependency discovery cannot choose those unrelated DLLs.
os.environ['PATH']=os.pathsep.join(
    p for p in os.environ.get('PATH','').split(os.pathsep)
    if 'codex-runtimes' not in p.lower() and 'openai.codexprimaryruntime' not in p.lower())
qt=Path(importlib.util.find_spec('PySide6').origin).parent
runtime=[]
for name in ('MSVCP140.dll','MSVCP140_1.dll','MSVCP140_2.dll','VCRUNTIME140.dll','VCRUNTIME140_1.dll'):
    if (qt/name).exists():runtime+=['--add-binary',str(qt/name)+os.pathsep+'.']
arguments=[
    str(ROOT/'launcher.py'),'--noconfirm','--onefile','--windowed','--name','VintendoCODTracker',
    '--distpath',str(DIST),'--workpath',str(WORK/'pyinstaller'),'--specpath',str(WORK),
    '--paths',str(pure_root),'--additional-hooks-dir',str(ROOT/'build_support'/'hooks'),
    '--icon',str(ROOT/'tracker.ico'),
    '--paths',str(ROOT/'core'/'legacy'),'--paths',str(ROOT/'tests'),
    '--hidden-import','vintendo_tracker','--hidden-import','achievements','--hidden-import','graphics',
    '--hidden-import','codmunity_share','--hidden-import','test_core','--hidden-import','test_ui',
    '--hidden-import','test_squad_queue',
    '--hidden-import','codmunity_stats','--hidden-import','test_codmunity_stats',
    '--hidden-import','PySide6.QtWebEngineWidgets','--hidden-import','PySide6.QtWebEngineCore',
    '--collect-all','rapidocr_onnxruntime','--collect-all','dxcam','--collect-all','playwright',
    '--collect-binaries','onnxruntime','--collect-data','onnxruntime',
    '--add-data',str(ROOT/'core'/'legacy')+os.pathsep+'core/legacy',
    '--add-data',str(ROOT/'tracker.ico')+os.pathsep+'.',
]+runtime
for mod in ('torch','torchvision','torchaudio','transformers','diffusers','gradio','gradio_client','lightning','lightning_fabric','pytorch_lightning','jax','jaxlib','tensorflow','deepspeed','accelerate','datasets','tokenizers','sentencepiece','huggingface_hub'):
    arguments+=['--exclude-module',mod]
# Every image the UI loads from the assets folder must ship inside the
# one-file exe, or installed copies silently fall back to text titles.
bundled_assets=[('vintendo-cod-tracker-logo.png','assets'),('loading.png','assets'),('loading_animated_transparent.webp','assets')]+[('banners/'+name,'assets/banners') for name in ('dashboard.png','scoreboard.png','match-history.png','roster.png','ai-studio.png','screenshots.png','browser.png','settings.png')]
for asset_name,asset_dest in bundled_assets:
    asset_path=ROOT/'assets'/asset_name
    if asset_path.is_file():arguments+=['--add-data',str(asset_path)+os.pathsep+asset_dest]
PyInstaller.__main__.run(arguments)
