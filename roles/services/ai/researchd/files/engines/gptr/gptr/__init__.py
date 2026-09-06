"""gptr -- a FastAPI sidecar wrapping the `gpt-researcher` library behind the
fixed three-endpoint contract researchd's engines/remote.py speaks (see
../../../app/researchd/engines/remote.py for the client side of this same
contract, and docs/spec/services.md for why a sidecar exists at all: keeping
gpt-researcher's own dependency tree -- and its very different trust model,
it fetches whatever URLs a search engine hands it -- out of researchd's own
image).
"""

__version__ = "1.0.0"
