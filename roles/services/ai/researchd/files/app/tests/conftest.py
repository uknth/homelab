import sys
from pathlib import Path

# Make the `researchd` package importable without installing it -- tests
# run directly against files/app/researchd, same layout the Dockerfile
# copies into the image.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
