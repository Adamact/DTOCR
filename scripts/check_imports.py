from pathlib import Path
import sys
p = Path(__file__).resolve().parents[1]
# we must add the parent of project root to allow importing the 'DTOCR' package directory
sys.path.insert(0, str(p.parent))
print('Inserted', str(p.parent))
tried = []
try:
    import DTOCR as pkg
    print('Imported DTOCR OK, __file__ =', pkg.__file__)
except Exception as e:
    tried.append(('DTOCR', str(e)))
    try:
        import dtocr as pkg
        print('Imported dtocr OK, __file__ =', pkg.__file__)
    except Exception as e2:
        tried.append(('dtocr', str(e2)))
        print('Import attempts failed:', tried)
        raise
