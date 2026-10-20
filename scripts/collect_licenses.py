"""Copia arquivos de licença/NOTICE declarados nos pacotes distribuídos."""
from importlib import metadata
from pathlib import Path
import shutil


def main():
    target = Path('dist/MusicLibraryManager/LICENCAS')
    target.mkdir(parents=True, exist_ok=True)
    recorded = []
    for dist in metadata.distributions():
        name = (dist.metadata.get('Name') or 'unknown').replace('/', '_')
        for f in dist.files or []:
            filename = Path(str(f)).name.lower()
            if not filename.startswith(('license', 'licence', 'copying', 'notice')):
                continue
            try:
                file = Path(dist.locate_file(f))
                if not file.is_file() or file.stat().st_size > 2_000_000:
                    continue
                dest = target / name / Path(str(f)).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file, dest)
                recorded.append(f'{name}: {f}')
            except (OSError, ValueError):
                continue
    (target / 'INVENTARIO.txt').write_text('\n'.join(sorted(recorded)), encoding='utf-8')
    print(f'{len(recorded)} arquivos de licença copiados')


if __name__ == '__main__':
    main()
