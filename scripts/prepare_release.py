"""Copy only the reviewed application source to an existing clean Git checkout."""
import argparse
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOP=['README.md','DEPLOYMENT.md','.gitignore','.dockerignore','Dockerfile','compose.yaml','启动统一分析工作台.cmd']
def main():
    p=argparse.ArgumentParser();p.add_argument('destination',type=Path);a=p.parse_args()
    destination=a.destination.resolve()
    if not (destination/'.git').exists() or destination==ROOT:p.error('需要独立的 Git 发布检出目录')
    files=[ROOT/name for name in TOP]
    for folder in ('workbench','.github','scripts'):
        for path in (ROOT/folder).rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to(ROOT)
            if any(part in ('data','__pycache__','.git') for part in relative.parts):continue
            if path.suffix.lower() in ('.pyc','.pt','.pth','.npz','.sqlite3','.png','.jpg','.zip'):continue
            if folder=='workbench' and 'datasets' in relative.parts and path.name!='example.json':continue
            files.append(path)
    for path in files:
        target=destination/path.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    print('Prepared',len(files),'source files at',destination)

if __name__=='__main__':main()
