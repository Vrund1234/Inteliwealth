from raw_ingestion import extract_and_push
from silver.silver_loader import load_silver
from gold.loader import load_gold
import sys

class StreamlitMockFile:
    def __init__(self, path, name):
        self.file = open(path, "rb")
        self.name = name
    def seek(self, offset):
        self.file.seek(offset)
    def read(self, *args):
        return self.file.read(*args)

f = StreamlitMockFile("/var/www/html/intelliwealth-layers/data-files/10072026104746_216882305R2.csv", "10072026104746_216882305R2.csv")
try:
    print("Testing Raw Ingestion...")
    extract_and_push([f])
    print("Testing Silver Load...")
    load_silver()
    print("Testing Gold Load...")
    load_gold()
    print("SUCCESS")
except Exception as e:
    import traceback
    traceback.print_exc()
    sys.exit(1)
finally:
    f.file.close()
