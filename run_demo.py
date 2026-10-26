"""One-command demo: generate data, start the mock lab API, run the pipeline, score it.

    py run_demo.py
"""
from oilmon import config, evaluate, generate_data, pipeline
from oilmon.mock_api import MockLabServer

if __name__ == "__main__":
    generate_data.generate()
    server = MockLabServer(flaky=True)  # flaky: random 503s exercise the retry logic
    server.start_background()
    try:
        cleaned, analysed = pipeline.run(full=True)
        print("Second pull (should find nothing new):")
        pipeline.ingest.pull()
    finally:
        server.shutdown()
    print()
    print(evaluate.evaluate(cleaned, analysed))
    print(f"Evaluation -> {config.OUTPUT_DIR / 'evaluation.md'}")
