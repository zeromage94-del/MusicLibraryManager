# conftest.py — configura o pytest para descobrir apenas arquivos test_*.py
# Evita que o pytest tente importar app.py, build_windows.py etc. como testes.
collect_ignore = ["app.py", "spotdl_entry.py", "tiddl_entry.py", "collect_licenses.py"]
collect_ignore_glob = ["tools/*.py", "scripts/*.py", "packaging/*.py"]
