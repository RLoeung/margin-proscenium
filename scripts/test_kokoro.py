from kokoro import KPipeline
from pathlib import Path
import soundfile as sf

pipeline = KPipeline(lang_code="a")

text = (
    "The Doctor is a TimeLord. She travels in her TARDIS."
    "The fall of the Roman Empire was inevitable."
)

generator = pipeline(
    text,
    voice="bf_isabella",
    speed=1.2,
)

project_root = Path(__file__).resolve().parent.parent
output_dir = project_root / "output"
output_dir.mkdir(exist_ok=True)

for index, (_, _, audio) in enumerate(generator):
    filename = output_dir / f"kokoro_test_{index}.wav"
    sf.write(filename, audio, 24000)
    print(f"Created {filename}")