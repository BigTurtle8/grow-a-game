from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(
    "/Users/brandonnguyen/.cursor/projects/Users-brandonnguyen-grow-a-game/"
    "assets/Designer__6_-b0edf790-dc17-4379-80f5-1bafaba48126.png"
)
TARGET = ROOT / "web" / "brand-logo.png"

image = Image.open(SOURCE).convert("RGBA")
pixels = image.load()
for y in range(image.height):
    for x in range(image.width):
        red, green, blue, alpha = pixels[x, y]
        luminance = max(red, green, blue)
        if luminance < 24:
            alpha = round(alpha * luminance / 24)
        pixels[x, y] = (red, green, blue, alpha)

image.save(TARGET, optimize=True)
print(TARGET)
