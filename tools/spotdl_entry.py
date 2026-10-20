"""Entry point do CLI spotDL quando congelado pelo PyInstaller."""
import sys

from spotdl import console_entry_point

if __name__ == '__main__':
    if sys.argv[1:] == ['--build-check']:
        # Exercise the bundled dictionaries without downloads or credentials.
        from pykakasi import kakasi

        result = ''.join(item['hepburn'] for item in kakasi().convert('日本語'))
        if result != 'nihongo':
            raise RuntimeError(f'pykakasi: transliteracao inesperada: {result!r}')
        print('spotDL - pykakasi: dicionarios OK')
    else:
        console_entry_point()
