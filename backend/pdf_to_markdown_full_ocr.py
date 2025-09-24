
"""
pdf_to_markdown_full_ocr.py
-----------------------------------
Approach: render each PDF page -> capped-size image -> LLM (Azure OpenAI via Azure AI Projects)
The LLM must:
  - Transcribe page text verbatim (preserve order as best as possible)
  - Insert IMAGE: … lines for figures/charts/diagrams/logos/photos with key info
  - Output ONLY Markdown (no meta text)

ENV (required):
  - PROJECT_ENDPOINT
  - MODEL_DEPLOYMENT_NAME
  - AZURE_OPENAI_API_VERSION

Usage:
  python pdf_to_markdown_full_page_vision.py input.pdf -o output.md [--start 1 --end 3 --no-page-headings]
"""

import os
import sys
import io
import re
import base64
import argparse
from typing import Optional, Tuple, List

import fitz  # PyMuPDF
from PIL import Image
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient

# ============== Hardcoded settings (keep simple) ==============
RENDER_DPI: int = 280                 # 260–320 is a good balance for dense text
MAX_IMAGE_BYTES: int = 20 * 1024**2   # hard cap per page payload (~20MB)
JPEG_QUALITY_START: int = 85          # descending steps will be tried if needed
MIN_JPEG_QUALITY: int = 35            # lowest JPEG quality before we downscale
DOWNSCALE_FLOOR_PX: int = 720         # don't shrink below this edge size
ADD_PAGE_HEADINGS: bool = True        # add "## Page N" headers
MAX_TOKENS_PER_PAGE: int = 3500       # realistic for most Azure deployments
TEMPERATURE: float = 0.0              # deterministic
# ===============================================================

load_dotenv()

# ---- Config from environment (only these 3) ----
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

# ---------------- Azure OpenAI client ----------------

def get_openai_client():
    if not PROJECT_ENDPOINT:
        raise RuntimeError("PROJECT_ENDPOINT env var is required but missing.")
    project_client = AIProjectClient(
        credential=DefaultAzureCredential(),
        endpoint=PROJECT_ENDPOINT,
    )
    return project_client.get_openai_client(api_version=AZURE_OPENAI_API_VERSION)

# ---------------- Image helpers ----------------

def _ensure_rgb(img: Image.Image) -> Image.Image:
    """Convert alpha/P modes to RGB with white background for JPEG."""
    if img.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img)
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img

def image_bytes_with_size_cap(
    pil_img: Image.Image,
    max_bytes: int = MAX_IMAGE_BYTES,
    q_start: int = JPEG_QUALITY_START,
    q_min: int = MIN_JPEG_QUALITY,
    downscale_floor_px: int = DOWNSCALE_FLOOR_PX,
) -> Tuple[bytes, str]:
    """
    Return (bytes, mime) for the page image, enforcing a max size.
    Strategy:
      1) Try PNG
      2) If too big, try JPEG at descending qualities
      3) If still big, downscale proportionally and save as JPEG
    """
    # 1) PNG first
    png_buf = io.BytesIO()
    pil_img.save(png_buf, format="PNG")
    png_data = png_buf.getvalue()
    if len(png_data) <= max_bytes:
        return png_data, "image/png"

    # 2) JPEG qualities
    jpeg_img = _ensure_rgb(pil_img)
    for q in range(q_start, q_min - 1, -10):  # e.g., 85, 75, 65, 55, 45, 35
        buf = io.BytesIO()
        jpeg_img.save(buf, format="JPEG", quality=q, optimize=True)
        data = buf.getvalue()
        if len(data) <= max_bytes:
            return data, "image/jpeg"

    # 3) Downscale geometrically until under cap (single pass heuristic)
    # Compute factor by area ratio; clamp to floor
    last_len = len(data)  # from the last JPEG encode
    scale = max((max_bytes / last_len) ** 0.5, 0.2)  # avoid absurdly tiny
    new_w = max(downscale_floor_px, int(jpeg_img.width * scale))
    new_h = max(downscale_floor_px, int(jpeg_img.height * scale))
    resized = jpeg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resized.save(buf, format="JPEG", quality=max(70, q_min), optimize=True)
    data = buf.getvalue()
    # If still over, we accept it (rare with 20MB cap and 280 DPI),
    # but you could loop here if your deployment needs strict enforcement.
    return data, "image/jpeg"

def page_to_capped_data_url(page: fitz.Page, dpi: int = RENDER_DPI) -> Tuple[str, Tuple[int, int]]:
    """Render a page via PyMuPDF and return a size-capped data URL + (w,h) pixels."""
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pil_img = Image.open(io.BytesIO(pix.tobytes("png")))
    # enforce size cap
    blob, mime = image_bytes_with_size_cap(pil_img)
    b64 = base64.b64encode(blob).decode("ascii")
    return f"data:{mime};base64,{b64}", (pil_img.width, pil_img.height)

# ---------------- LLM prompting ----------------

SYSTEM_PROMPT = (
    "You are a meticulous OCR + visual-describer for document pages. "
    "Produce ONLY valid Markdown for EACH page with these rules:\n"
    "1) Transcribe all legible text VERBATIM in reading order (best effort).\n"
    "2) Reconstruct structure: use #/##/### for headings when clear; bullet/numbered lists; "
    "   Markdown tables for obvious tables; blockquotes if present; bold/italic when shown.\n"
    "3) For non-text visuals (charts/graphs/figures/logos/diagrams/photos/screenshots/equations), "
    "   add a standalone line starting with: 'IMAGE: ' followed by a concise but complete description "
    "   capturing labels, axes, numbers, legends, relationships, or main visual details.\n"
    "4) Do NOT add meta commentary. Do NOT say 'the image shows' except on IMAGE: lines. "
    "5) If text is partially unreadable, include best guess and mark unclear parts with [...]."
)

USER_INSTRUCTION = (
    "Convert this page to Markdown. Extract all text verbatim and insert IMAGE: … lines for any visuals. "
    "Output ONLY the Markdown for this single page."
)

def normalize_image_prefixes(md: str) -> str:
    """Normalize any 'image:' variants to 'IMAGE:' for downstream consistency."""
    lines = []
    for line in (md or "").splitlines():
        if re.match(r'^\s*(image|Image|IMAGE)\s*:', line):
            rest = re.sub(r'^\s*(?:image|Image|IMAGE)\s*:\s*', '', line)
            lines.append(f"IMAGE: {rest}")
        else:
            lines.append(line)
    return "\n".join(lines).strip()

def extract_page_markdown(client, data_url: str, page_num: int) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"{USER_INSTRUCTION} (Page {page_num})"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
            ],
        },
    ]
    resp = client.chat.completions.create(
        model=MODEL_DEPLOYMENT_NAME,
        messages=messages,
        max_tokens=MAX_TOKENS_PER_PAGE,
        temperature=TEMPERATURE,
    )
    content = (resp.choices[0].message.content or "").strip()
    return normalize_image_prefixes(content)

# ---------------- Orchestration ----------------

def pdf_to_markdown_full_vision(pdf_path: str, start: Optional[int], end: Optional[int], add_page_headings: bool) -> str:
    print("==============================================================")
    print("🚀 Starting PDF → Markdown (full-page vision) pipeline")
    print("==============================================================")
    print(f"📂 Input: {pdf_path}")
    print(f"🖨️  DPI: {RENDER_DPI} | 🧱 Cap: {MAX_IMAGE_BYTES/1024/1024:.1f} MB | 🔤 Tokens/page: {MAX_TOKENS_PER_PAGE}")
    print("--------------------------------------------------------------")

    client = get_openai_client()
    print(f"✅ OpenAI client ready (model: {MODEL_DEPLOYMENT_NAME})")

    doc = fitz.open(pdf_path)
    total = doc.page_count
    s = 1 if start is None else max(1, start)
    e = total if end is None else min(end, total)
    if s > e:
        doc.close()
        raise ValueError(f"Invalid page range: start={s} end={e} (total={total})")

    print(f"📄 Pages: {s}–{e} of {total}")
    print("--------------------------------------------------------------")

    sections: List[str] = []
    for i in range(s - 1, e):
        page_num = i + 1
        try:
            print(f"🖼️  Rendering page {page_num}…")
            data_url, (w, h) = page_to_capped_data_url(doc.load_page(i), dpi=RENDER_DPI)
            approx_mb = len(data_url) / 1024 / 1024
            print(f"   -> image ~{approx_mb:.2f} MB | {w}x{h}px")
            print(f"🤖 LLM extracting page {page_num}…")
            md = extract_page_markdown(client, data_url, page_num)
            if add_page_headings:
                md = f"## Page {page_num}\n\n{md}".strip()
            sections.append(md)
            print(f"✅ Page {page_num} done.")
        except Exception as e:
            print(f"⚠️  Error on page {page_num}: {e}", file=sys.stderr)
            placeholder = f"## Page {page_num}\n\n_Error extracting this page. Try lowering DPI or tokens and re-run._"
            sections.append(placeholder)

    doc.close()
    print("--------------------------------------------------------------")
    print("🧩 Reassembling final Markdown…")
    final_md = "\n\n".join(sections).strip()
    # mild cleanup of extra blank lines
    while "\n\n\n" in final_md:
        final_md = final_md.replace("\n\n\n", "\n\n")
    print(f"📏 Final length: {len(final_md):,} chars")
    print("==============================================================")
    print("✅ Pipeline complete")
    print("==============================================================")
    return final_md

# ---------------- CLI ----------------

def main():
    parser = argparse.ArgumentParser(description="PDF → Markdown via full-page vision (Azure OpenAI).")
    parser.add_argument("input_pdf", help="Path to the input PDF")
    parser.add_argument("-o", "--output", help="Output .md file (default: <input>_vision.md)")
    parser.add_argument("--start", type=int, default=None, help="Start page (1-based, inclusive)")
    parser.add_argument("--end", type=int, default=None, help="End page (1-based, inclusive)")
    parser.add_argument("--no-page-headings", action="store_true", help="Do not add '## Page N' headings")
    args = parser.parse_args()

    input_pdf = args.input_pdf
    if not os.path.isfile(input_pdf):
        print(f"❌ Error: file not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or os.path.splitext(os.path.abspath(input_pdf))[0] + "_vision.md"
    print(f"📝 Output: {output_path}")

    try:
        final_md = pdf_to_markdown_full_vision(
            pdf_path=input_pdf,
            start=args.start,
            end=args.end,
            add_page_headings=not args.no_page_headings,
        )
    except Exception as e:
        print(f"❌ Fatal error: {e}", file=sys.stderr)
        sys.exit(2)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_md)

    print(f"\n✅ Wrote Markdown to: {output_path}")

if __name__ == "__main__":
    main()
