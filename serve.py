# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "vgi-python[http,oauth]>=0.55.1,<0.56",
#     "httpx>=0.27",
#     "rdflib>=7.1,<8",
# ]
# ///
"""HTTP entrypoint for the Wikipedia worker.

Forces the worker's CLI into HTTP mode (``Worker.main()`` serves stdio by
default) so callers only pass ``--host``/``--port``.
"""

import sys

from wikipedia_worker import WikipediaWorker

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--http" not in argv:
        argv = ["--http", *argv]
    sys.argv = [sys.argv[0], *argv]
    WikipediaWorker.main()
