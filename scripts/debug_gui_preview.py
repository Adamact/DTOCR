"""Create App instance and call _on_preview programmatically using a generated image."""
import sys
import tempfile
from pathlib import Path

# ensure parent of project root is on sys.path
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[2]))

try:
    from PIL import Image, ImageDraw
except Exception:
    print('Pillow missing; install Pillow to test GUI preview')
    raise

from DTOCR.gui.app import App
from DTOCR.services.template_service import TemplateService

# create test image
img = Image.new('RGB', (800, 300), color='white')
draw = ImageDraw.Draw(img)
draw.text((10, 10), 'Hello GUI Preview test', fill='black')
outdir = Path(tempfile.mkdtemp(prefix='dtocr_gui_preview_'))
img_path = outdir / 'test_gui.png'
img.save(img_path)
print('Wrote test image to', img_path)

class DummyRepo:
    def list_templates(self):
        return []
    def list_label_options(self):
        return []

service = TemplateService(template_repo=DummyRepo())
app = App(service)
# configure preview
app.preview_image_path = str(img_path)
app.preview_is_pdf = False
app.word_kernel_divisor_var.set(12)
# call preview
try:
    print('Calling _on_preview()...')
    app._on_preview()
    print('Called _on_preview() successfully')
except Exception as e:
    import traceback
    print('Exception during _on_preview:', e)
    traceback.print_exc()

# keep the app window open for a few seconds so user can see it, then exit
import time

print('Sleeping 5 seconds to allow GUI to paint...')
time.sleep(5)
app.root.destroy()
print('Done.')
