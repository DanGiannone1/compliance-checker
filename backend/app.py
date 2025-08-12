# app.py - FastAPI backend using Azure AI Projects SDK
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
import tiktoken
from typing import List
from dotenv import load_dotenv
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor

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

# Set up Azure AI Project client using environment-based values
endpoint = os.getenv("PROJECT_ENDPOINT")
model_deployment_name = os.getenv("MODEL_DEPLOYMENT_NAME")

if not endpoint or not model_deployment_name:
    raise ValueError("PROJECT_ENDPOINT and MODEL_DEPLOYMENT_NAME must be set in environment")

# Initialize Azure AI Project client
project_client = AIProjectClient(
    credential=DefaultAzureCredential(),
    endpoint=endpoint,
)

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

# Input models
class ValidationInput(BaseModel):
    input_document: str
    reference_document: str
    instructions: str

class ValidationResult(BaseModel):
    findings: List[str]
    success: bool
    message: str

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
    system_prompt = """You are an analyst. Your task is to examine an input document and validate it against a reference document. An example might be that you are given a statement of work, and you need to validate it against a corporate policy document to ensure it adheres to all guidelines. Or perhaps you are given a design document and need to validate it against a security policy."""
    
    user_prompt = f"""
#######BEGIN USER INSTRUCTIONS/GUIDANCE########### 
{instructions}

#######END USER INSTRUCTIONS/GUIDANCE###########



#######BEGIN INPUT DOCUMENT##########
{input_document}

########END INPUT DOCUMENT##########


#######BEGIN REFERENCE DOCUMENT CONTENT##########
{reference_chunk}

########END REFERENCE DOCUMENT CONTENT##########

Please analyze the input document against this reference document section and provide your findings.
"""
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]
    
    try:
        response = openai_client.chat.completions.create(
            model=model_deployment_name,
            messages=messages,
            temperature=0,
            max_tokens=5000
        )
        
        if response.choices:
            return response.choices[0].message.content
        else:
            return "No response from model"
    except Exception as e:
        print(f"Error during LLM call: {e}")
        return f"Error: {str(e)}"

@app.post("/validate", response_model=ValidationResult)
async def validate_documents(input_data: ValidationInput):
    """Validate input document against reference document"""
    try:
        instructions = input_data.instructions
        input_document = input_data.input_document
        reference_document = input_data.reference_document
        
        # Count tokens for each component
        system_prompt = """You are an analyst. Your task is to examine an input document and validate it against a reference document. An example might be that you are given a statement of work, and you need to validate it against a corporate policy document to ensure it adheres to all guidelines. Or perhaps you are given a design document and need to validate it against a security policy."""
        
        system_tokens = count_tokens(system_prompt)
        instructions_tokens = count_tokens(f"Instructions: {instructions}")
        input_tokens = count_tokens(f"Input Document:\n{input_document}")
        
        # Calculate overhead for user prompt template
        user_prompt_template = """
Instructions: {instructions}

Input Document:
{input_document}

Reference Document Section:
{reference_chunk}

Please analyze the input document against this reference document section and provide your findings.
"""
        template_overhead = count_tokens(user_prompt_template.format(
            instructions="",
            input_document="", 
            reference_chunk=""
        ))
        
        # Calculate total overhead (system + instructions + input + template)
        total_overhead = system_tokens + instructions_tokens + input_tokens + template_overhead + 50  # 50 token buffer
        
        print(f"System tokens: {system_tokens}")
        print(f"Instructions tokens: {instructions_tokens}")
        print(f"Input document tokens: {input_tokens}")
        print(f"Template overhead tokens: {template_overhead}")
        print(f"Total overhead: {total_overhead}")
        
        # Calculate available tokens for reference document chunks
        available_tokens = MAX_TOKENS - total_overhead
        print(f"Available tokens for reference content: {available_tokens}")
        
        if available_tokens <= 1000:  # Need at least some tokens for reference content
            return ValidationResult(
                findings=[],
                success=False,
                message=f"Input document + overhead ({total_overhead} tokens) is too large. Available tokens for reference: {available_tokens}"
            )
        
        # Split reference document into chunks
        reference_chunks = chunk_document(reference_document, available_tokens)
        print(f"Split reference document into {len(reference_chunks)} chunks")
        
        # Initialize findings tracker
        all_findings = []
        
        # Process each reference chunk
        for i, chunk in enumerate(reference_chunks):
            print(f"\nProcessing reference chunk {i+1}/{len(reference_chunks)}...")
            
            # Validate against current chunk
            result = validate_document_chunk(instructions, input_document, chunk)
            
            # Record findings (assuming any non-empty meaningful response contains findings)
            if result and result.strip() and "no findings" not in result.lower():
                all_findings.append(f"Chunk {i+1}: {result}")
            
            print(f"Validation complete for chunk {i+1}")
        
        # Return results
        return ValidationResult(
            findings=all_findings,
            success=True,
            message=f"Validation complete. Processed {len(reference_chunks)} reference document sections."
        )
        
    except Exception as e:
        return ValidationResult(
            findings=[],
            success=False,
            message=f"Error during validation: {str(e)}"
        )

@app.post("/validate-files", response_model=ValidationResult)
async def validate_files(
    input_file: UploadFile = File(...),
    reference_file: UploadFile = File(...),
    instructions: str = Form(...)
):
    """Validate input file against reference file with custom instructions"""
    try:
        # Read input file
        input_content = await input_file.read()
        input_text = input_content.decode("utf-8")
        
        # Read reference file
        reference_content = await reference_file.read()
        reference_text = reference_content.decode("utf-8")
        
        # Create validation input
        validation_input = ValidationInput(
            input_document=input_text,
            reference_document=reference_text,
            instructions=instructions
        )
        
        # Call the validate function
        return await validate_documents(validation_input)
        
    except Exception as e:
        return ValidationResult(
            findings=[],
            success=False,
            message=f"Error processing files: {str(e)}"
        )

def load_local_file(file_path):
    """Load a local file and return its content"""
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load file {file_path}: {str(e)}")

@app.post("/test-validation", response_model=ValidationResult)
async def test_validation():
    """Test validation using local sample files"""
    try:
        # Define file paths - sample_data is at the same level as backend folder
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Go up one level from backend/
        sample_data_dir = os.path.join(base_dir, "sample_data")
        
        sow_path = os.path.join(sample_data_dir, "sow.txt")
        guidelines_path = os.path.join(sample_data_dir, "compliance_guidelines.txt")
        instructions_path = os.path.join(sample_data_dir, "instructions.txt")
        
        # Load the local files
        input_document = load_local_file(sow_path)
        reference_document = load_local_file(guidelines_path)
        instructions = load_local_file(instructions_path)
        
        print(f"✅ Loaded test files:")
        print(f"  - Input document: {len(input_document)} characters")
        print(f"  - Reference document: {len(reference_document)} characters") 
        print(f"  - Instructions: {len(instructions)} characters")
        
        # Create validation input
        validation_input = ValidationInput(
            input_document=input_document,
            reference_document=reference_document,
            instructions=instructions
        )
        
        # Call the validate function
        return await validate_documents(validation_input)
        
    except Exception as e:
        return ValidationResult(
            findings=[],
            success=False,
            message=f"Error in test validation: {str(e)}"
        )

@app.get("/test-files-info")
async def test_files_info():
    """Get information about the test files without running validation"""
    try:
        # sample_data is at the same level as backend folder
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Go up one level from backend/
        sample_data_dir = os.path.join(base_dir, "sample_data")
        
        files_info = {}
        
        for filename in ["sow.txt", "compliance_guidelines.txt", "instructions.txt"]:
            file_path = os.path.join(sample_data_dir, filename)
            if os.path.exists(file_path):
                content = load_local_file(file_path)
                files_info[filename] = {
                    "exists": True,
                    "character_count": len(content),
                    "token_count": count_tokens(content),
                    "preview": content[:200] + "..." if len(content) > 200 else content
                }
            else:
                files_info[filename] = {
                    "exists": False,
                    "error": f"File not found: {file_path}"
                }
        
        return {
            "sample_data_directory": sample_data_dir,
            "files": files_info
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting test files info: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)