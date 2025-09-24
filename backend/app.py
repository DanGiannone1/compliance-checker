# app.py — FastAPI backend (PDF-only) with:
# - ADLS upload (PDF bytes -> Markdown in-memory)
# - Atomic PDF→Markdown twin generation
# - ONE validation route that:
#     * finds the latest input PDF for the user
#     * finds ALL reference PDFs for the user
#     * validates the input's Markdown against each reference's Markdown
# - User-space bootstrap: creates required folders on first login (idempotent)

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
import os.path as op
import mimetypes
import uuid
from typing import List, Optional, Tuple
from datetime import datetime

from dotenv import load_dotenv

from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.storage.filedatalake import DataLakeServiceClient
from azure.core.exceptions import AzureError

from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

import tiktoken

# In-memory PDF -> Markdown (no temp files)
import fitz  # PyMuPDF
import pymupdf4llm

# Reuse image-description & client helpers
from pdf_to_markdown_with_image_descriptions import (
    replace_images_with_text,
    get_openai_client,  # uses Azure AI Projects + DefaultAzureCredential
)

from prompts import VALIDATION_SYSTEM_PROMPT, get_validation_user_prompt

# ------------------------------------------------------------------------------
# Environment & global setup
# ------------------------------------------------------------------------------

load_dotenv()

app = FastAPI(title="Document Validator API")

# CORS (open for dev; restrict origins in prod)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Azure AI Projects (for tracing + OpenAI client used in validation)
PROJECT_ENDPOINT = os.getenv("PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME")
if not PROJECT_ENDPOINT or not MODEL_DEPLOYMENT_NAME:
    raise ValueError("PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in environment")

project_client = AIProjectClient(credential=DefaultAzureCredential(), endpoint=PROJECT_ENDPOINT)
openai_client = project_client.get_openai_client(api_version="2024-02-01")

# Tracing (best-effort, non-fatal)
try:
    connection_string = project_client.telemetry.get_application_insights_connection_string()
    os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = "true"
    OpenAIInstrumentor().instrument()
    configure_azure_monitor(connection_string=connection_string)
    print("✅ Tracing configured")
except Exception as e:
    print(f"⚠️ Tracing setup failed (continuing without tracing): {e}")

# ADLS config
STORAGE_ACCOUNT_NAME = os.getenv("STORAGE_ACCOUNT_NAME", "djg0storage0shared")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "shared")

# Tokenization constants
MAX_TOKENS = 50000
ENCODING_NAME = "cl100k_base"

# Simple user scoping (replace if/when you add auth)
HARDCODED_USER_ID = "dangiannone"

# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------

class ValidationResult(BaseModel):
    success: bool
    message: str
    raw_output: Optional[str] = None

class UploadResponse(BaseModel):
    success: bool
    message: str
    file_id: str
    file_path: str
    file_size: int
    original_filename: str
    markdown_file_path: Optional[str] = None

# --------------------------------------------------------------------------
# ADLS helpers
# --------------------------------------------------------------------------

def get_adls_client() -> DataLakeServiceClient:
    try:
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.dfs.core.windows.net/"
        credential = DefaultAzureCredential()
        return DataLakeServiceClient(account_url=account_url, credential=credential)
    except Exception as e:
        print(f"Failed to connect to ADLS: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect to storage: {str(e)}")

def ensure_user_folders_exist(service_client: DataLakeServiceClient, user_id: str):
    """
    Ensure container and user folders exist, including Markdown mirrors:
      <user_id>/
      <user_id>/input_docs
      <user_id>/reference_docs
      <user_id>/input_docs_md
      <user_id>/reference_docs_md
    """
    try:
        fs = service_client.get_file_system_client(CONTAINER_NAME)
        if not fs.exists():
            fs.create_file_system()
            print(f"✅ Created container: {CONTAINER_NAME}")

        folders_to_create = [
            f"{user_id}",
            f"{user_id}/input_docs",
            f"{user_id}/reference_docs",
            f"{user_id}/input_docs_md",
            f"{user_id}/reference_docs_md",
        ]
        for folder_path in folders_to_create:
            try:
                dc = fs.get_directory_client(folder_path)
                if not dc.exists():
                    dc.create_directory()
                    print(f"✅ Created directory: {folder_path}")
            except AzureError as e:
                if "PathAlreadyExists" not in str(e):
                    print(f"⚠️ Error creating directory {folder_path}: {e}")
        return fs
    except Exception as e:
        print(f"❌ Error ensuring folders exist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user folders: {str(e)}")

def upload_file_to_adls(file_system_client, file_content: bytes, file_path: str) -> bool:
    try:
        fc = file_system_client.get_file_client(file_path)
        fc.create_file()
        fc.append_data(file_content, offset=0, length=len(file_content))
        fc.flush_data(len(file_content))
        print(f"✅ Uploaded file to: {file_path}")
        return True
    except Exception as e:
        print(f"❌ Error uploading file to {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

def download_file_from_adls(file_system_client, file_path: str) -> bytes:
    try:
        fc = file_system_client.get_file_client(file_path)
        download = fc.download_file()
        return download.readall()
    except Exception as e:
        print(f"❌ Error downloading file from {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")

def list_files_in_directory_detailed(file_system_client, directory_path: str) -> List[dict]:
    try:
        paths = file_system_client.get_paths(path=directory_path, recursive=False)
        files = []
        for p in paths:
            if not p.is_directory:
                fc = file_system_client.get_file_client(p.name)
                try:
                    props = fc.get_file_properties()
                    files.append({
                        "file_path": p.name,
                        "original_filename": os.path.basename(p.name),
                        "file_size": props.size,
                        "upload_date": props.last_modified.isoformat() if props.last_modified else None
                    })
                except Exception as e:
                    print(f"Error getting properties for {p.name}: {e}")
        return files
    except Exception as e:
        print(f"❌ Error listing files in {directory_path}: {e}")
        return []

def delete_file_from_adls(file_system_client, file_path: str) -> bool:
    try:
        fc = file_system_client.get_file_client(file_path)
        fc.delete_file()
        print(f"✅ Deleted file: {file_path}")
        return True
    except Exception as e:
        print(f"❌ Error deleting file {file_path}: {e}")
        return False

def adls_path_exists(file_system_client, file_path: str) -> bool:
    try:
        fc = file_system_client.get_file_client(file_path)
        _ = fc.get_file_properties()
        return True
    except Exception:
        return False

def safe_delete(file_system_client, path: str) -> bool:
    try:
        fc = file_system_client.get_file_client(path)
        fc.delete_file()
        print(f"✅ Deleted: {path}")
        return True
    except Exception as e:
        print(f"ℹ️ Skipped delete (likely missing): {path} ({e})")
        return False

# --------------------------------------------------------------------------
# Markdown twin helpers (PDF-only)
# --------------------------------------------------------------------------

def to_md_folder(raw_path: str) -> str:
    """
    Map raw PDF path to its Markdown twin path:
      <user>/input_docs/foo.pdf -> <user>/input_docs_md/foo.md
      <user>/reference_docs/bar.pdf -> <user>/reference_docs_md/bar.md
    """
    parts = raw_path.split("/")
    if len(parts) < 3:
        raise ValueError(f"Unexpected path shape: {raw_path}")
    folder = parts[-2]
    if folder.endswith("_md"):
        base, _ = op.splitext(parts[-1])
        parts[-1] = base + ".md"
        return "/".join(parts)
    md_folder = folder + "_md"
    base, _ = op.splitext(parts[-1])
    parts[-2] = md_folder
    parts[-1] = base + ".md"
    return "/".join(parts)

def generate_markdown_from_pdf_bytes(file_bytes: bytes) -> str:
    """
    PDF bytes -> Markdown (with base64 images) -> replace images with text using LLM.
    No disk I/O; fully in-memory.
    """
    print("=" * 60)
    print("🚀 Starting PDF(bytes) → Markdown pipeline")
    print("=" * 60)

    # Convert PDF bytes to Markdown using PyMuPDF doc object
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        md = pymupdf4llm.to_markdown(doc, embed_images=True, write_images=False)

    print(f"📏 Markdown size: {len(md):,} characters")

    # Try to init OpenAI client (for image descriptions). Fallback to placeholders on failure.
    try:
        client = get_openai_client()
        print(f"✅ OpenAI client initialized (model: {MODEL_DEPLOYMENT_NAME})")
    except Exception as e:
        print(f"[warn] Could not initialize OpenAI client; images will use placeholders. ({e})")
        client = None

    result = replace_images_with_text(md, client)

    print("=" * 60)
    print("✅ Pipeline complete")
    print("=" * 60)
    return result

# --------------------------------------------------------------------------
# Upload endpoints — transactional: raw PDF + Markdown twin OR fail
# --------------------------------------------------------------------------

def _handle_upload_common_bytes(file_bytes: bytes, original_name: str, target_subdir: str) -> UploadResponse:
    """
    Core upload handler (pure sync): given the PDF bytes + original filename
    - Generate Markdown twin FIRST (in-memory); if it fails, abort
    - Upload BOTH raw PDF and MD twin to ADLS
    """
    # Generate Markdown FIRST to make the operation atomic
    try:
        md_text = generate_markdown_from_pdf_bytes(file_bytes)
    except Exception as e:
        print(f"❌ PDF→Markdown failed for {original_name}: {e}")
        raise HTTPException(status_code=502, detail="Failed to convert PDF to Markdown; upload aborted.")

    # ADLS setup & ensured folders
    service_client = get_adls_client()
    fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)

    # Compute paths
    raw_path = f"{HARDCODED_USER_ID}/{target_subdir}/{original_name}"
    md_path = to_md_folder(raw_path)

    # Upload raw + twin
    upload_file_to_adls(fs, file_bytes, raw_path)
    upload_file_to_adls(fs, md_text.encode("utf-8"), md_path)

    return UploadResponse(
        success=True,
        message=f"Successfully uploaded {original_name} (+ Markdown twin)",
        file_id=str(uuid.uuid4()),
        file_path=raw_path,
        file_size=len(file_bytes),
        original_filename=original_name,
        markdown_file_path=md_path
    )

@app.post("/upload/input", response_model=UploadResponse)
async def upload_input_file(file: UploadFile = File(...)):
    """Upload an input PDF; create its Markdown twin; store both atomically."""
    try:
        # Strict PDF-only guard
        content_type = (file.content_type or mimetypes.guess_type(file.filename)[0] or "").lower()
        _, ext = op.splitext(file.filename.lower())
        if content_type != "application/pdf" and ext != ".pdf":
            raise HTTPException(status_code=400, detail=f"Only PDF files are supported (got: {content_type or ext})")

        file_bytes = await file.read()

        # Ensure user dirs exist before any write (first login resilience)
        service_client = get_adls_client()
        ensure_user_folders_exist(service_client, HARDCODED_USER_ID)

        return _handle_upload_common_bytes(file_bytes, file.filename, "input_docs")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error uploading input file: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post("/upload/reference", response_model=UploadResponse)
async def upload_reference_file(file: UploadFile = File(...)):
    """Upload a reference PDF; create its Markdown twin; store both atomically."""
    try:
        content_type = (file.content_type or mimetypes.guess_type(file.filename)[0] or "").lower()
        _, ext = op.splitext(file.filename.lower())
        if content_type != "application/pdf" and ext != ".pdf":
            raise HTTPException(status_code=400, detail=f"Only PDF files are supported (got: {content_type or ext})")

        file_bytes = await file.read()

        # Ensure user dirs exist before any write (first login resilience)
        service_client = get_adls_client()
        ensure_user_folders_exist(service_client, HARDCODED_USER_ID)

        return _handle_upload_common_bytes(file_bytes, file.filename, "reference_docs")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error uploading reference file: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# --------------------------------------------------------------------------
# List endpoints — frontend shows PDFs only (raw folders)
# --------------------------------------------------------------------------

@app.get("/files/input")
async def list_input_files():
    """List input PDFs for the current user (raw folder only, filtered to .pdf)."""
    try:
        service_client = get_adls_client()
        fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        input_dir = f"{HARDCODED_USER_ID}/input_docs"
        files = [f for f in list_files_in_directory_detailed(fs, input_dir) if f["original_filename"].lower().endswith(".pdf")]
        return {"files": files}
    except Exception as e:
        print(f"❌ Error listing input files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

@app.get("/files/reference")
async def list_reference_files():
    """List reference PDFs for the current user (raw folder only, filtered to .pdf)."""
    try:
        service_client = get_adls_client()
        fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        reference_dir = f"{HARDCODED_USER_ID}/reference_docs"
        files = [f for f in list_files_in_directory_detailed(fs, reference_dir) if f["original_filename"].lower().endswith(".pdf")]
        return {"files": files}
    except Exception as e:
        print(f"❌ Error listing reference files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

@app.get("/files/all")
async def list_all_files():
    """List all raw PDFs (input + reference) for the current user."""
    try:
        service_client = get_adls_client()
        fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        input_dir = f"{HARDCODED_USER_ID}/input_docs"
        reference_dir = f"{HARDCODED_USER_ID}/reference_docs"
        input_files = [f for f in list_files_in_directory_detailed(fs, input_dir) if f["original_filename"].lower().endswith(".pdf")]
        reference_files = [f for f in list_files_in_directory_detailed(fs, reference_dir) if f["original_filename"].lower().endswith(".pdf")]
        return {"input_files": input_files, "reference_files": reference_files}
    except Exception as e:
        print(f"❌ Error listing all files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

# --------------------------------------------------------------------------
# Delete endpoint — removes raw PDF + Markdown twin together
# --------------------------------------------------------------------------

@app.delete("/files/delete")
async def delete_file(file_path: str):
    """
    Delete a raw PDF and its Markdown twin.
    Accepts either the raw path (preferred) or the MD path; both companions are removed.
    """
    try:
        if not file_path.startswith(f"{HARDCODED_USER_ID}/"):
            raise HTTPException(status_code=403, detail="Access denied: Can only delete your own files")

        service_client = get_adls_client()
        fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)

        deleted_any = False
        if "/input_docs_md/" in file_path or "/reference_docs_md/" in file_path:
            deleted_any = safe_delete(fs, file_path) or deleted_any
            parts = file_path.split("/")
            parts[-2] = parts[-2].replace("_md", "")
            stem, _ = op.splitext(parts[-1])
            raw_path = "/".join(parts[:-1] + [stem + ".pdf"])
            deleted_any = safe_delete(fs, raw_path) or deleted_any
        else:
            deleted_any = safe_delete(fs, file_path) or deleted_any
            md_path = to_md_folder(file_path)
            deleted_any = safe_delete(fs, md_path) or deleted_any

        if deleted_any:
            return {"success": True, "message": f"Deleted {os.path.basename(file_path)} and any companions"}
        else:
            raise HTTPException(status_code=404, detail="Nothing deleted (file not found)")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

# --------------------------------------------------------------------------
# Validation — always runs on Markdown twins found in ADLS (no frontend paths)
# --------------------------------------------------------------------------

def count_tokens(text: str, encoding_name: str = ENCODING_NAME) -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))

def chunk_document(document: str, max_chunk_size: int, encoding_name: str = ENCODING_NAME) -> List[str]:
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(document)
    chunks = []
    for i in range(0, len(tokens), max_chunk_size):
        chunk_tokens = tokens[i:i+max_chunk_size]
        chunks.append(encoding.decode(chunk_tokens))
    return chunks

def validate_document_chunk(instructions: str, input_document: str, reference_chunk: str) -> str:
    user_prompt = get_validation_user_prompt(instructions, input_document, reference_chunk)
    messages = [
        {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    try:
        response = openai_client.chat.completions.create(
            model=MODEL_DEPLOYMENT_NAME,
            messages=messages,
            temperature=0,
            max_tokens=1000
        )
        if response.choices:
            return response.choices[0].message.content
        else:
            return "No response from model"
    except Exception as e:
        print(f"Error during LLM call: {e}")
        return f"Error: {str(e)}"

def run_validation(input_markdown: str, reference_markdown: str, instructions: str) -> ValidationResult:
    """
    Validate the input document against ONE reference (chunked internally).
    Returns a ValidationResult with Markdown in raw_output.
    """
    try:
        system_tokens = count_tokens(VALIDATION_SYSTEM_PROMPT)
        instructions_tokens = count_tokens(f"Instructions: {instructions}")
        input_tokens = count_tokens(f"Input Document:\n{input_markdown}")

        user_prompt_template = get_validation_user_prompt("", "", "")
        template_overhead = count_tokens(user_prompt_template)

        total_overhead = system_tokens + instructions_tokens + input_tokens + template_overhead + 50  # buffer
        available_tokens = MAX_TOKENS - total_overhead

        if available_tokens <= 1000:
            return ValidationResult(
                success=False,
                message=f"Input document + overhead ({total_overhead} tokens) is too large. "
                        f"Available tokens for reference: {available_tokens}"
            )
        
        # Split reference document into chunks
        reference_chunks = chunk_document(reference_document, available_tokens)
        print(f"Split reference document into {len(reference_chunks)} chunks")
        
        # Collect all LLM responses and track any errors
        all_responses = []
        errors = []
        
        # Process each reference chunk
        for i, chunk in enumerate(reference_chunks):
            print(f"\nProcessing reference chunk {i+1}/{len(reference_chunks)}...")
            
            # Validate against current chunk
            result = validate_document_chunk(instructions, input_document, chunk)

            normalized_result = result.strip() if isinstance(result, str) else ""

            if not normalized_result:
                error_message = f"No response received for reference section {i+1}."
                errors.append(error_message)
                print(f"⚠️ {error_message}")
                continue

            if normalized_result.startswith("Error:"):
                error_message = f"Reference section {i+1} returned an error from the language model: {normalized_result}"
                errors.append(error_message)
                print(f"⚠️ {error_message}")
                continue

            if normalized_result.lower() == "no response from model":
                error_message = f"No response from model for reference section {i+1}."
                errors.append(error_message)
                print(f"⚠️ {error_message}")
                continue

            all_responses.append(f"## Analysis of Reference Section {i+1}\n\n{result}")

            print(f"Validation complete for chunk {i+1}")

        if errors:
            combined_output = "\n\n---\n\n".join(all_responses) if all_responses else ""
            error_section = "\n".join(f"- {msg}" for msg in errors)

            if combined_output:
                combined_output = f"{combined_output}\n\n---\n\n## Errors\n\n{error_section}"
            else:
                combined_output = f"## Errors\n\n{error_section}"

            return ValidationResult(
                success=False,
                message=f"Validation failed: encountered {len(errors)} error(s) while analyzing the reference document.",
                raw_output=combined_output
            )

        # Combine all responses into a single markdown document
        if all_responses:
            combined_output = "\n\n---\n\n".join(all_responses)
            return ValidationResult(
                success=True,
                message=f"Validation complete. Analyzed {len(reference_chunks)} reference document sections.",
                raw_output="\n\n---\n\n".join(sections)
            )
        else:
            return ValidationResult(
                success=True,
                message=f"Validation complete. Analyzed {len(reference_chunks)} reference document sections with no significant findings.",
                raw_output="No significant findings or issues identified in the validation."
            )
    except Exception as e:
        return ValidationResult(success=False, message=f"Error during validation: {str(e)}")

def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None

def _pick_latest_input(files: List[dict]) -> Optional[dict]:
    pdfs = [f for f in files if f["original_filename"].lower().endswith(".pdf")]
    pdfs.sort(key=lambda x: _iso_to_dt(x.get("upload_date")) or datetime.min)
    return pdfs[-1] if pdfs else None

def _list_all_references(files: List[dict]) -> List[dict]:
    return [f for f in files if f["original_filename"].lower().endswith(".pdf")]

@app.post("/validate", response_model=ValidationResult)
async def validate(instructions: str = Form("")):
    """
    Single validation endpoint (no frontend file paths):
      - Finds the latest INPUT PDF for the user
      - Finds ALL REFERENCE PDFs for the user
      - Uses their Markdown twins (created at upload time)
      - Aggregates results: one top-level section per reference file
    """
    try:
        service_client = get_adls_client()
        fs = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)

        # Discover current corpus
        input_dir = f"{HARDCODED_USER_ID}/input_docs"
        reference_dir = f"{HARDCODED_USER_ID}/reference_docs"

        input_files = list_files_in_directory_detailed(fs, input_dir)
        ref_files = list_files_in_directory_detailed(fs, reference_dir)

        latest_input = _pick_latest_input(input_files)
        if not latest_input:
            raise HTTPException(status_code=404, detail="No input PDFs found. Upload an input document.")

        references = _list_all_references(ref_files)
        if not references:
            raise HTTPException(status_code=404, detail="No reference PDFs found. Upload at least one reference document.")

        # Resolve twins & ensure presence
        input_md_path = to_md_folder(latest_input["file_path"])
        if not adls_path_exists(fs, input_md_path):
            raise HTTPException(
                status_code=409,
                detail={"message": "Input Markdown twin missing. Re-upload the input PDF to regenerate twin.", "missing": [input_md_path]}
            )

        missing_refs = []
        ref_md_paths: List[Tuple[str, str]] = []  # (ref_name, md_path)
        for rf in references:
            mdp = to_md_folder(rf["file_path"])
            if not adls_path_exists(fs, mdp):
                missing_refs.append(mdp)
            else:
                ref_md_paths.append((rf["original_filename"], mdp))

        if missing_refs:
            raise HTTPException(
                status_code=409,
                detail={"message": "One or more reference Markdown twins are missing. Re-upload to regenerate.", "missing": missing_refs}
            )

        # Download twins
        input_md = download_file_from_adls(fs, input_md_path).decode("utf-8", errors="replace")

        # Validate against each reference, aggregate results
        per_reference_sections = []
        total_sections = 0

        for ref_name, ref_md_path in ref_md_paths:
            ref_md = download_file_from_adls(fs, ref_md_path).decode("utf-8", errors="replace")
            result = run_validation(input_md, ref_md, instructions)
            if not result.success:
                # Bubble up a failure for visibility but keep partial results
                per_reference_sections.append(
                    f"## Analysis of input document against **{ref_name}**\n\n"
                    f"> Validation error: {result.message}"
                )
                continue

            total_sections += result.raw_output.count("### Analysis of Reference Section")
            per_reference_sections.append(
                f"## Analysis of input document against **{ref_name}**\n\n{result.raw_output or ''}"
            )

        if not per_reference_sections:
            return ValidationResult(
                success=True,
                message="Validation completed with no results to display.",
                raw_output="No findings produced."
            )

        combined_md = "\n\n---\n\n".join(per_reference_sections)
        summary_msg = (
            f"Validation complete. Input: {latest_input['original_filename']} | "
            f"References analyzed: {len(ref_md_paths)} | Sections: {total_sections}"
        )
        return ValidationResult(success=True, message=summary_msg, raw_output=combined_md)

    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in /validate: {e}")
        return ValidationResult(success=False, message=f"Error during validation: {str(e)}")

# --------------------------------------------------------------------------
# Login bootstrap — call once when user signs in
# --------------------------------------------------------------------------

@app.post("/init-user")
async def init_user():
    """Bootstrap the current user's ADLS namespace. Idempotent."""
    try:
        service_client = get_adls_client()
        ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        return {"success": True, "message": "User folders ensured."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error bootstrapping user folders: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to initialize user space: {str(e)}")

# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Simple health check (does not hit ADLS)."""
    return {"status": "healthy", "adls_connected": True}

# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
