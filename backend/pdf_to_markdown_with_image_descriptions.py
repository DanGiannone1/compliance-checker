"""
pdf_to_markdown_with_image_descriptions.py
------------
1) Convert a PDF to Markdown with base64-embedded images (using pymupdf4llm).
2) For each embedded image, call LLM with the image *and* nearby text for additional context. LLM will return all the key info from the image.
3) Replace the image in the Markdown with the LLM's text
4) Return the final Markdown with images replaced by text descriptions.

Requirements:
  pip install pymupdf4llm azure-identity azure-ai-projects openai python-dotenv
"""

import os
import re
import sys
import argparse

import pymupdf4llm
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from dotenv import load_dotenv

load_dotenv()

# ---- Config from environment ----
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")

# How much text (characters) to take before/after an image for "surrounding context"
CONTEXT_BEFORE_CHARS = int(os.getenv("VISION_CONTEXT_BEFORE_CHARS", "400"))
CONTEXT_AFTER_CHARS  = int(os.getenv("VISION_CONTEXT_AFTER_CHARS",  "400"))
MAX_CONTEXT_CHARS    = int(os.getenv("VISION_MAX_CONTEXT_CHARS",    "1000"))

# Matches: ![alt](data:image/<type>;base64,<B64...>)
IMAGE_DATAURI_PATTERN = re.compile(
    r"!\[[^\]]*\]\((data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+)\)",
    re.DOTALL,
)

def get_openai_client():
    """Create the Azure AI Foundry OpenAI client using your pattern."""
    if not PROJECT_ENDPOINT:
        raise RuntimeError("PROJECT_ENDPOINT env var is required but missing.")
    print(f"🔗 Connecting to Azure AI Foundry endpoint: {PROJECT_ENDPOINT}")
    project_client = AIProjectClient(
        credential=DefaultAzureCredential(),
        endpoint=PROJECT_ENDPOINT,
    )
    return project_client.get_openai_client(api_version=AZURE_OPENAI_API_VERSION)

# ---------- Helpers for context ----------

def strip_images_from_text(text: str) -> str:
    """Remove any base64 image markdown from the context to keep it small."""
    # Replace data-URI images with a short placeholder
    text = IMAGE_DATAURI_PATTERN.sub("[Image]", text)
    # (Optional) collapse any non-data image markdown too (kept simple)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "[ImageRef]", text)
    return text

def collapse_whitespace(text: str) -> str:
    """Make the context compact."""
    return re.sub(r"\s+", " ", text).strip()

def build_surrounding_context(markdown: str, start_idx: int, end_idx: int) -> str:
    """
    Grab a window of text around the image location, clean it, and cap its length.
    """
    before = markdown[max(0, start_idx - CONTEXT_BEFORE_CHARS): start_idx]
    after  = markdown[end_idx: min(len(markdown), end_idx + CONTEXT_AFTER_CHARS)]

    # Remove any inline images inside the window (so we don't leak big base64)
    before = strip_images_from_text(before)
    after  = strip_images_from_text(after)

    # Combine with a small marker so the model knows where the image was
    context = f"{before} [IMAGE LOCATION] {after}"
    context = collapse_whitespace(context)

    # Safety cap
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "…"
    return context

# ---------- PDF -> Markdown ----------

def convert_pdf_to_markdown(pdf_path: str) -> str:
    """Convert PDF to Markdown, embedding images as base64 data URLs (no disk writes)."""
    print(f"📄 Converting PDF to Markdown: {pdf_path}")
    result = pymupdf4llm.to_markdown(
        pdf_path,
        embed_images=True,
        write_images=False,
    )
    print("✅ PDF converted successfully")
    return result

# ---------- Image description ----------

def describe_image(client, data_url: str, surrounding_context: str):
    """Send one data-URL image to the model and get a short description back."""
    # Extract image type for logging
    image_type_match = re.match(r"data:image\/([a-zA-Z0-9.+-]+);", data_url)
    image_type = image_type_match.group(1) if image_type_match else "unknown"

    print(f"  📤 Sending {image_type} image to LLM...")
    print(f"  🧭 Context length sent: {len(surrounding_context)} chars")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a helpful assistant that looks at images and writes clear, detailed descriptions of the content. "
                "You will be provided with some surrounding context as well to better understand the image."
                "Just say what you see without saying 'the image contains' or similar phrases."
                "It is important that you capture all of the information that could be extracted from the image."
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Surrounding context: {surrounding_context}"},
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                
            ],
        },
    ]

    resp = client.chat.completions.create(
        model=MODEL_DEPLOYMENT_NAME,
        messages=messages,
        max_tokens=2000,
        temperature=0,
    )
    description = (resp.choices[0].message.content or "").strip()
    print(f"  📥 LLM response: \"{description}\"")
    return description

# ---------- Replace images with text ----------

def replace_images_with_text(markdown: str, client=None) -> str:
    """
    Find each base64 image, send it with nearby text, and replace it with a text line.

    Replacement format:
      > Image: <description>
    """
    parts = []
    last = 0

    # Collect matches up front so the indexes remain valid while we build `parts`
    all_matches = list(IMAGE_DATAURI_PATTERN.finditer(markdown))
    total_images = len(all_matches)

    if total_images == 0:
        print("ℹ️  No images found in the markdown")
        return markdown

    print(f"🖼️  Found {total_images} image(s) to process")
    print("-" * 60)

    for idx, match in enumerate(all_matches, 1):
        parts.append(markdown[last:match.start()])
        data_url = match.group(1)

        print(f"\n📍 Processing image {idx}/{total_images}")

        # Build surrounding context from the original markdown positions
        context = build_surrounding_context(markdown, match.start(), match.end())

        if client:
            try:
                desc = describe_image(client, data_url, context)
            except Exception as e:
                print(f"  ⚠️  Error describing image: {e}")
                desc = ""
        else:
            print("  ⚠️  No client available, skipping LLM description")
            desc = ""

        if desc:
            replacement = f"> Image: {desc}\n\n"
            print("  ✅ Image replaced with description")
        else:
            replacement = "> Image: [description unavailable]\n\n"
            print("  ⚠️  Using placeholder text (no description available)")

        parts.append(replacement)
        last = match.end()

    parts.append(markdown[last:])
    print("-" * 60)
    print(f"✅ All {total_images} image(s) processed and replaced")

    return "".join(parts)

# ---------- Full pipeline ----------

def pdf_to_markdown_with_image_text(pdf_path: str) -> str:
    """PDF -> Markdown w/ base64 images -> ALWAYS replace images with text (+ context)."""
    print("=" * 60)
    print("🚀 Starting PDF to Markdown conversion pipeline")
    print("=" * 60)

    md = convert_pdf_to_markdown(pdf_path)
    print(f"📏 Markdown size: {len(md):,} characters")

    try:
        client = get_openai_client()
        print(f"✅ OpenAI client initialized (model: {MODEL_DEPLOYMENT_NAME})")
    except Exception as e:
        print(f"[warn] Could not initialize OpenAI client; images will be removed with placeholders. ({e})", file=sys.stderr)
        client = None

    result = replace_images_with_text(md, client)

    print("=" * 60)
    print("✅ Pipeline complete")
    print("=" * 60)

    return result

# ---------- CLI ----------



def main():
    parser = argparse.ArgumentParser(description="PDF to Markdown, replacing images with LLM text (+ surrounding context).")
    parser.add_argument("input_pdf", help="Path to the input PDF")
    parser.add_argument(
        "-o", "--output",
        help="Path for the output .md file (default: <input_stem>.md in the same folder)",
    )
    args = parser.parse_args()

    input_pdf = args.input_pdf
    if not os.path.isfile(input_pdf):
        print(f"Error: file not found: {input_pdf}", file=sys.stderr)
        sys.exit(1)

    # Default output path: same folder as input, with .md
    if args.output:
        output_path = args.output
    else:
        folder = os.path.dirname(os.path.abspath(input_pdf))
        stem = os.path.splitext(os.path.basename(input_pdf))[0]
        output_path = os.path.join(folder, f"{stem}.md")

    print(f"📂 Input PDF: {input_pdf}")
    print(f"📝 Output will be saved to: {output_path}\n")

    result_md = pdf_to_markdown_with_image_text(input_pdf)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result_md)

    print(f"\n✅ Wrote Markdown to: {output_path}")
    print(f"📊 Final file size: {len(result_md):,} characters")

if __name__ == "__main__":
    main()
