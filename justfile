formatter:
    . .venv/bin/activate; command ruff format

test:
    . .venv/bin/activate && python -m pytest -v --maxfail=1 --disable-warnings