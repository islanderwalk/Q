"""Render ROCLING 2022 paper pages with pdftoppm (200 dpi) and crop the regions the evidence
cards quote. Coordinates are PDF points, found with `pdftotext -bbox-layout`.

Usage: python3 crop_paper.py <rocling2022.pdf> <work_dir>
PDF: https://aclanthology.org/2022.rocling-1.24.pdf
"""
import subprocess
import sys
from pathlib import Path

from PIL import Image

PDF, WORK = Path(sys.argv[1]), Path(sys.argv[2])
pages, crops = WORK / "paper_pages", WORK / "crops"
pages.mkdir(parents=True, exist_ok=True)
crops.mkdir(parents=True, exist_ok=True)
subprocess.run(["pdftoppm", "-f", "2", "-l", "5", "-r", "200", "-png", str(PDF), str(pages / "p")], check=True)

S = 200 / 72
REGIONS = {  # name: (rendered page, x0, y0, x1, y1) in PDF points
    "rocling_p194_hardware_versions": ("p-2.png", 300, 262, 532, 566),
    "rocling_p195_method_40000": ("p-3.png", 64, 329, 294, 404),
    "rocling_p196_fig3_conclusion": ("p-4.png", 300, 305, 532, 757),
    "rocling_p197_table1_f1": ("p-5.png", 300, 72, 532, 209),
    "rocling_p197_jieba_note": ("p-5.png", 64, 362, 294, 414),
}
for name, (page, x0, y0, x1, y1) in REGIONS.items():
    im = Image.open(pages / page)
    im.crop(tuple(round(v * S) for v in (x0, y0, x1, y1))).save(crops / f"{name}.png")
    print(name)
