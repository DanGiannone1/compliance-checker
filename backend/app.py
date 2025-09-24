# app.py - Simplified FastAPI backend with ADLS upload functionality
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
import tiktoken
from typing import List, Optional
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from azure.storage.filedatalake import DataLakeServiceClient
from azure.core.exceptions import AzureError
import uuid
from datetime import datetime
import mimetypes
from prompts import VALIDATION_SYSTEM_PROMPT, get_validation_user_prompt

load_dotenv()

app = FastAPI(title="Document Validator API")

# Add CORS middleware to allow requests from the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Azure AI Project configuration
endpoint = os.getenv("PROJECT_ENDPOINT")
model_deployment_name = os.getenv("MODEL_DEPLOYMENT_NAME")

# Azure Data Lake Storage configuration
STORAGE_ACCOUNT_NAME = os.getenv("STORAGE_ACCOUNT_NAME", "djg0storage0shared")
CONTAINER_NAME = os.getenv("CONTAINER_NAME", "shared")

if not endpoint or not model_deployment_name:
    raise ValueError("PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in environment")

# Initialize Azure AI Project client
project_client = AIProjectClient(
    credential=DefaultAzureCredential(),
    endpoint=endpoint,
)

# Initialize Azure Data Lake Storage client
def get_adls_client():
    """Get ADLS client using DefaultAzureCredential"""
    try:
        account_url = f"https://{STORAGE_ACCOUNT_NAME}.dfs.core.windows.net/"
        credential = DefaultAzureCredential()
        service_client = DataLakeServiceClient(
            account_url=account_url,
            credential=credential
        )
        return service_client
    except Exception as e:
        print(f"Failed to connect to ADLS: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to connect to storage: {str(e)}")

# Set up tracing (optional - can be disabled by removing these lines)
try:
    connection_string = project_client.telemetry.get_application_insights_connection_string()
    os.environ["AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED"] = "true"
    OpenAIInstrumentor().instrument()
    configure_azure_monitor(connection_string=connection_string)
    print("✅ Tracing configured")
except Exception as e:
    print(f"⚠️ Tracing setup failed (continuing without tracing): {e}")

# Get OpenAI client
openai_client = project_client.get_openai_client(
    api_version="2024-02-01"
)

# Constants
MAX_TOKENS = 50000  # Maximum context window size
ENCODING_NAME = "cl100k_base"  # Encoding for token counting
HARDCODED_USER_ID = "dangiannone"  # Hardcoded user ID for now

# Simplified data models
class ValidationInput(BaseModel):
    input_document: str
    reference_document: str
    instructions: str

class ValidationResult(BaseModel):
    success: bool
    message: str
    raw_output: Optional[str] = None  # Just return the raw LLM output

class UploadResponse(BaseModel):
    success: bool
    message: str
    file_id: str
    file_path: str
    file_size: int
    original_filename: str

class FileInfo(BaseModel):
    file_path: str
    original_filename: str
    file_size: int
    upload_date: str

# ADLS Helper Functions (keeping these the same)
def ensure_user_folders_exist(service_client, user_id: str):
    """Ensure user folders exist in ADLS"""
    try:
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        # Check if container exists, create if it doesn't
        if not file_system_client.exists():
            file_system_client.create_file_system()
            print(f"✅ Created container: {CONTAINER_NAME}")
        
        # Create user directory structure
        folders_to_create = [
            f"{user_id}",
            f"{user_id}/input_docs",
            f"{user_id}/reference_docs"
        ]
        
        for folder_path in folders_to_create:
            try:
                directory_client = file_system_client.get_directory_client(folder_path)
                if not directory_client.exists():
                    directory_client.create_directory()
                    print(f"✅ Created directory: {folder_path}")
            except AzureError as e:
                if "PathAlreadyExists" not in str(e):
                    print(f"⚠️ Error creating directory {folder_path}: {e}")
                    
        return file_system_client
        
    except Exception as e:
        print(f"❌ Error ensuring folders exist: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user folders: {str(e)}")

def upload_file_to_adls(file_system_client, file_content: bytes, file_path: str):
    """Upload file content to ADLS"""
    try:
        file_client = file_system_client.get_file_client(file_path)
        
        # Create file and upload data
        file_client.create_file()
        file_client.append_data(file_content, offset=0, length=len(file_content))
        file_client.flush_data(len(file_content))
        
        print(f"✅ Uploaded file to: {file_path}")
        return True
        
    except Exception as e:
        print(f"❌ Error uploading file to {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload file: {str(e)}")

def download_file_from_adls(file_system_client, file_path: str) -> bytes:
    """Download file content from ADLS"""
    try:
        file_client = file_system_client.get_file_client(file_path)
        download = file_client.download_file()
        return download.readall()
        
    except Exception as e:
        print(f"❌ Error downloading file from {file_path}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")

def list_files_in_directory_detailed(file_system_client, directory_path: str) -> List[dict]:
    """List files in a directory with detailed information"""
    try:
        paths = file_system_client.get_paths(path=directory_path, recursive=False)
        files = []
        for path in paths:
            if not path.is_directory:
                # Get file properties for additional metadata
                file_client = file_system_client.get_file_client(path.name)
                try:
                    properties = file_client.get_file_properties()
                    file_info = {
                        "file_path": path.name,
                        "original_filename": os.path.basename(path.name),
                        "file_size": properties.size,
                        "upload_date": properties.last_modified.isoformat() if properties.last_modified else None
                    }
                    files.append(file_info)
                except Exception as e:
                    print(f"Error getting properties for {path.name}: {e}")
        return files
    except Exception as e:
        print(f"❌ Error listing files in {directory_path}: {e}")
        return []

def delete_file_from_adls(file_system_client, file_path: str) -> bool:
    """Delete a file from ADLS"""
    try:
        file_client = file_system_client.get_file_client(file_path)
        file_client.delete_file()
        print(f"✅ Deleted file: {file_path}")
        return True
    except Exception as e:
        print(f"❌ Error deleting file {file_path}: {e}")
        return False

# File Upload Endpoints (keeping these the same)
@app.post("/upload/input", response_model=UploadResponse)
async def upload_input_file(file: UploadFile = File(...)):
    """Upload an input document to ADLS"""
    try:
        # Validate file type
        allowed_types = ['text/plain', 'application/pdf', 'application/msword', 
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
        
        content_type = file.content_type or mimetypes.guess_type(file.filename)[0]
        if content_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"File type {content_type} not supported")
        
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        # Use original filename (will overwrite if exists)
        file_id = str(uuid.uuid4())  # Keep for tracking purposes
        file_path = f"{HARDCODED_USER_ID}/input_docs/{file.filename}"
        
        # Connect to ADLS and ensure folders exist
        service_client = get_adls_client()
        file_system_client = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        
        # Upload file (will overwrite if exists)
        upload_file_to_adls(file_system_client, file_content, file_path)
        
        return UploadResponse(
            success=True,
            message=f"Successfully uploaded {file.filename}",
            file_id=file_id,
            file_path=file_path,
            file_size=file_size,
            original_filename=file.filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error uploading input file: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.post("/upload/reference", response_model=UploadResponse)
async def upload_reference_file(file: UploadFile = File(...)):
    """Upload a reference document to ADLS"""
    try:
        # Validate file type
        allowed_types = ['text/plain', 'application/pdf', 'application/msword', 
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
        
        content_type = file.content_type or mimetypes.guess_type(file.filename)[0]
        if content_type not in allowed_types:
            raise HTTPException(status_code=400, detail=f"File type {content_type} not supported")
        
        # Read file content
        file_content = await file.read()
        file_size = len(file_content)
        
        # Use original filename (will overwrite if exists)
        file_id = str(uuid.uuid4())  # Keep for tracking purposes
        file_path = f"{HARDCODED_USER_ID}/reference_docs/{file.filename}"
        
        # Connect to ADLS and ensure folders exist
        service_client = get_adls_client()
        file_system_client = ensure_user_folders_exist(service_client, HARDCODED_USER_ID)
        
        # Upload file (will overwrite if exists)
        upload_file_to_adls(file_system_client, file_content, file_path)
        
        return UploadResponse(
            success=True,
            message=f"Successfully uploaded {file.filename}",
            file_id=file_id,
            file_path=file_path,
            file_size=file_size,
            original_filename=file.filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected error uploading reference file: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

@app.get("/files/input")
async def list_input_files():
    """List all input files for the current user"""
    try:
        service_client = get_adls_client()
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        input_dir = f"{HARDCODED_USER_ID}/input_docs"
        files = list_files_in_directory_detailed(file_system_client, input_dir)
        
        return {"files": files}
        
    except Exception as e:
        print(f"❌ Error listing input files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

@app.get("/files/reference")
async def list_reference_files():
    """List all reference files for the current user"""
    try:
        service_client = get_adls_client()
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        reference_dir = f"{HARDCODED_USER_ID}/reference_docs"
        files = list_files_in_directory_detailed(file_system_client, reference_dir)
        
        return {"files": files}
        
    except Exception as e:
        print(f"❌ Error listing reference files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

@app.get("/files/all")
async def list_all_files():
    """List all files for the current user (input and reference)"""
    try:
        service_client = get_adls_client()
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        input_dir = f"{HARDCODED_USER_ID}/input_docs"
        reference_dir = f"{HARDCODED_USER_ID}/reference_docs"
        
        input_files = list_files_in_directory_detailed(file_system_client, input_dir)
        reference_files = list_files_in_directory_detailed(file_system_client, reference_dir)
        
        return {
            "input_files": input_files,
            "reference_files": reference_files
        }
        
    except Exception as e:
        print(f"❌ Error listing all files: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")

@app.delete("/files/delete")
async def delete_file(file_path: str):
    """Delete a file from ADLS"""
    try:
        # Validate that the file path belongs to the current user
        if not file_path.startswith(f"{HARDCODED_USER_ID}/"):
            raise HTTPException(status_code=403, detail="Access denied: Can only delete your own files")
        
        service_client = get_adls_client()
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        success = delete_file_from_adls(file_system_client, file_path)
        
        if success:
            return {"success": True, "message": f"Successfully deleted {os.path.basename(file_path)}"}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete file")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

# Simplified validation functions
def count_tokens(text, encoding_name=ENCODING_NAME):
    """Count the number of tokens in a text string"""
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))

def chunk_document(document, max_chunk_size, encoding_name=ENCODING_NAME):
    """Split document into chunks of approximately max_chunk_size tokens"""
    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(document)
    chunks = []
    
    for i in range(0, len(tokens), max_chunk_size):
        chunk_tokens = tokens[i:i+max_chunk_size]
        chunks.append(encoding.decode(chunk_tokens))
    
    return chunks

def validate_document_chunk(instructions, input_document, reference_chunk):
    """Validate input document against reference chunk using LLM"""
    user_prompt = get_validation_user_prompt(instructions, input_document, reference_chunk)
    
    messages = [
        {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = openai_client.chat.completions.create(
            model=model_deployment_name,
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

@app.post("/validate-uploaded", response_model=ValidationResult)
async def validate_uploaded_files(
    input_file_path: str = Form(...),
    reference_file_path: str = Form(...),
    instructions: str = Form("")
):
    """Validate uploaded files from ADLS - simplified version"""
    try:
        # Connect to ADLS
        service_client = get_adls_client()
        file_system_client = service_client.get_file_system_client(CONTAINER_NAME)
        
        # Download files from ADLS
        input_content = download_file_from_adls(file_system_client, input_file_path)
        reference_content = download_file_from_adls(file_system_client, reference_file_path)
        
        # Convert bytes to string (assuming text files for now)
        input_text = input_content.decode('utf-8')
        reference_text = reference_content.decode('utf-8')
        
        # Create validation input
        validation_input = ValidationInput(
            input_document=input_text,
            reference_document=reference_text,
            instructions=instructions
        )
        
        # Call the simplified validate function
        return await validate_documents(validation_input)
        
    except Exception as e:
        print(f"❌ Error in validate_uploaded_files: {e}")
        return ValidationResult(
            success=False,
            message=f"Error during validation: {str(e)}"
        )

@app.post("/validate", response_model=ValidationResult)
async def validate_documents(input_data: ValidationInput):
    """Simplified validation that returns raw LLM output"""
    try:
        instructions = input_data.instructions
        input_document = input_data.input_document
        reference_document = input_data.reference_document
        
        # Count tokens for each component
        system_tokens = count_tokens(VALIDATION_SYSTEM_PROMPT)
        instructions_tokens = count_tokens(f"Instructions: {instructions}")
        input_tokens = count_tokens(f"Input Document:\n{input_document}")
        
        # Calculate overhead for user prompt template
        user_prompt_template = get_validation_user_prompt("", "", "")
        template_overhead = count_tokens(user_prompt_template)
        
        # Calculate total overhead (system + instructions + input + template)
        total_overhead = system_tokens + instructions_tokens + input_tokens + template_overhead + 50  # 50 token buffer
        
        print(f"Total overhead: {total_overhead}")
        
        # Calculate available tokens for reference document chunks
        available_tokens = MAX_TOKENS - total_overhead
        print(f"Available tokens for reference content: {available_tokens}")
        
        if available_tokens <= 1000:  # Need at least some tokens for reference content
            return ValidationResult(
                success=False,
                message=f"Input document + overhead ({total_overhead} tokens) is too large. Available tokens for reference: {available_tokens}"
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
                raw_output=combined_output
            )
        else:
            return ValidationResult(
                success=True,
                message=f"Validation complete. Analyzed {len(reference_chunks)} reference document sections with no significant findings.",
                raw_output="No significant findings or issues identified in the validation."
            )
        
    except Exception as e:
        return ValidationResult(
            success=False,
            message=f"Error during validation: {str(e)}"
        )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "adls_connected": True}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)