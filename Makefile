.PHONY: setup index serve studio test evals check demo viz site clean

setup:
	uv sync

index:
	PYTHONPATH=. uv run python ingest/build_kb.py
	PYTHONPATH=. uv run python ingest/build_catalog.py

serve:
	uv run uvicorn app.main:app --port 8000 --reload

test:
	uv run pytest -q

evals:
	PYTHONPATH=. uv run python evals/run_evals.py

# What CI runs. Green here means the factual layer is intact.
check: test evals

# The three-line version of the whole argument: same sofa, two rooms,
# and a listing with no dimensions that refuses to guess.
demo:
	@echo "\n== 218cm sofa -> living/dining =="
	@uv run python cli.py access unit01 living_dining 218 95 84 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['status'].upper());[print('   ',r) for r in d['reasons']]"
	@echo "\n== same sofa -> bedroom =="
	@uv run python cli.py access unit01 bedroom 218 95 84 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['status'].upper());[print('   ',r) for r in d['reasons'] if 'cannot' in r]"
	@echo "\n== flat-packed armchair: assembled vs carton =="
	@uv run python cli.py access unit01 bedroom 90 90 105 | python3 -c "import json,sys;d=json.load(sys.stdin);print('   assembled:',d['status'])"
	@uv run python cli.py access unit01 bedroom 90 90 105 --carton 95 88 40 | python3 -c "import json,sys;d=json.load(sys.stdin);print('   carton   :',d['status'])"

studio:
	@echo "open http://localhost:8000/studio"
	uv run uvicorn app.main:app --port 8000

site:
	PYTHONPATH=. uv run python tools/export_static.py
	@echo "static build in site/ — serve with: python3 -m http.server -d site 8080"

viz:
	PYTHONPATH=. uv run python viz/render.py

clean:
	rm -rf data/chroma .pytest_cache site
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
